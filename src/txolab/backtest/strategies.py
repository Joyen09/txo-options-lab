"""四個基準策略（SPEC M6 三賣方 + 2026-07-31 新增 B1 買方）——驗證機制，不是找聖杯。

共同紀律：一次一組部位、空手才進場、delta 選檔（用當日 smile IV 反推）、
到期前 force_close_dte 日一律強制平倉（結算週不賭）。
賣方以「回補成本 ≥ 進場權利金 × stop_credit_mult」停損；
買方以市值對進場權利金的倍率停利/停損。

- vertical_spread：賣出 put 信用價差（賣 ~0.25Δ put + 買 ~0.10Δ put 保險）
- iron_condor：call/put 兩邊各一組信用價差（賣 ±0.20Δ、買 ±0.10Δ）
- short_strangle：裸賣 ±0.25Δ——純賣方對照組，另加 delta 停損
- long_strangle_low_iv：IV rank 低檔買 ±0.25Δ 雙邊——買方基準（B1）
"""
from __future__ import annotations

from ..data.chain import ExpirySlice
from ..pricing.greeks import greeks
from ..vol.surface import Smile, build_smile_cached
from .engine import EntrySignal, Leg, Strategy


def _strike_by_delta(sm: Smile, cp: str, target: float, r: float) -> float | None:
    """回傳該 cp 中 delta 最接近 target 的履約價（解不出 IV 的檔位不參與）。"""
    best: tuple[float, float] | None = None  # (|delta−target|, strike)
    for p in sm.points:
        if p.cp != cp or p.iv is None:
            continue
        d = greeks(cp, sm.forward, p.strike, r, p.iv, sm.t_years).delta
        cand = (abs(d - target), p.strike)
        best = min(best, cand) if best else cand
    return best[1] if best else None


def _wing_by_delta(sm: Smile, cp: str, short_strike: float, target: float,
                   r: float) -> float | None:
    """保險腳：在短腳「更價外側」找 delta 最接近 target 的履約價。

    delta 制翼寬會隨指數水準與 IV 自動縮放——固定點數在指數翻倍後
    會退化成貼著短腳的無效保險（2026-07-31 結構修正，見 backtest.toml）。
    """
    direction = 1.0 if cp == "C" else -1.0
    best: tuple[float, float] | None = None
    for p in sm.points:
        if p.cp != cp or p.iv is None:
            continue
        if direction * (p.strike - short_strike) <= 0:
            continue  # 必須真的更價外，結構才成立
        d = greeks(cp, sm.forward, p.strike, r, p.iv, sm.t_years).delta
        cand = (abs(d - target), p.strike)
        best = min(best, cand) if best else cand
    return best[1] if best else None


class VerticalSpread(Strategy):
    name, kind = "vertical_spread", "vertical"

    def __init__(self, short_delta: float, wing_delta: float, stop_credit_mult: float):
        self.short_delta = short_delta
        self.wing_delta = wing_delta
        self.stop_credit_mult = stop_credit_mult

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        short = _strike_by_delta(sm, "P", -self.short_delta, r)
        if short is None:
            return None
        long = _wing_by_delta(sm, "P", short, -self.wing_delta, r)
        if long is None:
            return None
        return EntrySignal(
            legs=(Leg(sl.expiry_code, short, "P", -1), Leg(sl.expiry_code, long, "P", +1)),
            expiry=sl.expiry, kind=self.kind)


class IronCondor(Strategy):
    name, kind = "iron_condor", "condor"

    def __init__(self, short_delta: float, wing_delta: float, stop_credit_mult: float):
        self.short_delta = short_delta
        self.wing_delta = wing_delta
        self.stop_credit_mult = stop_credit_mult

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        sc = _strike_by_delta(sm, "C", self.short_delta, r)
        sp = _strike_by_delta(sm, "P", -self.short_delta, r)
        if sc is None or sp is None or sc <= sp:
            return None
        lc = _wing_by_delta(sm, "C", sc, self.wing_delta, r)
        lp = _wing_by_delta(sm, "P", sp, -self.wing_delta, r)
        if lc is None or lp is None:
            return None
        return EntrySignal(
            legs=(Leg(sl.expiry_code, sc, "C", -1), Leg(sl.expiry_code, lc, "C", +1),
                  Leg(sl.expiry_code, sp, "P", -1), Leg(sl.expiry_code, lp, "P", +1)),
            expiry=sl.expiry, kind=self.kind)


class ShortStrangle(Strategy):
    name, kind = "short_strangle", "strangle"

    def __init__(self, short_delta: float, stop_credit_mult: float, stop_abs_delta: float):
        self.short_delta = short_delta
        self.stop_credit_mult = stop_credit_mult
        self.stop_abs_delta = stop_abs_delta  # 任一賣方腳 |Δ| 觸頂 → 強制停損

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        sc = _strike_by_delta(sm, "C", self.short_delta, r)
        sp = _strike_by_delta(sm, "P", -self.short_delta, r)
        if sc is None or sp is None or sc <= sp:
            return None
        return EntrySignal(
            legs=(Leg(sl.expiry_code, sc, "C", -1), Leg(sl.expiry_code, sp, "P", -1)),
            expiry=sl.expiry, kind=self.kind)


class LongStrangleLowIV(Strategy):
    """B1 買方基準（2026-07-31 pre-registered，門檻見 backtest.toml，不可回調）。

    波動率便宜（近月 IV rank 低檔）時買雙邊——方向中性，賭的是
    「便宜買進的 vega/gamma 在波動率回升或大行情時兌現」。
    買方最大虧損 = 付出的權利金（sizing 即以此預算），無保證金、無追繳。
    """
    name, kind = "long_strangle_low_iv", "debit"

    def __init__(self, buy_delta: float, iv_rank_max: float, iv_rank_window: int,
                 premium_budget: float, take_profit_mult: float, stop_value_frac: float):
        self.buy_delta = buy_delta
        self.iv_rank_max = iv_rank_max
        self.iv_rank_window = iv_rank_window
        self.premium_budget = premium_budget
        self.take_profit_mult = take_profit_mult
        self.stop_value_frac = stop_value_frac

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        lc = _strike_by_delta(sm, "C", self.buy_delta, r)
        lp = _strike_by_delta(sm, "P", -self.buy_delta, r)
        if lc is None or lp is None or lc <= lp:
            return None
        return EntrySignal(
            legs=(Leg(sl.expiry_code, lc, "C", +1), Leg(sl.expiry_code, lp, "P", +1)),
            expiry=sl.expiry, kind=self.kind)
