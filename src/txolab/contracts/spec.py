"""TXO 契約基本規格：乘數、報價 tick 表、點值換算、漲跌幅。

Facts 來源：期交所 TXO 契約規格頁 (SPEC.md 第 2 節，查證日期 2026-07-30)。
規則若改版，改這裡並更新 SPEC 註記——不要散落在各處寫死。
"""
from __future__ import annotations

from decimal import Decimal

MULTIPLIER = 50  # 指數每點 NT$50

# 權利金報價單位 (五段)：(下限, 上限, tick)，上限 None = 無上限
_TICK_TABLE: list[tuple[float, float | None, float]] = [
    (0.0, 10.0, 0.1),      # <10 點：0.1 點 (NT$5)
    (10.0, 50.0, 0.5),     # 10–<50：0.5 點 (NT$25)
    (50.0, 500.0, 1.0),    # 50–<500：1 點 (NT$50)
    (500.0, 1000.0, 5.0),  # 500–<1,000：5 點 (NT$250)
    (1000.0, None, 10.0),  # ≥1,000：10 點 (NT$500)
]


def tick_size(premium_points: float) -> float:
    """給定權利金報價 (點)，回傳該價位帶的最小跳動點數。"""
    if premium_points < 0:
        raise ValueError(f"權利金不可為負: {premium_points}")
    for lo, hi, tick in _TICK_TABLE:
        if premium_points >= lo and (hi is None or premium_points < hi):
            return tick
    raise AssertionError("tick 表未涵蓋此價位")  # pragma: no cover


def premium_to_twd(points: float | Decimal, lots: int = 1) -> Decimal:
    """權利金點數 → 新台幣金額 (Decimal，對到元)。35 點 = NT$1,750。"""
    if lots <= 0:
        raise ValueError(f"口數必須為正: {lots}")
    return Decimal(str(points)) * MULTIPLIER * lots


def daily_price_limit(prev_taiex_close: float) -> float:
    """權利金單日最大漲跌點數 = 前一日 TAIEX 收盤價的 10%。"""
    if prev_taiex_close <= 0:
        raise ValueError(f"指數必須為正: {prev_taiex_close}")
    return prev_taiex_close * 0.10
