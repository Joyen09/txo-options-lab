"""Black-76 定價——本專案的預設模型。

TXO 標的是 TAIEX 現貨指數，但市場慣例以「同到期的臺股期貨 (TX) 價格」
作為 forward 輸入定價：股利與利率差已內含在期貨價裡，不必自己估。

  d1 = (ln(F/K) + 0.5σ²T) / (σ√T),  d2 = d1 − σ√T
  Call = e^(−rT) × (F·N(d1) − K·N(d2))
  Put  = e^(−rT) × (K·N(−d2) − F·N(−d1))

r 只影響折現；TXO 多為短天期，影響極小，config 給台幣短率近似值即可。
"""
from __future__ import annotations

import math

from scipy.stats import norm


def _validate(f: float, k: float, sigma: float, t: float) -> None:
    if f <= 0 or k <= 0:
        raise ValueError(f"forward 與履約價必須為正: F={f}, K={k}")
    if sigma <= 0:
        raise ValueError(f"波動率必須為正: sigma={sigma}")
    if t <= 0:
        raise ValueError(f"到期時間必須為正 (已到期請走結算損益): T={t}")


def d1_d2(f: float, k: float, sigma: float, t: float) -> tuple[float, float]:
    _validate(f, k, sigma, t)
    sig_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sigma**2 * t) / sig_sqrt_t
    return d1, d1 - sig_sqrt_t


def call(f: float, k: float, r: float, sigma: float, t: float) -> float:
    d1, d2 = d1_d2(f, k, sigma, t)
    return math.exp(-r * t) * (f * norm.cdf(d1) - k * norm.cdf(d2))


def put(f: float, k: float, r: float, sigma: float, t: float) -> float:
    d1, d2 = d1_d2(f, k, sigma, t)
    return math.exp(-r * t) * (k * norm.cdf(-d2) - f * norm.cdf(-d1))


def price(cp: str, f: float, k: float, r: float, sigma: float, t: float) -> float:
    """cp: 'C' 或 'P'。"""
    if cp.upper() == "C":
        return call(f, k, r, sigma, t)
    if cp.upper() == "P":
        return put(f, k, r, sigma, t)
    raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")


def intrinsic(cp: str, f: float, k: float, r: float, t: float) -> float:
    """折現後內含價值——歐式選擇權價格的理論下限 (IV solver 用來判斷無解)。"""
    payoff = max(f - k, 0.0) if cp.upper() == "C" else max(k - f, 0.0)
    return math.exp(-r * t) * payoff
