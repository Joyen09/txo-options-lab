"""txolab CLI：fetch（下載+驗證+落地）、monitor（隱波監控→Discord）、chain、notify-test。

VM 上的每日排程（deploy/ 有 systemd timer）：
  txolab fetch && txolab monitor
"""
from __future__ import annotations

import datetime as dt
import tomllib
import zoneinfo
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .data import taifex
from .data.chain import build_chain
from .data.parser import parse_futures_csv, parse_options_csv
from .data.store import Store
from .notify import DiscordNotifier
from .vol.monitor import MonitorConfig, run_monitor
from .vol.surface import atm_iv, build_smile

app = typer.Typer(help="TXO Options Lab — 台指選擇權研究管線", no_args_is_help=True)
console = Console()

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "db" / "txolab.sqlite"
SAMPLES_DIR = ROOT / "data" / "samples"
MONITOR_TOML = ROOT / "config" / "monitor.toml"


def _load_env(path: Path = ROOT / ".env") -> None:
    """極簡 .env 載入（KEY=VALUE，# 註解）——不引額外套件。"""
    import os
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _monitor_config() -> MonitorConfig:
    raw = tomllib.loads(MONITOR_TOML.read_text())
    return MonitorConfig(
        r=raw["pricing"]["r"],
        window=raw["monitor"]["window"],
        rank_high=raw["monitor"]["rank_high"],
        rank_low=raw["monitor"]["rank_low"],
        jump_vol_pts=raw["monitor"]["jump_vol_pts"],
        daily_summary=raw["monitor"]["daily_summary"],
    )


def _parse_date(date: str | None) -> dt.date:
    if date is not None:
        return dt.date.fromisoformat(date)
    return dt.datetime.now(tz=zoneinfo.ZoneInfo("Asia/Taipei")).date()


@app.command()
def fetch(date: str = typer.Option(None, help="交易日 YYYY-MM-DD（預設今天）"),
          days: int = typer.Option(1, help="往回抓幾個日曆日（跳過週末）")):
    """下載 TXO/TX 每日行情 → 嚴格驗證 → 存 data/samples/ 原始檔 → 落地 sqlite。

    第一次跑 = parser 格式驗證（鐵律 6）：驗證不過會整批拒收並列出實際表頭。
    """
    end = _parse_date(date)
    targets = [end - dt.timedelta(days=i) for i in range(days)]
    targets = [d for d in targets if d.weekday() < 5]
    with Store(DB_PATH) as store:
        for d in targets:
            console.print(f"[bold]{d}[/bold] 下載中…")
            opt_text = taifex.download_options_daily(d)
            fut_text = taifex.download_futures_daily(d)
            taifex.save_sample(opt_text, "opt", d, SAMPLES_DIR)
            taifex.save_sample(fut_text, "fut", d, SAMPLES_DIR)
            opt_rows = parse_options_csv(opt_text)   # 驗證不過在這裡爆，原始檔已留樣
            fut_rows = parse_futures_csv(fut_text)
            if not opt_rows:
                console.print(f"  [yellow]{d} 無 TXO 資料（假日？）[/yellow]")
                continue
            n_o = store.upsert_options(opt_rows)
            n_f = store.upsert_futures(fut_rows)
            console.print(f"  TXO {n_o} 列、TX {n_f} 列 → {DB_PATH.name} ✅")


@app.command()
def monitor(date: str = typer.Option(None, help="交易日 YYYY-MM-DD（預設 DB 最新日）"),
            dry_run: bool = typer.Option(False, "--dry-run", help="只印訊息不送 Discord")):
    """隱波監控：smile / ATM IV / rank / 事件 → Discord（見 config/monitor.toml）。"""
    _load_env()
    with Store(DB_PATH) as store:
        if date is None:
            dates = store.trade_dates()
            if not dates:
                console.print("[red]DB 是空的——先跑 txolab fetch[/red]")
                raise typer.Exit(1)
            d = dt.date.fromisoformat(dates[-1])
        else:
            d = dt.date.fromisoformat(date)
        notifier = DiscordNotifier(dry_run=dry_run)
        res = run_monitor(d, store, _monitor_config(), notifier)
        if not dry_run:
            console.print(res.message or "（無訊息）")
        if res.events:
            console.print(f"[bold yellow]{len(res.events)} 個事件[/bold yellow]")


