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
