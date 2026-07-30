"""M1 定價引擎：P-1 / P-2 golden fixtures + P-3 性質測試 + Greeks 有限差分對照。"""
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.fixtures import manual_fixtures as fx
from txolab.pricing import black76, bs
from txolab.pricing.greeks import finite_difference, greeks
from txolab.pricing.iv import implied_vol

# ---------------- P-1 / P-2 golden fixtures ----------------

def test_p1_black_scholes_standard():
    p = fx.P1
    assert bs.call(p["s"], p["k"], p["r"], p["sigma"], p["t"]) == pytest.approx(p["call"], abs=p["tol"])
    assert bs.put(p["s"], p["k"], p["r"], p["sigma"], p["t"]) == pytest.approx(p["put"], abs=p["tol"])


def test_p2_black76_standard_and_atm_parity():
    p = fx.P2
    c = black76.call(p["f"], p["k"], p["r"], p["sigma"], p["t"])
    q = black76.put(p["f"], p["k"], p["r"], p["sigma"], p["t"])
    assert c == pytest.approx(p["call"], abs=p["tol"])
    assert q == pytest.approx(p["put"], abs=p["tol"])
    assert c == pytest.approx(q, abs=1e-12)  # ATM on forward：call = put


# ---------------- P-3 性質測試 (hypothesis) ----------------

_f = st.floats(min_value=5000, max_value=40000)
_moneyness = st.floats(min_value=0.8, max_value=1.2)
_sigma = st.floats(min_value=0.05, max_value=1.5)
_t = st.floats(min_value=1 / 365, max_value=1.0)
_r = st.floats(min_value=0.0, max_value=0.05)


@settings(max_examples=200, deadline=None)
@given(f=_f, m=_moneyness, sigma=_sigma, t=_t, r=_r)
def test_put_call_parity(f, m, sigma, t, r):
    k = f * m
    c = black76.call(f, k, r, sigma, t)
    p = black76.put(f, k, r, sigma, t)
    assert c - p == pytest.approx(math.exp(-r * t) * (f - k), abs=1e-6 * f)


@settings(max_examples=200, deadline=None)
@given(f=_f, m=_moneyness, sigma=_sigma, t=_t, r=_r, cp=st.sampled_from(["C", "P"]))
def test_iv_round_trip(f, m, sigma, t, r, cp):
    k = f * m
    px = black76.price(cp, f, k, r, sigma, t)
    # 價格裡的「波動率資訊」在時間價值裡；時間價值低於浮點雜訊時 IV 本就不可反推
    time_value = px - black76.intrinsic(cp, f, k, r, t)
    if time_value < 1e-6 * f:
        return
    res = implied_vol(cp, px, f, k, r, t)
    assert res.ok, f"IV 反推失敗: {res.reason}"
    reprice = black76.price(cp, f, k, r, res.iv, t)
    assert reprice == pytest.approx(px, abs=1e-6 * max(1.0, px))


@settings(max_examples=100, deadline=None)
@given(f=_f, m=_moneyness, sigma=_sigma, t=_t, r=_r)
def test_monotonicity_and_vega_positive(f, m, sigma, t, r):
    k = f * m
    bump = f * 1e-3
    # Call 對 F 遞增、對 K 遞減
    assert black76.call(f + bump, k, r, sigma, t) >= black76.call(f, k, r, sigma, t)
    assert black76.call(f, k + bump, r, sigma, t) <= black76.call(f, k, r, sigma, t)
    # vega ≥ 0
    assert greeks("C", f, k, r, sigma, t).vega >= 0
    assert greeks("P", f, k, r, sigma, t).vega >= 0


# ---------------- Greeks closed form vs 有限差分 (tolerance 1e-6) ----------------

@pytest.mark.parametrize("cp", ["C", "P"])
@pytest.mark.parametrize("f,k,sigma,t", [
    (22000.0, 22000.0, 0.20, 30 / 365),   # ATM、月契約尺度
    (22000.0, 22600.0, 0.25, 10 / 365),   # 價外、短天期
    (22000.0, 21000.0, 0.35, 90 / 365),   # 價內、季月尺度
    (100.0, 100.0, 0.20, 1.0),            # 教科書尺度
])
def test_greeks_match_finite_difference(cp, f, k, sigma, t):
    r = 0.015
    a = greeks(cp, f, k, r, sigma, t)
    n = finite_difference(cp, f, k, r, sigma, t)
    scale = max(1.0, f)  # 大指數尺度下用相對容差，等價教科書尺度的 1e-6 絕對容差
    assert a.delta == pytest.approx(n.delta, abs=1e-6)
    assert a.gamma == pytest.approx(n.gamma, abs=1e-6)
    assert a.vega == pytest.approx(n.vega, abs=1e-6 * scale)
    assert a.theta == pytest.approx(n.theta, abs=1e-6 * scale)
    assert a.rho == pytest.approx(n.rho, abs=1e-6 * scale)


# ---------------- IV solver 無解情境：標記原因、不丟例外 ----------------

def test_iv_below_intrinsic_returns_none():
    # 深度價內 call：市場價低於折現內含價值 → below_intrinsic
    res = implied_vol("C", 900.0, 22000.0, 21000.0, 0.015, 30 / 365)
    assert res.iv is None and res.reason == "below_intrinsic"


def test_iv_expired_returns_none():
    res = implied_vol("C", 50.0, 22000.0, 22000.0, 0.015, 0.0)
    assert res.iv is None and res.reason == "expired"


def test_iv_absurd_price_returns_none():
    # 價格高於理論上限 e^{-rT}·F → above_upper_bound
    res = implied_vol("C", 30000.0, 22000.0, 22000.0, 0.015, 30 / 365)
    assert res.iv is None and res.reason == "above_upper_bound"


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        black76.call(-1, 22000, 0.01, 0.2, 0.1)
    with pytest.raises(ValueError):
        black76.call(22000, 22000, 0.01, -0.2, 0.1)
    with pytest.raises(ValueError):
        black76.call(22000, 22000, 0.01, 0.2, 0.0)
    with pytest.raises(ValueError):
        black76.price("X", 22000, 22000, 0.01, 0.2, 0.1)
