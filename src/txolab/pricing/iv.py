"""隱含波動率 solver (Brent 法反推 Black-76 的 σ)。

無解情境要「明確標記原因、回 None」，不可丟例外炸掉整條 option chain：
市場上永遠有價格低於內含價值 (深價內零流動性、只剩掛牌價) 或
貴到不合理 (深價外亂報價) 的序列，chain 要能整條算完、壞的標記跳過。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq

from . import black76

SIGMA_LO = 1e-6
SIGMA_HI = 10.0  # 1000% 年化波動——超過這個就不是市場，是垃圾報價


@dataclass(frozen=True)
class IVResult:
    iv: float | None
    reason: str | None = None  # None=成功；否則 'expired'/'below_intrinsic'/'above_upper_bound'/'no_solution'

    @property
    def ok(self) -> bool:
        return self.iv is not None


def implied_vol(cp: str, price: float, f: float, k: float, r: float, t: float) -> IVResult:
    """由市場價格反推隱波。回 IVResult 而非裸 float，讓失敗原因可追蹤。"""
    cp = cp.upper()
    if cp not in ("C", "P"):
        raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")
    if t <= 0:
        return IVResult(None, "expired")
    if f <= 0 or k <= 0 or price is None or price < 0:
        return IVResult(None, "no_solution")

    disc = math.exp(-r * t)
    lower = black76.intrinsic(cp, f, k, r, t)          # 歐式價格理論下限
    upper = disc * f if cp == "C" else disc * k        # 理論上限
    if price < lower - 1e-12:
        return IVResult(None, "below_intrinsic")
    if price >= upper - 1e-12:
        return IVResult(None, "above_upper_bound")

    def objective(sigma: float) -> float:
        return black76.price(cp, f, k, r, sigma, t) - price

    lo, hi = objective(SIGMA_LO), objective(SIGMA_HI)
    if lo > 0 or hi < 0:
        # 價格貼著下限 (深價內時間價值≈0) 之類的數值極限，標記而非硬解
        return IVResult(None, "no_solution")
    try:
        sigma = brentq(objective, SIGMA_LO, SIGMA_HI, xtol=1e-12, rtol=1e-12, maxiter=200)
    except (ValueError, RuntimeError):
        return IVResult(None, "no_solution")
    return IVResult(float(sigma))
