"""IV 表面：各到期日 smile、ATM IV（forward 兩側履約價內插）、term structure、
IV rank / percentile、25-delta skew 近似。

價格取用順序：結算價優先（期交所每檔都有結算價，含無成交檔），其次收盤價。
反推失敗（低於內含價值、深度價外垃圾報價…）的履約價會被標記略過，
整條 chain 照算——這正是 iv.implied_vol 回 IVResult 而非丟例外的原因。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..data.chain import ExpirySlice
from ..pricing.greeks import greeks
from ..pricing.iv import implied_vol


@dataclass(frozen=True)
class SmilePoint:
    strike: float
    cp: str
    price: float
    iv: float | None
    reason: str | None  # 反推失敗原因（成功為 None）


@dataclass(frozen=True)
class Smile:
    expiry_code: str
    forward: float
    t_years: float
    points: list[SmilePoint]

    def iv_by_strike(self) -> dict[float, float]:
        """每個履約價一個 IV：C/P 都解出來就取平均（無套利下兩者應一致）。"""
        acc: dict[float, list[float]] = {}
        for p in self.points:
            if p.iv is not None:
                acc.setdefault(p.strike, []).append(p.iv)
        return {k: sum(v) / len(v) for k, v in acc.items()}


def build_smile(sl: ExpirySlice, r: float) -> Smile:
    points: list[SmilePoint] = []
    for row in sl.rows:
        price = row.settlement if row.settlement else row.close
        if price is None or price <= 0:
            continue
        res = implied_vol(row.cp, price, sl.forward, row.strike, r, sl.t_years)
        points.append(SmilePoint(row.strike, row.cp, price, res.iv, res.reason))
    return Smile(sl.expiry_code, sl.forward, sl.t_years, points)


def atm_iv(smile: Smile) -> float | None:
    """ATM IV = 以 forward 兩側最近履約價的 IV 線性內插；解不出來回 None。"""
    ivs = smile.iv_by_strike()
    if not ivs:
        return None
    below = [k for k in ivs if k <= smile.forward]
    above = [k for k in ivs if k >= smile.forward]
    if not below or not above:
        return None  # forward 落在 smile 之外——資料有問題，不硬掰
    k_lo, k_hi = max(below), min(above)
    if k_lo == k_hi:
        return ivs[k_lo]
    w = (smile.forward - k_lo) / (k_hi - k_lo)
    return ivs[k_lo] * (1 - w) + ivs[k_hi] * w


def skew_25d(smile: Smile, r: float) -> float | None:
    """put−call 25-delta skew 近似：取 delta 最接近 ±0.25 的履約價之 IV 差。

    正值 = 下檔保護較貴（台指常態）；急遽放大通常是避險需求湧入。
    """
    best_c: tuple[float, float] | None = None  # (|delta−0.25|, iv)
    best_p: tuple[float, float] | None = None
    for p in smile.points:
        if p.iv is None:
            continue
        d = greeks(p.cp, smile.forward, p.strike, r, p.iv, smile.t_years).delta
        if p.cp == "C":
            cand = (abs(d - 0.25), p.iv)
            best_c = min(best_c, cand) if best_c else cand
        else:
            cand = (abs(d + 0.25), p.iv)
            best_p = min(best_p, cand) if best_p else cand
    if best_c is None or best_p is None:
        return None
    return best_p[1] - best_c[1]


def iv_rank(series: list[float], current: float) -> float | None:
    """IV rank = (現值 − 窗口最低) / (窗口最高 − 窗口最低) × 100。窗口不足回 None。"""
    if len(series) < 2:
        return None
    lo, hi = min(series), max(series)
    if hi == lo:
        return 50.0
    return (current - lo) / (hi - lo) * 100.0


def iv_percentile(series: list[float], current: float) -> float | None:
    """IV percentile = 窗口內低於現值的天數占比 × 100。窗口不足回 None。"""
    if len(series) < 2:
        return None
    return sum(1 for v in series if v < current) / len(series) * 100.0
