"""三個基準策略（SPEC M6）——先驗證機制，不是找聖杯。

共同紀律：一次一組部位、空手才進場、delta 選檔（用當日 smile IV 反推）、
收租部位以「回補成本 ≥ 進場權利金 × stop_credit_mult」停損、
到期前 force_close_dte 日一律強制平倉（結算週不賭）。

- vertical_spread：賣出 put 信用價差（賣 ~0.25Δ put + 買更價外 put 保險）
  ——買賣混合、最大虧損鎖死在履約價差
- iron_condor：call/put 兩邊各一組信用價差（±0.20Δ）——兩邊收租、兩邊保險
- short_strangle：裸賣 ±0.25Δ call+put——純賣方對照組，另加 delta 停損
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


def _wing(sm: Smile, cp: str, short_strike: float, offset_points: float) -> float | None:
    """保險腳：往價外方向 offset 點，取最接近且不等於短腳的可交易履約價。"""
    direction = 1.0 if cp == "C" else -1.0
    target = short_strike + direction * offset_points
    candidates = sorted({p.strike for p in sm.points if p.cp == cp}
                        - {short_strike}, key=lambda k: (abs(k - target), k))
    if not candidates:
        return None
    k = candidates[0]
    # 保險腳必須真的在短腳的價外側，否則結構不成立
    if direction * (k - short_strike) <= 0:
        return None
    return k


class VerticalSpread(Strategy):
    name, kind = "vertical_spread", "vertical"

    def __init__(self, short_delta: float, wing_points: float, stop_credit_mult: float):
        self.short_delta = short_delta
        self.wing_points = wing_points
        self.stop_credit_mult = stop_credit_mult

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        short = _strike_by_delta(sm, "P", -self.short_delta, r)
        if short is None:
            return None
        long = _wing(sm, "P", short, self.wing_points)
        if long is None:
            return None
        return EntrySignal(
            legs=(Leg(sl.expiry_code, short, "P", -1), Leg(sl.expiry_code, long, "P", +1)),
            expiry=sl.expiry, kind=self.kind)


class IronCondor(Strategy):
    name, kind = "iron_condor", "condor"

    def __init__(self, short_delta: float, wing_points: float, stop_credit_mult: float):
        self.short_delta = short_delta
        self.wing_points = wing_points
        self.stop_credit_mult = stop_credit_mult

    def entry(self, sl: ExpirySlice, r: float) -> EntrySignal | None:
        sm = build_smile_cached(sl, r)
        sc = _strike_by_delta(sm, "C", self.short_delta, r)
        sp = _strike_by_delta(sm, "P", -self.short_delta, r)
        if sc is None or sp is None or sc <= sp:
            return None
        lc = _wing(sm, "C", sc, self.wing_points)
        lp = _wing(sm, "P", sp, self.wing_points)
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