@app.command()
def chain(date: str = typer.Option(None, help="交易日 YYYY-MM-DD（預設 DB 最新日）")):
    """列出某日 option chain 摘要（各到期日 forward / T / ATM IV / 檔數）。"""
    with Store(DB_PATH) as store:
        dates = store.trade_dates()
        if not dates:
            console.print("[red]DB 是空的——先跑 txolab fetch[/red]")
            raise typer.Exit(1)
        d = dt.date.fromisoformat(date) if date else dt.date.fromisoformat(dates[-1])
        ch = build_chain(d, store.options_on(d), store.futures_on(d))
        cfg = _monitor_config()
        table = Table(title=f"TXO chain {d}")
        for col in ("到期代碼", "到期日", "forward", "TX月份", "T(年)", "檔數", "ATM IV"):
            table.add_column(col)
        for sl in ch.slices:
            sm = build_smile(sl, cfg.r)
            a = atm_iv(sm)
            table.add_row(sl.expiry_code, str(sl.expiry), f"{sl.forward:.0f}",
                          sl.forward_code, f"{sl.t_years:.4f}", str(len(sl.rows)),
                          "n/a" if a is None else f"{a * 100:.2f}%")
        console.print(table)


@app.command()
def backfill(start: str = typer.Argument(..., help="起日 YYYY-MM-DD"),
             end: str = typer.Option(None, help="迄日（預設今天）")):
    """回補歷史每日行情（Phase 5 回測需要 >=1 年）。冪等：已入庫的日期自動跳過。

    禮貌性 rate limit 每請求間隔 3 秒，一年約 25 分鐘——建議 nohup 背景跑。
    期交所資料下載專區僅提供近三年，更早的會抓不到。
    """
    from .data.parser import ParserError
    from .data.taifex import DownloadError
    d0 = dt.date.fromisoformat(start)
    d1 = _parse_date(end)
    ok = skip = fail = 0
    with Store(DB_PATH) as store:
        have = set(store.trade_dates())
        d = d0
        while d <= d1:
            if d.weekday() >= 5 or d.isoformat() in have:
                d += dt.timedelta(days=1)
                continue
            try:
                opt_text = taifex.download_options_daily(d)
                fut_text = taifex.download_futures_daily(d)
                opt_rows = parse_options_csv(opt_text)
                fut_rows = parse_futures_csv(fut_text)
                if opt_rows:
                    store.upsert_options(opt_rows)
                    store.upsert_futures(fut_rows)
                    ok += 1
                    console.print(f"{d} ✅ TXO {len(opt_rows)} 列")
                else:
                    skip += 1
            except ParserError as e:
                if "內容不足" in str(e):
                    skip += 1  # 假日/非交易日
                else:
                    fail += 1
                    console.print(f"[red]{d} parser 失敗: {e}[/red]")
            except DownloadError as e:
                fail += 1
                console.print(f"[yellow]{d} 下載失敗: {e}[/yellow]")
            d += dt.timedelta(days=1)
    console.print(f"[bold]回補完成：{ok} 天入庫、{skip} 天跳過（假日/已有）、{fail} 天失敗[/bold]")


def _engine_config(slippage_mult: int = 1):
    """組 EngineConfig：吃 costs.toml / margin.toml / backtest.toml / monitor.toml。

    margin.toml 未查證（verified_date 空）→ 拒絕跑回測（config 檔頭的約定）。
    """
    from .backtest.criteria import INITIAL_EQUITY
    from .backtest.engine import EngineConfig
    costs = tomllib.loads((ROOT / "config" / "costs.toml").read_text())
    margin = tomllib.loads((ROOT / "config" / "margin.toml").read_text())
    bt = tomllib.loads((ROOT / "config" / "backtest.toml").read_text())
    if not margin["txo"]["verified_date"]:
        console.print("[red]config/margin.toml 的 A/B/C 未查證（verified_date 空）"
                      "——依約定拒絕跑回測[/red]")
        raise typer.Exit(1)
    from decimal import Decimal
    return EngineConfig(
        initial_equity=INITIAL_EQUITY,
        slippage_ticks=costs["slippage"]["ticks"] * slippage_mult,
        tax_rate=Decimal(str(costs["tax"]["options_rate"])),
        fee_per_lot=Decimal(str(costs["fee"]["per_lot_twd"])),
        util_cap=costs["risk"]["max_margin_utilization"],
        max_lots=bt["sizing"]["max_lots"],
        min_entry_dte=bt["entry"]["min_days_to_expiry"],
        force_close_dte=bt["exit"]["force_close_days_to_expiry"],
        r=_monitor_config().r,
        margin_a=Decimal(margin["txo"]["a_value"]["initial"]),
        margin_b=Decimal(margin["txo"]["b_value"]["initial"]),
        margin_c=Decimal(margin["txo"]["c_value"]["initial"]),
    ), bt


