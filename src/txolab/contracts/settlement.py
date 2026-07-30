"""最後結算價與到期損益。

規則：最後結算價 = 到期日收盤前 30 分鐘 (13:00–13:30) 標的指數之「簡單算術平均」。
歐式現金結算：價內自動履約，
  call 到期損益 = max(結算價 − K, 0) × 50
  put  到期損益 = max(K − 結算價, 0) × 50
金錢計算用 Decimal (CLAUDE.md 鐵律 4：要對到元)。
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from .spec import MULTIPLIER


def final_settlement_price(samples: Sequence[float]) -> Decimal:
    """13:00–13:30 的指數樣本 → 簡單算術平均，四捨五入到小數 2 位。"""
    if not samples:
        raise ValueError("結算樣本不可為空")
    total = sum(Decimal(str(s)) for s in samples)
    return (total / len(samples)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def expiry_payoff_twd(cp: str, strike: float, settlement: float | Decimal,
                      lots: int = 1) -> Decimal:
    """到期履約損益 (買方收到的金額，NT$)。賣方 = 付出同額。"""
    if lots <= 0:
        raise ValueError(f"口數必須為正: {lots}")
    k = Decimal(str(strike))
    s = Decimal(str(settlement)) if not isinstance(settlement, Decimal) else settlement
    if cp.upper() == "C":
        diff = max(s - k, Decimal(0))
    elif cp.upper() == "P":
        diff = max(k - s, Decimal(0))
    else:
        raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")
    return diff * MULTIPLIER * lots
