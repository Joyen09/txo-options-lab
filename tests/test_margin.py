"""M5 保證金引擎：M-1 exact match、M-2 深度價外邊界、跨式 ≤ 兩單邊之和。"""
from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies as st

from txolab.margin.engine import (
    deep_otm_multiplier,
    otm_value,
    single_seller_margin,
    strangle_margin,
    transaction_cost,
)

from tests.fixtures import manual_fixtures as fx


# ---------------- M-1 官方公式範例 (exact) ----------------

def test_m1_single_seller_margin_exact():
    p = fx.M1
    assert otm_value(p["cp"], p["index"], p["strike"]) == Decimal(10000)
    m = single_seller_margin(p["cp"], p["index"], p["strike"], p["premium"], p["a"], p["b"])
    assert m == Decimal(p["margin"])  # 87,750 整，一元不差


def test_b_floor_applies_when_deep_otm():
    # 價外值大到 A−價外值 < B 時，改收 B (地板)
    # 價外 1,900 點 → 價外值 95,000 > A=96,000−95,000=1,000 → 用 B
    # 但價外 ≥1,000 點 → A、B ×1.5：MAX(96,000×1.5−95,000, 48,000×1.5) = 72,000
    m = single_seller_margin("C", 22000, 23900, 5, 96000, 48000)
    assert m == Decimal(5 * 50) + Decimal(72000)


# ---------------- M-2 深度價外加收邊界 ----------------

@pytest.mark.parametrize("points,mult", fx.M2)
def test_m2_deep_otm_multiplier_boundaries(points, mult):
    assert deep_otm_multiplier(points) == Decimal(mult)


@settings(max_examples=200, deadline=None)
@given(points=st.integers(min_value=0, max_value=3000))
def test_m2_multiplier_is_step_function(points):
    m = deep_otm_multiplier(points)
    if points < 500:
        assert m == Decimal("1.0")
    elif points < 1000:
        assert m == Decimal("1.2")
    else:
        assert m == Decimal("1.5")


# ---------------- 跨式/勒式 ----------------

def test_strangle_margin_le_sum_of_singles():
    idx, a, b, c = 22000, 96000, 48000, 5000
    call_k, call_prem = 22400, Decimal("30")
    put_k, put_prem = 21600, Decimal("42")
    m_str = strangle_margin(idx, call_k, call_prem, put_k, put_prem, a, b, c)
    m_call = single_seller_margin("C", idx, call_k, call_prem, a, b)
    m_put = single_seller_margin("P", idx, put_k, put_prem, a, b)
    assert m_str <= m_call + m_put
    # 組成驗證：MAX 邊 + 弱邊權利金市值 + C
    assert m_str == max(m_call, m_put) + min(call_prem, put_prem) * 50 + c


@settings(max_examples=100, deadline=None)
@given(
    call_off=st.integers(min_value=0, max_value=1500),
    put_off=st.integers(min_value=0, max_value=1500),
    call_prem=st.integers(min_value=1, max_value=500),
    put_prem=st.integers(min_value=1, max_value=500),
)
def test_strangle_margin_property(call_off, put_off, call_prem, put_prem):
    """C 不超過 B 的前提下，跨式保證金恆 ≤ 兩單邊之和 (期交所設計的折讓)。"""
    idx, a, b, c = 22000, 96000, 48000, 30000
    m_str = strangle_margin(idx, idx + call_off, call_prem, idx - put_off, put_prem, a, b, c)
    m_sum = (single_seller_margin("C", idx, idx + call_off, call_prem, a, b)
             + single_seller_margin("P", idx, idx - put_off, put_prem, a, b))
    assert m_str <= m_sum


# ---------------- 交易成本 ----------------

def test_transaction_cost_tax_plus_fee():
    # 權利金 100 點 = NT$5,000；稅率 0.1% → 稅 NT$5；手續費 NT$25 → 合計 NT$30
    assert transaction_cost(100, "0.001", 25) == Decimal(30)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        single_seller_margin("C", 22000, 22200, -1, 96000, 48000)
    with pytest.raises(ValueError):
        deep_otm_multiplier(-1)
    with pytest.raises(ValueError):
        transaction_cost(100, "0.001", 25, lots=0)
