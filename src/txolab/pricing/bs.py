"""Black–Scholes 定價（現貨版）。

TXO 的預設定價模型是 black76.py（用同到期 TX 期貨當 forward）；
這份 BS 是對照用——期交所官方理論價計算器用 BS，交叉驗證時走這裡。

  d1 = (ln(S/K) + (r + σ²/2)T) / (σ√T),  d2 = d1 − σ√T
  Call = S·N(d1) − K·e^(−rT)·N(d2)
  Put  = K·e^(−rT)·N(−d2) − S·N(−d1)
"""
from __future__ import annotations

import math

from scipy.stats import norm


def _validate(s: float, k: float, sigma: float, t: float) -> None:
    if s <= 0 or k <= 0:
        raise ValueError(f"標的價與履約價必須為正: S={s}, K={k}")
    if sigma <= 0:
        raise ValueError(f"波動率必須為正: sigma={sigma}")
    if t <= 0:
        raise ValueError(f"到期時間必須為正 (已到期請走結算損益): T={t}")


def d1_d2(s: float, k: float, r: float, sigma: float, t: float) -> tuple[float, float]:
    _validate(s, k, sigma, t)
    sig_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma**2) * t) / sig_sqrt_t
    return d1, d1 - sig_sqrt_t


def call(s: float, k: float, r: float, sigma: float, t: float) -> float:
    d1, d2 = d1_d2(s, k, r, sigma, t)
    return s * norm.cdf(d1) - k * math.exp(-r * t) * norm.cdf(d2)


def put(s: float, k: float, r: float, sigma: float, t: float) -> float:
    d1, d2 = d1_d2(s, k, r, sigma, t)
    return k * math.exp(-r * t) * norm.cdf(-d2) - s * norm.cdf(-d1)


def price(cp: str, s: float, k: float, r: float, sigma: float, t: float) -> float:
    """cp: 'C' 或 'P'。"""
    if cp.upper() == "C":
        return call(s, k, r, sigma, t)
    if cp.upper() == "P":
        return put(s, k, r, sigma, t)
    raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")