def _strategies(bt: dict):
    from .backtest.strategies import IronCondor, ShortStrangle, VerticalSpread
    v, c, s = bt["vertical_spread"], bt["iron_condor"], bt["short_strangle"]
    return [
        VerticalSpread(v["short_delta"], v["wing_points"], v["stop_credit_mult"]),
        IronCondor(c["short_delta"], c["wing_points"], c["stop_credit_mult"]),
        ShortStrangle(s["short_delta"], s["stop_credit_mult"], s["stop_abs_delta"]),
    ]


@app.command()
def backtest(strategy: str = typer.Option("all", help="all / vertical_spread / iron_condor / short_strangle"),
             start: str = typer.Option(None, help="起日 YYYY-MM-DD"),
             end: str = typer.Option(None, help="迄日 YYYY-MM-DD")):
    """跑三個基準策略回測 + 滑價×2 敏感度，輸出 criteria PASS/FAIL 報告。

    trade log 存 data/db/backtest_<策略>.csv；同一輸入重跑結果 bit-identical。
    """
    import csv as _csv

    from .backtest.criteria import SLIPPAGE_SENSITIVITY_MULT, evaluate
    from .backtest.engine import run_backtest
    cfg1, bt = _engine_config(1)
    cfg2, _ = _engine_config(SLIPPAGE_SENSITIVITY_MULT)
    d0 = dt.date.fromisoformat(start) if start else None
    d1 = dt.date.fromisoformat(end) if end else None
    strategies = [s for s in _strategies(bt)
                  if strategy in ("all", s.name)]
    if not strategies:
        console.print(f"[red]未知策略: {strategy}[/red]")
        raise typer.Exit(1)

    with Store(DB_PATH) as store:
        n_days = len(store.trade_dates())
        console.print(f"DB 內共 {n_days} 個交易日")
        for strat in strategies:
            console.print(f"[dim]{strat.name}: 回測中（正常滑價）…[/dim]")
            res = run_backtest(store, strat, cfg1, d0, d1)
            console.print(f"[dim]{strat.name}: 滑價×2 敏感度…[/dim]")
            res2x = run_backtest(store, strat, cfg2, d0, d1)
            report = evaluate(strat.name, res.n_closed, res.avg_net,
                              res.max_drawdown, res.peak_utilization,
                              res2x.total_net)

            table = Table(title=f"{strat.name}（滑價 {cfg1.slippage_ticks} ticks）")
            for col in ("項目", "值"):
                table.add_column(col)
            table.add_row("平倉筆數", str(res.n_closed))
            table.add_row("總淨損益", f"NT${res.total_net:,.0f}")
            table.add_row("每筆期望值", f"NT${res.avg_net:,.0f}")
            table.add_row("期末權益", f"NT${res.equity_curve[-1][1]:,.0f}" if res.equity_curve else "n/a")
            table.add_row("MDD", f"{res.max_drawdown * 100:.1f}%")
            table.add_row("保證金佔用峰值", f"{res.peak_utilization * 100:.1f}%")
            table.add_row("滑價×2 總淨損益", f"NT${res2x.total_net:,.0f}")
            table.add_row("略過的進場", str(res.skipped_entries))
            console.print(table)

            for chk in report.checks:
                mark = "[green]PASS[/green]" if chk.passed else "[red]FAIL[/red]"
                console.print(f"  {mark}  {chk.name}（{chk.detail}）")
            verdict = ("[bold green]✅ 通過 criteria——可進 Phase 6 paper trade[/bold green]"
                       if report.passed else
                       "[bold red]❌ 未通過 criteria——結論照實記錄，不進 Phase 6[/bold red]")
            console.print(f"  {verdict}\n")

            out = DB_PATH.parent / f"backtest_{strat.name}.csv"
            with out.open("w", newline="") as f:
                w = _csv.writer(f)
                w.writerow(["open_date", "close_date", "reason", "lots",
                            "entry_credit_points", "exit_debit_points",
                            "costs_twd", "net_twd"])
                for t in res.trades:
                    w.writerow([t.open_date, t.close_date, t.reason, t.lots,
                                f"{t.entry_credit_points:.1f}", f"{t.exit_debit_points:.1f}",
                                f"{t.costs_twd:.0f}", f"{t.net_twd:.0f}"])
            console.print(f"  trade log → {out}\n")


@app.command("notify-test")
def notify_test():
    """送一則測試訊息到 Discord webhook（驗證 .env 設定）。"""
    _load_env()
    n = DiscordNotifier()
    if not n.enabled:
        console.print("[red]DISCORD_WEBHOOK_URL 未設定（.env 或環境變數）[/red]")
        raise typer.Exit(1)
    ok = n.send("✅ txolab 通知測試：Discord webhook 設定成功")
    console.print("已送出 ✅" if ok else "[red]送出失敗[/red]")
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()
