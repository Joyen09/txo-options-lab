"""Black-76 Greeks：closed form + 有限差分對照。

金融意義（賣方視角尤其重要）：
- delta：F 動 1 點，權利金動幾點 → 避險要買賣多少口期貨
- gamma：delta 本身變多快 → 接近到期的價平序列 gamma 爆大，避險成本失控的來源
- vega：隱波動 1 個百分點的損益 → 賣方最怕「波動率事件」
- theta：時間流逝的損益 → 賣方的收入來源，但跟 gamma 是一體兩面
- rho：折現率敏感度，短天期 TXO 幾乎可忽略

theta 採「日曆衰減」慣例：theta = −∂Price/∂T（買方通常為負）。
rho = −T × Price（Black-76 的 r 只出現在折現項，∂Price/∂r = −T·Price）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

from . import black76


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float   # 對「σ 變動 1.0 (=100 個百分點)」的敏感度；除以 100 = 每 1% 的損益
    theta: float  # 每「年」的時間衰減；除以 365 = 每日衰減
    rho: float


def greeks(cp: str, f: float, k: float, r: float, sigma: float, t: float) -> Greeks:
    d1, _ = black76.d1_d2(f, k, sigma, t)
    disc = math.exp(-r * t)
    pdf1 = norm.pdf(d1)
    sqrt_t = math.sqrt(t)

    if cp.upper() == "C":
        delta = disc * norm.cdf(d1)
        px = black76.call(f, k, r, sigma, t)
    elif cp.upper() == "P":
        delta = -disc * norm.cdf(-d1)
        px = black76.put(f, k, r, sigma, t)
    else:
        raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")

    gamma = disc * pdf1 / (f * sigma * sqrt_t)
    vega = disc * f * pdf1 * sqrt_t
    # theta = −dPrice/dT；推導見測試 (與有限差分對照)。call/put 只差在折現項 r·Price。
    theta = r * px - disc * f * pdf1 * sigma / (2.0 * sqrt_t)
    rho = -t * px
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)


# ---------------- 有限差分對照 (測試用的第二種算法) ----------------

def finite_difference(cp: str, f: float, k: float, r: float, sigma: float, t: float,
                      rel_h: float = 1e-4) -> Greeks:
    """用中央差分數值微分重算全部 Greeks，跟 closed form 對照抓公式錯誤。"""
    p = black76.price
    hf = f * rel_h
    hs = max(sigma * rel_h, 1e-7)
    ht = t * rel_h
    hr = 1e-6

    delta = (p(cp, f + hf, k, r, sigma, t) - p(cp, f - hf, k, r, sigma, t)) / (2 * hf)
    gamma = (p(cp, f + hf, k, r, sigma, t) - 2 * p(cp, f, k, r, sigma, t)
             + p(cp, f - hf, k, r, sigma, t)) / (hf * hf)
    vega = (p(cp, f, k, r, sigma + hs, t) - p(cp, f, k, r, sigma - hs, t)) / (2 * hs)
    theta = -(p(cp, f, k, r, sigma, t + ht) - p(cp, f, k, r, sigma, t - ht)) / (2 * ht)
    rho = (p(cp, f, k, r + hr, sigma, t) - p(cp, f, k, r - hr, sigma, t)) / (2 * hr)
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
