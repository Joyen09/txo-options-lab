"""每日模擬交易一步：讀當日 chain → 套用（與回測共用的）規則 → 更新帳本 → 產報告。

**不含任何下單程式碼**（見 paper/__init__.py）。成交價沿用回測假設：
結算價 ± 滑價 tick。這一階段要驗證的是「規則接上每日真實資料後跑不跑得動」，
不是驗證優勢——優勢由樣本外驗證仲裁（README 樣本外驗證約定）。

已知限制（誠實記錄）：日行情檔沒有 bid/ask，所以本階段的模擬成交價與回測完全
同源，對「實際可執行性」的檢驗力有限。要真正檢驗滑價假設，需接即時報價
（唯讀），列為下一階段。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from ..backtest.engine import (
    EngineConfig,
    Strategy,
    _leg_value,
    _marks,
    close_reason,
    entry_gates_ok,
    size_lots,
    trade_legs_twd,
)
from ..contracts.spec import MULTIPLIER
from ..data.chain import build_chain
from ..data.store import Store
from ..vol.surface import atm_iv, build_smile_cached
from .ledger import OpenPosition, PaperLedger, PaperTrade


@dataclass(frozen=True)
class PaperDay:
    """某一交易日的模擬結果——給 CLI 與 Discord 報告用。"""

    date: dt.date
    action: str          # 'open' | 'close' | 'hold' | 'flat' | 'skipped' | 'no_data'
    detail: str
    cash: Decimal
    unrealized: Decimal
    equity: Decimal
    realized_net: Decimal
    position: OpenPosition | None


def _indicator_series(store: Store, strategy: Strategy, cfg: EngineConfig,
                      upto: dt.date) -> tuple[list[float], list[float]]:
    """重建進場閘門所需的指標序列（IV rank 用 iv、趨勢用近月 TX），逐日算法同引擎。

    只回看「窗口所需的天數」——策略沒設的門檻不算，省下大量 chain 組裝。
    """
    need = 0
    if strategy.iv_rank_max is not None:
        need = max(need, strategy.iv_rank_window)
    if strategy.trend_ma_days is not None:
        need = max(need, strategy.trend_ma_days)
    if need == 0:
        return [], []
    dates = [dt.date.fromisoformat(s) for s in store.trade_dates()]
    dates = [d for d in dates if d <= upto][-need:]

    iv_series: list[float] = []
    px_series: list[float] = []
    for d in dates:
        options, futures = store.options_on(d), store.futures_on(d)
        if not options or not futures:
            continue
        try:
            chain = build_chain(d, options, futures)
        except Exception:  # noqa: BLE001, S112 — 資料不完整的日子跳過（同引擎）
            continue
        if not chain.slices:
            continue
        monthly = [s for s in chain.slices if len(s.expiry_code) == 6]
        index = monthly[0].forward if monthly else chain.slices[0].forward
        if strategy.iv_rank_max is not None and monthly:
            a = atm_iv(build_smile_cached(monthly[0], cfg.r))
            if a is not None:
                iv_series.append(a)
        if strategy.trend_ma_days is not None:
            px_series.append(index)
    return iv_series, px_series


def run_paper_day(store: Store, ledger: PaperLedger, strategy: Strategy,
                  cfg: EngineConfig, date: dt.date | None = None) -> PaperDay:
    """跑一個交易日。冪等：同一日重跑不會重複開平倉（以 last_run_date 把關）。"""
    dates = store.trade_dates()
    if not dates:
        raise ValueError("DB 是空的——先跑 txolab fetch")
    d = date if date is not None else dt.date.fromisoformat(dates[-1])

    def snapshot(action: str, detail: str, unrealized: Decimal = Decimal(0)) -> PaperDay:
        cash = ledger.cash
        return PaperDay(date=d, action=action, detail=detail, cash=cash,
                        unrealized=unrealized, equity=cash + unrealized,
                        realized_net=ledger.realized_net, position=ledger.open_position())

    last = ledger.last_run_date
    if last is not None and last >= d:
        return snapshot("hold", f"{d} 已處理過（上次執行 {last}），不重複動作")

    options, futures = store.options_on(d), store.futures_on(d)
    if not options or not futures:
        return snapshot("no_data", f"{d} 缺選擇權或 TX 資料——不動作")
    try:
        chain = build_chain(d, options, futures)
    except Exception as e:  # noqa: BLE001 — 資料不完整就不動作，不臆測
        return snapshot("no_data", f"{d} chain 組不起來（{e}）——不動作")
    if not chain.slices:
        return snapshot("no_data", f"{d} 無可用到期別——不動作")

    marks = _marks(chain)
    monthly = [s for s in chain.slices if len(s.expiry_code) == 6]
    index = monthly[0].forward if monthly else chain.slices[0].forward

    # ---- 1. 管理在倉部位（規則與回測同一份實作）----
    pos = ledger.open_position()
    if pos is not None:
        reason = close_reason(pos.signal.legs, pos.signal.expiry,
                              pos.entry_credit_points, d, chain, marks, strategy, cfg)
        value = _leg_value(pos.signal.legs, marks)
        if reason is not None and value is not None:
            exit_points, cash_flow, exit_costs = trade_legs_twd(
                pos.signal.legs, pos.lots, marks, closing=True, cfg=cfg)
            total_costs = pos.entry_costs + exit_costs
            net = (Decimal(str(round(pos.entry_credit_points + exit_points, 4)))
                   * MULTIPLIER * pos.lots) - total_costs
            ledger.record_close(PaperTrade(
                strategy=strategy.name, open_date=pos.open_date, close_date=d,
                reason=reason, lots=pos.lots,
                entry_credit_points=pos.entry_credit_points,
                exit_debit_points=-exit_points,
                costs_twd=total_costs, net_twd=net), cash_flow)
            ledger.mark_run(d)
            # 平倉後直接收工——當日不再進場，同引擎的 closed_today 冷靜規則
            return snapshot("close", f"平倉（{reason}）{pos.lots} 口，淨損益 NT${net:,.0f}")
        unrealized = (Decimal(str(round(value, 4))) * MULTIPLIER * pos.lots
                      if value is not None else Decimal(0))
        ledger.mark_run(d)
        return snapshot("hold", f"續抱 {pos.lots} 口（{pos.open_date} 進場，"
                                f"{pos.signal.expiry} 到期）", unrealized)

    # ---- 2. 空手：評估進場 ----
    iv_series, px_series = _indicator_series(store, strategy, cfg, d)
    if not entry_gates_ok(strategy, iv_series, px_series, index):
        ledger.mark_run(d)
        return snapshot("flat", f"進場閘門未開（指數 {index:,.0f}）——空手")

    sl = next((s for s in monthly if (s.expiry - d).days >= cfg.min_entry_dte), None)
    sig = strategy.entry(sl, cfg.r) if sl is not None else None
    if sig is None:
        ledger.mark_run(d)
        return snapshot("flat", "找不到符合 delta 條件的履約價——空手")

    equity = ledger.cash
    lots, _margin = size_lots(strategy, sig, equity, index, marks, cfg)
    if lots < 1:
        ledger.mark_run(d)
        return snapshot("skipped", "口數不足 1（權利金超出預算）——不進場")

    entry_points, cash_flow, entry_costs = trade_legs_twd(
        sig.legs, lots, marks, closing=False, cfg=cfg)
    ledger.record_open(OpenPosition(
        strategy=strategy.name, open_date=d, signal=sig, lots=lots,
        entry_credit_points=entry_points, entry_costs=entry_costs), cash_flow)
    ledger.mark_run(d)
    legs_txt = " / ".join(f"{'買' if l.qty > 0 else '賣'}{l.strike:.0f}{l.cp}"
                          for l in sig.legs)
    value = _leg_value(sig.legs, marks)
    unrealized = (Decimal(str(round(value, 4))) * MULTIPLIER * lots
                  if value is not None else Decimal(0))
    return snapshot("open", f"進場 {lots} 口：{legs_txt}（{sig.expiry} 到期）", unrealized)


def format_report(day: PaperDay, strategy_name: str) -> str:
    """Discord 用的純文字報告（模擬，非真實部位——標題就講明）。"""
    lines = [
        f"📄 **Paper trade（模擬，未下單）** {day.date} — {strategy_name}",
        f"動作：{day.detail}",
        (f"權益 NT${day.equity:,.0f}（現金 NT${day.cash:,.0f}"
         f" + 未實現 NT${day.unrealized:,.0f}）"),
        f"已實現累計 NT${day.realized_net:,.0f}",
    ]
    if day.position is not None:
        p = day.position
        legs = " / ".join(f"{'買' if l.qty > 0 else '賣'}{l.strike:.0f}{l.cp}"
                          for l in p.signal.legs)
        lines.append(f"在倉：{p.lots} 口 {legs}，{p.signal.expiry} 到期")
    return "\n".join(lines)
