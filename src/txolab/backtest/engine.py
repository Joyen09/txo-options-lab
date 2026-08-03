"""逐日重放回測引擎。

成交假設（誠實優先，SPEC M6）：
  - 每日頻率，訊號與成交同在收盤後：以「結算價 ± 滑價」成交
    （買進 +slip、賣出 −slip，slip = ticks × 該價位帶 tick；日行情檔無
    bid/ask，這是已知限制，滑價參數要做敏感度分析）
  - 成本全含：期交稅（權利金 × 稅率、雙邊）+ 手續費（每口每邊）
  - 保證金逐日重算（指數以最近月 TX 結算價近似，期現基差誤差已記錄）；
    賣方口數以「保證金佔用 ≤ util_cap × 當時權益」決定；
    買方（debit）無保證金，口數以「權利金支出 ≤ premium_budget × 權益」決定
  - 條件化進場：策略可設 IV rank 門檻與趨勢均線門檻（指標逐日自算，
    滿窗口前不進場）
  - 結算週處理：到期前 force_close_dte 日強制平倉——引擎永不持有到期，
    因此不需要最後結算價（那要另一個資料源）
  - 無任何隨機性：同一輸入重跑 bit-identical

金錢一律 Decimal；定價/Greeks 用 float（CLAUDE.md 鐵律 4）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ..contracts.spec import MULTIPLIER, tick_size
from ..data.chain import Chain, ExpirySlice, build_chain
from ..data.store import Store
from ..margin.engine import (
    strangle_margin,
    transaction_cost,
    vertical_spread_margin,
)
from ..pricing.greeks import greeks
from ..pricing.iv import implied_vol
from ..vol.surface import atm_iv, build_smile_cached, iv_rank

MarkKey = tuple[str, float, str]  # (expiry_code, strike, cp)


@dataclass(frozen=True)
class Leg:
    expiry_code: str
    strike: float
    cp: str   # 'C' | 'P'
    qty: int  # 每口組合中 +1 買 / -1 賣


@dataclass(frozen=True)
class EntrySignal:
    legs: tuple[Leg, ...]
    expiry: dt.date
    kind: str  # 'vertical' | 'condor' | 'strangle' | 'debit'——決定保證金算法


@dataclass(frozen=True)
class ClosedTrade:
    strategy: str
    open_date: dt.date
    close_date: dt.date
    reason: str          # 'stop' | 'delta_stop' | 'take_profit' | 'expiry_week' | 'end'
    lots: int
    entry_credit_points: float   # 每口淨收權利金（負值 = 淨付，即買方）
    exit_debit_points: float     # 每口平倉淨付（負值 = 平倉淨收）
    costs_twd: Decimal           # 全部稅費（進出雙邊 × 口數）
    net_twd: Decimal             # 淨損益（含成本）


@dataclass
class _Position:
    signal: EntrySignal
    lots: int
    open_date: dt.date
    entry_credit_points: float
    entry_costs: Decimal
    margin: Decimal


@dataclass
class BacktestResult:
    strategy: str
    slippage_ticks: int
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[tuple[str, Decimal]] = field(default_factory=list)
    utilization_curve: list[tuple[str, float]] = field(default_factory=list)
    skipped_entries: int = 0

    @property
    def n_closed(self) -> int:
        return len(self.trades)

    @property
    def total_net(self) -> Decimal:
        return sum((t.net_twd for t in self.trades), Decimal(0))

    @property
    def avg_net(self) -> Decimal:
        return self.total_net / self.n_closed if self.n_closed else Decimal(0)

    @property
    def max_drawdown(self) -> float:
        """峰值回撤，以初始資金為分母（criteria 定義）。"""
        if not self.equity_curve:
            return 0.0
        initial = self.equity_curve[0][1]
        peak, mdd = initial, Decimal(0)
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
        return float(mdd / initial)

    @property
    def peak_utilization(self) -> float:
        return max((u for _, u in self.utilization_curve), default=0.0)


@dataclass(frozen=True)
class EngineConfig:
    initial_equity: Decimal
    slippage_ticks: int
    tax_rate: Decimal
    fee_per_lot: Decimal
    util_cap: float           # 保證金佔用上限（進場 sizing 用）
    max_lots: int
    min_entry_dte: int        # 進場時近月至少要剩幾個日曆日
    force_close_dte: int      # 到期前 N 日強制平倉
    r: float                  # 折現率（Greeks/delta 用）
    margin_a: Decimal         # 原始保證金檔 A/B/C（config/margin.toml）
    margin_b: Decimal
    margin_c: Decimal


class Strategy:
    """策略介面：entry 回傳每口組合的腳；stop 條件由引擎依參數檢查。"""
    name: str = ""
    kind: str = ""
    stop_credit_mult: float = 2.0
    stop_abs_delta: float | None = None  # 任一賣方腳 |delta| 觸頂即停損（None = 不檢查）
    # ---- 買方（debit）部位專用 ----
    premium_budget: float | None = None   # 非 None = 預算制 sizing（權利金支出 <= 權益×此值）
    take_profit_mult: float | None = None  # 市值 >= 進場權利金 × 此值 → 停利
    stop_value_frac: float | None = None   # 市值 <= 進場權利金 × 此值 → 停損
    # ---- 條件化進場 ----
    iv_rank_max: float | None = None      # 近月 IV rank <= 此值才進場（None = 不限）
    iv_rank_window: int = 60              # rank 回看交易日數；滿窗口前不進場
    trend_ma_days: int | None = None      # 近月 TX > N 日均線（含當日）才進場（None = 不限）

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        raise NotImplementedError


def _marks(chain: Chain) -> dict[MarkKey, float]:
    out: dict[MarkKey, float] = {}
    for sl in chain.slices:
        for row in sl.rows:
            px = row.settlement if row.settlement else row.close
            if px is not None and px > 0:
                out[(row.expiry_code, row.strike, row.cp)] = px
    return out


def _fill(mark: float, qty: int, slippage_ticks: int) -> float:
    """買進付高、賣出收低；不低於最小 tick。"""
    slip = slippage_ticks * tick_size(mark)
    px = mark + slip if qty > 0 else mark - slip
    return max(round(px, 1), 0.1)


def _position_margin(sig: EntrySignal, index: float, marks: dict[MarkKey, float],
                     cfg: EngineConfig) -> Decimal | None:
    """每口保證金。缺 mark 時回 None（無法評價 → 不進場/沿用前值）。"""
    shorts = [l for l in sig.legs if l.qty < 0]
    longs = [l for l in sig.legs if l.qty > 0]
    if sig.kind == "debit":
        return Decimal(0)  # 純買方/債務價差：權利金付清即最大風險，無保證金
    if sig.kind == "vertical":
        return vertical_spread_margin(shorts[0].strike, longs[0].strike)
    if sig.kind == "condor":
        s_call = next(l for l in shorts if l.cp == "C")
        s_put = next(l for l in shorts if l.cp == "P")
        l_call = next(l for l in longs if l.cp == "C")
        l_put = next(l for l in longs if l.cp == "P")
        return (vertical_spread_margin(s_call.strike, l_call.strike)
                + vertical_spread_margin(s_put.strike, l_put.strike))
    if sig.kind == "strangle":
        s_call = next(l for l in shorts if l.cp == "C")
        s_put = next(l for l in shorts if l.cp == "P")
        pc = marks.get((s_call.expiry_code, s_call.strike, "C"))
        pp = marks.get((s_put.expiry_code, s_put.strike, "P"))
        if pc is None or pp is None:
            return None
        return strangle_margin(index, s_call.strike, pc, s_put.strike, pp,
                               cfg.margin_a, cfg.margin_b, cfg.margin_c)
    raise ValueError(f"未知部位型態: {sig.kind}")


def _leg_value(legs: tuple[Leg, ...], marks: dict[MarkKey, float]) -> float | None:
    """每口組合的市值（點，多頭為正）。任一腳缺 mark 回 None。"""
    total = 0.0
    for leg in legs:
        px = marks.get((leg.expiry_code, leg.strike, leg.cp))
        if px is None:
            return None
        total += leg.qty * px
    return total


def _short_deltas(pos: _Position, chain: Chain, r: float) -> list[float]:
    """賣方腳的當前 delta（用當日該腳結算價反推 IV）。算不出來的腳略過。

    只解「賣方腳本身」的 IV（每天 2 次 Brent 求解），不建整條 smile
    （數百次）——delta 停損逐日要跑，這裡是 short_strangle 的效能熱點。
    輸入與解法跟 smile 版完全相同，結果 bit-identical。
    """
    sl = next((s for s in chain.slices
               if s.expiry_code == pos.signal.legs[0].expiry_code), None)
    if sl is None:
        return []
    price_by = {(row.strike, row.cp): (row.settlement if row.settlement else row.close)
                for row in sl.rows}
    out = []
    for leg in pos.signal.legs:
        if leg.qty >= 0:
            continue
        price = price_by.get((leg.strike, leg.cp))
        if price is None or price <= 0:
            continue
        res = implied_vol(leg.cp, price, sl.forward, leg.strike, r, sl.t_years)
        if res.iv is None:
            continue
        out.append(greeks(leg.cp, sl.forward, leg.strike, r, res.iv, sl.t_years).delta)
    return out


def run_backtest(store: Store, strategy: Strategy, cfg: EngineConfig,
                 start: dt.date | None = None,
                 end: dt.date | None = None) -> BacktestResult:
    res = BacktestResult(strategy=strategy.name, slippage_ticks=cfg.slippage_ticks)
    cash = cfg.initial_equity
    pos: _Position | None = None
    iv_series: list[float] = []  # 近月 ATM IV 逐日序列（條件化進場的 rank 窗口）
    px_series: list[float] = []  # 近月 TX 價格逐日序列（趨勢均線窗口）

    dates = [dt.date.fromisoformat(s) for s in store.trade_dates()]
    dates = [d for d in dates
             if (start is None or d >= start) and (end is None or d <= end)]

    def trade_legs_twd(legs: tuple[Leg, ...], lots: int,
                       marks: dict[MarkKey, float], closing: bool,
                       ) -> tuple[float, Decimal, Decimal]:
        """成交全部腳：回傳（每口淨點數流入, 現金流 TWD, 稅費 TWD）。closing 時方向反轉。"""
        points_in = 0.0
        costs = Decimal(0)
        for leg in legs:
            qty = -leg.qty if closing else leg.qty
            mark = marks[(leg.expiry_code, leg.strike, leg.cp)]
            px = _fill(mark, qty, cfg.slippage_ticks)
            points_in += -qty * px  # 賣出收權利金、買進付權利金
            costs += transaction_cost(px, cfg.tax_rate, cfg.fee_per_lot, lots)
        cash_flow = Decimal(str(round(points_in, 4))) * MULTIPLIER * lots - costs
        return points_in, cash_flow, costs

    for d in dates:
        options = store.options_on(d)
        futures = store.futures_on(d)
        if not options or not futures:
            continue
        try:
            chain = build_chain(d, options, futures)
        except Exception:  # noqa: BLE001, S112 — 該日資料不完整（例如缺 TX）就跳過，不臆測
            continue
        if not chain.slices:
            continue
        marks = _marks(chain)
        monthly = [s for s in chain.slices if len(s.expiry_code) == 6]
        index = monthly[0].forward if monthly else chain.slices[0].forward

        # ---- 0. 條件化進場的指標序列（僅有設門檻的策略需要）----
        if strategy.iv_rank_max is not None and monthly:
            a = atm_iv(build_smile_cached(monthly[0], cfg.r))
            if a is not None:
                iv_series.append(a)
        if strategy.trend_ma_days is not None:
            px_series.append(index)

        # ---- 1. 管理在倉部位 ----
        closed_today = False
        if pos is not None:
            reason: str | None = None
            dte = (pos.signal.expiry - d).days
            value = _leg_value(pos.signal.legs, marks)
            if dte <= cfg.force_close_dte:
                reason = "expiry_week"
            elif value is not None and pos.entry_credit_points > 0:  # 收租部位
                buyback = -value  # 回補成本（點）
                if buyback >= pos.entry_credit_points * strategy.stop_credit_mult:
                    reason = "stop"
                elif strategy.stop_abs_delta is not None:
                    deltas = _short_deltas(pos, chain, cfg.r)
                    if deltas and max(abs(x) for x in deltas) >= strategy.stop_abs_delta:
                        reason = "delta_stop"
            elif value is not None and pos.entry_credit_points < 0:  # 買方（debit）部位
                debit = -pos.entry_credit_points  # 進場付出的權利金（點）
                if (strategy.take_profit_mult is not None
                        and value >= debit * strategy.take_profit_mult):
                    reason = "take_profit"
                elif (strategy.stop_value_frac is not None
                        and value <= debit * strategy.stop_value_frac):
                    reason = "stop"
            if reason is not None and value is not None:
                exit_points, cash_flow, exit_costs = trade_legs_twd(
                    pos.signal.legs, pos.lots, marks, closing=True)
                cash += cash_flow
                total_costs = pos.entry_costs + exit_costs
                net = (Decimal(str(round(pos.entry_credit_points + exit_points, 4)))
                       * MULTIPLIER * pos.lots) - total_costs
                res.trades.append(ClosedTrade(
                    strategy=strategy.name, open_date=pos.open_date, close_date=d,
                    reason=reason, lots=pos.lots,
                    entry_credit_points=pos.entry_credit_points,
                    exit_debit_points=-exit_points,
                    costs_twd=total_costs, net_twd=net))
                pos = None
                closed_today = True  # 當日不再進場——停損當下的行情不適合立刻回頭賣

        # ---- 2. 權益評價 ----
        unrealized = Decimal(0)
        if pos is not None:
            value = _leg_value(pos.signal.legs, marks)
            if value is not None:
                unrealized = Decimal(str(round(value, 4))) * MULTIPLIER * pos.lots
        equity = cash + unrealized
        if equity <= 0:
            res.equity_curve.append((d.isoformat(), equity))
            break  # 爆倉——真實世界早被斷頭，回測到此為止

        # ---- 3. 空手時評估進場（當日剛平倉則跳過；各門檻在此把關）----
        rank_ok = True
        if strategy.iv_rank_max is not None:
            w = strategy.iv_rank_window
            if len(iv_series) < w:
                rank_ok = False  # 滿窗口前不進場（rank 無意義）
            else:
                rank = iv_rank(iv_series[-w:], iv_series[-1])
                rank_ok = rank is not None and rank <= strategy.iv_rank_max
        trend_ok = True
        if strategy.trend_ma_days is not None:
            n = strategy.trend_ma_days
            if len(px_series) < n:
                trend_ok = False  # 均線暖機期不進場
            else:
                trend_ok = index > sum(px_series[-n:]) / n
        if pos is None and not closed_today and rank_ok and trend_ok:
            sl = next((s for s in monthly
                       if (s.expiry - d).days >= cfg.min_entry_dte), None)
            sig = strategy.entry(sl, cfg.r) if sl is not None else None
            if sig is not None:
                lots = 0
                margin_per_lot = Decimal(0)
                if strategy.premium_budget is not None:  # 買方：權利金預算制
                    cost = _leg_value(sig.legs, marks)   # 每口權利金支出（點，正值）
                    if cost is not None and cost > 0:
                        budget = equity * Decimal(str(strategy.premium_budget))
                        lots = min(int(budget / (Decimal(str(round(cost, 4))) * MULTIPLIER)),
                                   cfg.max_lots)
                else:  # 賣方：保證金佔用制
                    m = _position_margin(sig, index, marks, cfg)
                    if m is not None and m > 0:
                        margin_per_lot = m
                        budget = equity * Decimal(str(cfg.util_cap))
                        lots = min(int(budget / m), cfg.max_lots)
                if lots < 1:
                    res.skipped_entries += 1
                else:
                    entry_points, cash_flow, entry_costs = trade_legs_twd(
                        sig.legs, lots, marks, closing=False)
                    cash += cash_flow
                    pos = _Position(signal=sig, lots=lots, open_date=d,
                                    entry_credit_points=entry_points,
                                    entry_costs=entry_costs,
                                    margin=margin_per_lot * lots)
                    value = _leg_value(sig.legs, marks)
                    equity = cash + (Decimal(str(round(value, 4))) * MULTIPLIER * lots
                                     if value is not None else Decimal(0))

        # ---- 4. 保證金佔用（逐日重算，加收檔位隨行情變動；買方恆為 0）----
        util = 0.0
        if pos is not None:
            m = _position_margin(pos.signal, index, marks, cfg)
            if m is not None:
                pos.margin = m * pos.lots
            util = float(pos.margin / equity) if equity > 0 else 1.0
        res.equity_curve.append((d.isoformat(), equity))
        res.utilization_curve.append((d.isoformat(), util))

    # ---- 期末強制平倉（讓樣本封閉）----
    # 最後一天可能是殘缺日（例如當日 timer 抓到選擇權檔但 TX 檔尚未上架），
    # 從最後一天往回找第一個「可完整評價」的日子平倉。
    if pos is not None:
        for d in reversed(dates):
            options = store.options_on(d)
            futures = store.futures_on(d)
            if not options or not futures:
                continue
            try:
                chain = build_chain(d, options, futures)
            except Exception:  # noqa: BLE001, S112 — 殘缺日跳過，繼續往回找
                continue
            marks = _marks(chain)
            if _leg_value(pos.signal.legs, marks) is None:
                continue
            exit_points, cash_flow, exit_costs = trade_legs_twd(
                pos.signal.legs, pos.lots, marks, closing=True)
            cash += cash_flow
            total_costs = pos.entry_costs + exit_costs
            net = (Decimal(str(round(pos.entry_credit_points + exit_points, 4)))
                   * MULTIPLIER * pos.lots) - total_costs
            res.trades.append(ClosedTrade(
                strategy=strategy.name, open_date=pos.open_date, close_date=d,
                reason="end", lots=pos.lots,
                entry_credit_points=pos.entry_credit_points,
                exit_debit_points=-exit_points,
                costs_twd=total_costs, net_twd=net))
            if res.equity_curve and res.equity_curve[-1][0] == d.isoformat():
                res.equity_curve[-1] = (d.isoformat(), cash)
            else:
                res.equity_curve.append((d.isoformat(), cash))
            break
    return res
