"""賣方保證金與交易成本引擎。

公式 (期交所策略基礎法，SPEC 第 2 節)：
  單一賣方保證金 = 權利金市值 + MAX(A值 − 價外值, B值)
    call 價外值 = MAX((履約價 − 標的指數) × 50, 0)
    put  價外值 = MAX((標的指數 − 履約價) × 50, 0)
  深度價外加收：價外 500–<1,000 點 → A、B 各 ×1.2；價外 ≥1,000 點 → ×1.5
  賣出跨式/勒式 = MAX(call 邊, put 邊保證金) + 權利金較少邊之市值 + C值

A/B/C 由期交所不定期公告 (config/margin.toml，附生效日期)，本模組只吃參數。
全部 Decimal——保證金差 1 元就可能差在斷頭線上。

為什麼盯保證金：賣方策略的死法幾乎都不是方向看錯，而是行情急走時
保證金需求暴增 (價外值縮水 + 深度價外加收跳檔)、追繳不及被強制平倉。
"""
from __future__ import annotations

from decimal import Decimal

from ..contracts.spec import MULTIPLIER

Num = int | float | str | Decimal


def _dec(x: Num) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def otm_points(cp: str, index: Num, strike: Num) -> Decimal:
    """價外「點數」(≥0)。深度價外加收的判斷基準。"""
    idx, k = _dec(index), _dec(strike)
    if cp.upper() == "C":
        return max(k - idx, Decimal(0))
    if cp.upper() == "P":
        return max(idx - k, Decimal(0))
    raise ValueError(f"cp 必須是 'C' 或 'P': {cp!r}")


def otm_value(cp: str, index: Num, strike: Num) -> Decimal:
    """價外值 (NT$) = 價外點數 × 50。"""
    return otm_points(cp, index, strike) * MULTIPLIER


def deep_otm_multiplier(points_otm: Num) -> Decimal:
    """深度價外加收倍率：<500 點 ×1.0；500–<1,000 ×1.2；≥1,000 ×1.5。"""
    p = _dec(points_otm)
    if p < 0:
        raise ValueError(f"價外點數不可為負: {p}")
    if p >= 1000:
        return Decimal("1.5")
    if p >= 500:
        return Decimal("1.2")
    return Decimal("1.0")


def single_seller_margin(cp: str, index: Num, strike: Num, premium_points: Num,
                         a: Num, b: Num) -> Decimal:
    """單一部位賣方保證金 = 權利金市值 + MAX(A×倍率 − 價外值, B×倍率)。

    a/b 傳「原始或維持」檔位皆可 (呼叫端決定情境)；深度價外倍率在此自動套用。
    """
    if _dec(premium_points) < 0:
        raise ValueError(f"權利金不可為負: {premium_points}")
    mult = deep_otm_multiplier(otm_points(cp, index, strike))
    a_adj, b_adj = _dec(a) * mult, _dec(b) * mult
    premium_value = _dec(premium_points) * MULTIPLIER
    return premium_value + max(a_adj - otm_value(cp, index, strike), b_adj)


def strangle_margin(index: Num, call_strike: Num, call_premium: Num,
                    put_strike: Num, put_premium: Num,
                    a: Num, b: Num, c: Num) -> Decimal:
    """賣出跨式/勒式保證金 = MAX(兩邊單一保證金) + 權利金較少邊之市值 + C值。

    比兩邊各收一份便宜——指數不可能同時往兩邊走，
    但 C 值補償「大行情下另一邊從價外變價內」的風險。
    """
    m_call = single_seller_margin("C", index, call_strike, call_premium, a, b)
    m_put = single_seller_margin("P", index, put_strike, put_premium, a, b)
    weaker_premium = min(_dec(call_premium), _dec(put_premium)) * MULTIPLIER
    return max(m_call, m_put) + weaker_premium + _dec(c)


# ---------------- 交易成本 ----------------

def transaction_cost(premium_points: Num, tax_rate: Num, fee_per_lot: Num,
                     lots: int = 1) -> Decimal:
    """單邊成本 = 期交稅 (權利金市值 × 稅率，四捨五入到元) + 手續費。

    稅率/手續費從 config/costs.toml 讀，開工時先對期交所費率表核對現值。
    """
    if lots <= 0:
        raise ValueError(f"口數必須為正: {lots}")
    premium_value = _dec(premium_points) * MULTIPLIER * lots
    tax = (premium_value * _dec(tax_rate)).quantize(Decimal(1))
    return tax + _dec(fee_per_lot) * lots
