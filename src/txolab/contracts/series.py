"""履約價階梯 (strike ladder)。

規則 (指數 ≥3,000 點時，SPEC 第 2 節)：
- 基本間距：週契約與 3 近月 100 點；季月 200 點
- 序列涵蓋：週契約 ±10%、近月 ±15%、季月 ±20% (以前一日標的收盤價為基準)
- 細密序列：週契約自掛牌起、近月自「到期前二週之星期三」起，
  於前一日收盤價 ±3% 內加掛間距減半 (50 點) 之序列

本模組只管「照規則產生應有的階梯」；實際掛牌以行情檔為準 (Phase 3 回驗)。
"""
from __future__ import annotations

SPACING = {"weekly": 100, "monthly": 100, "quarterly": 200}
COVERAGE = {"weekly": 0.10, "monthly": 0.15, "quarterly": 0.20}
FINE_BAND = 0.03  # ±3% 細密序列
MIN_INDEX_FOR_RULES = 3000  # 本表僅適用指數 ≥3,000；更低的歷史區間需另查舊規則


def strikes(base_price: float, kind: str, with_fine: bool = False) -> list[int]:
    """依前一日收盤價產生履約價清單 (由低到高)。

    kind: 'weekly' / 'monthly' / 'quarterly'
    with_fine: 是否加入 ±3% 內的 50 點細密序列
      (週契約自掛牌即有；近月要到「到期前二週之星期三」後才有——由呼叫端判斷時點)
    """
    if kind not in SPACING:
        raise ValueError(f"kind 必須是 weekly/monthly/quarterly: {kind!r}")
    if base_price < MIN_INDEX_FOR_RULES:
        raise ValueError(
            f"指數 {base_price} < {MIN_INDEX_FOR_RULES}，本間距表不適用 (需查期交所低指數區間規則)")

    spacing = SPACING[kind]
    cov = COVERAGE[kind]
    lo, hi = base_price * (1 - cov), base_price * (1 + cov)

    # 基本階梯：涵蓋範圍內所有 spacing 的整數倍
    first = int(lo // spacing) * spacing
    if first < lo:
        first += spacing
    out = set(range(first, int(hi // spacing) * spacing + 1, spacing))

    if with_fine:
        f_lo, f_hi = base_price * (1 - FINE_BAND), base_price * (1 + FINE_BAND)
        fine = spacing // 2
        first = int(f_lo // fine) * fine
        if first < f_lo:
            first += fine
        out |= set(range(first, int(f_hi // fine) * fine + 1, fine))

    return sorted(out)
