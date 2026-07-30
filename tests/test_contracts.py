"""M2 合約與行事曆：C-1～C-5 golden fixtures + 假日順延 + 履約價階梯性質。"""
import datetime as dt
from decimal import Decimal

import pytest

from txolab.contracts import calendar as cal
from txolab.contracts import series, settlement, spec

from tests.fixtures import manual_fixtures as fx


# ---------------- C-1 Tick 表 ----------------

@pytest.mark.parametrize("premium,tick", fx.C1)
def test_c1_tick_table(premium, tick):
    assert spec.tick_size(premium) == tick


def test_tick_boundaries():
    # 段位邊界：下限含、上限不含
    assert spec.tick_size(9.9) == 0.1
    assert spec.tick_size(10.0) == 0.5
    assert spec.tick_size(49.5) == 0.5
    assert spec.tick_size(50.0) == 1.0
    assert spec.tick_size(999.0) == 5.0
    assert spec.tick_size(1000.0) == 10.0


# ---------------- C-2 點值換算 ----------------

@pytest.mark.parametrize("points,twd", fx.C2)
def test_c2_premium_to_twd(points, twd):
    assert spec.premium_to_twd(points) == Decimal(twd)


# ---------------- C-3 月契約到期日 ----------------

@pytest.mark.parametrize("ym,expiry", fx.C3)
def test_c3_monthly_expiry(ym, expiry):
    y, m = ym
    assert cal.monthly_expiry(y, m) == dt.date.fromisoformat(expiry)


def test_monthly_expiry_holiday_rolls_forward():
    # 合成假日：第 3 個星期三放假 → 順延次一營業日 (週四)
    holidays = frozenset({dt.date(2026, 7, 15)})
    assert cal.monthly_expiry(2026, 7, holidays) == dt.date(2026, 7, 16)
    # 連續假日跨過週末 → 順延到下週一
    holidays2 = frozenset({dt.date(2026, 7, 15), dt.date(2026, 7, 16), dt.date(2026, 7, 17)})
    assert cal.monthly_expiry(2026, 7, holidays2) == dt.date(2026, 7, 20)


# ---------------- C-4 週契約加掛規則 ----------------

def test_c4_first_wednesday_no_listing():
    d = dt.date.fromisoformat(fx.C4_WED_NO_LISTING)
    assert cal.weekly_wed_listing(d) is None


def test_c4_second_wednesday_lists_plus_two_weeks():
    d, expiry = (dt.date.fromisoformat(x) for x in fx.C4_WED_LISTING)
    assert cal.weekly_wed_listing(d) == expiry


def test_c4_friday_lists_plus_two_weeks():
    d, expiry = (dt.date.fromisoformat(x) for x in fx.C4_FRI_LISTING)
    assert cal.weekly_fri_listing(d) == expiry


def test_listing_on_wrong_weekday_raises():
    with pytest.raises(ValueError):
        cal.weekly_wed_listing(dt.date(2026, 7, 2))  # 週四
    with pytest.raises(ValueError):
        cal.weekly_fri_listing(dt.date(2026, 7, 1))  # 週三


def test_active_expiries_structure():
    """2026-07-30 (週四) 的存續清單：3 近月 + 2 季月 + 存活週契約，全部 >= 今天且不重複。"""
    today = dt.date(2026, 7, 30)
    exps = cal.active_expiries(today)
    kinds = [e.kind for e in exps]
    assert kinds.count("monthly") == 3
    assert kinds.count("quarterly") == 2
    dates = [e.date for e in exps]
    assert all(d >= today for d in dates)
    assert len(dates) == len(set(dates))  # 不重複
    # 近月應為 8/9/10 月的第 3 個星期三；接續季月 = 12 月與次年 3 月
    monthly = [e.date for e in exps if e.kind == "monthly"]
    assert monthly == [dt.date(2026, 8, 19), dt.date(2026, 9, 16), dt.date(2026, 10, 21)]
    quarterly = [e.date for e in exps if e.kind == "quarterly"]
    assert quarterly == [dt.date(2026, 12, 16), dt.date(2027, 3, 17)]
    # 週契約：7/22(三)掛的 8/5、7/29(三)掛的 8/12；7/17(五)掛的 7/31、7/24(五)掛的 8/7
    weekly = {e.date for e in exps if e.kind.startswith("weekly")}
    assert dt.date(2026, 7, 31) in weekly
    assert dt.date(2026, 8, 5) in weekly
    assert dt.date(2026, 8, 7) in weekly
    assert dt.date(2026, 8, 12) in weekly


# ---------------- C-5 最後結算價與到期損益 ----------------

def test_c5_final_settlement_simple_average():
    assert settlement.final_settlement_price(fx.C5_SAMPLES) == Decimal(fx.C5_SETTLEMENT)


def test_c5_expiry_payoff():
    p = fx.C5_PAYOFF
    assert settlement.expiry_payoff_twd(p["cp"], p["strike"], Decimal(p["settlement"])) \
        == Decimal(p["twd"])


def test_otm_expiry_payoff_is_zero():
    assert settlement.expiry_payoff_twd("C", 22100, Decimal("22015.00")) == 0
    assert settlement.expiry_payoff_twd("P", 21900, Decimal("22015.00")) == 0


# ---------------- 履約價階梯 (規則性質；無官方 fixture，Phase 3 拿行情檔回驗) ----------------

def test_strike_ladder_spacing_and_coverage():
    base = 22000.0
    weekly = series.strikes(base, "weekly")
    assert all(k % 100 == 0 for k in weekly)
    assert min(weekly) >= base * 0.90 and max(weekly) <= base * 1.10
    quarterly = series.strikes(base, "quarterly")
    assert all(k % 200 == 0 for k in quarterly)
    assert min(quarterly) >= base * 0.80 and max(quarterly) <= base * 1.20


def test_fine_series_only_within_3pct():
    base = 22000.0
    fine = series.strikes(base, "weekly", with_fine=True)
    odd_50 = [k for k in fine if k % 100 == 50]  # 細密序列特有的 50 點階
    assert odd_50, "±3% 內應有 50 點細密序列"
    assert all(base * 0.97 <= k <= base * 1.03 for k in odd_50)


def test_low_index_regime_raises():
    with pytest.raises(ValueError):
        series.strikes(2500.0, "weekly")
