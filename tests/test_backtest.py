"""M6 回測：criteria 判定、價差保證金、引擎機制（成交/停損/強制平倉/口數上限/
確定性）。合成行情一律用 black76 以已知 σ 產生——引擎不知道 σ，只看得到價格。"""
import datetime as dt
from decimal import Decimal

import pytest

from txolab.backtest.criteria import INITIAL_EQUITY, evaluate
from txolab.backtest.engine import EngineConfig, _fill, run_backtest
from txolab.backtest.strategies import IronCondor, ShortStrangle, VerticalSpread
from txolab.data.parser import FuturesRow, OptionRow
from txolab.data.store import Store
from txolab.margin.engine import vertical_spread_margin
from txolab.pricing import black76

R = 0.015
EXPIRY = dt.date(2026, 8, 19)  # 202608 月契約


def _cfg(slippage_mult: int = 1, initial: Decimal = INITIAL_EQUITY) -> EngineConfig:
    return EngineConfig(
        initial_equity=initial, slippage_ticks=2 * slippage_mult,
        tax_rate=Decimal("0.001"), fee_per_lot=Decimal(25),
        util_cap=0.30, max_lots=20, min_entry_dte=15, force_close_dte=2,
        r=R, margin_a=Decimal(169000), margin_b=Decimal(85000), margin_c=Decimal(17000))


def _insert_day(store: Store, d: dt.date, f: float, sigma: float,
                code: str = "202608", expiry: dt.date = EXPIRY) -> None:
    t = (expiry - d).days / 365.0
    rows = []
    lo = int(f * 0.85) // 100 * 100
    hi = int(f * 1.15) // 100 * 100
    for k in range(lo, hi + 1, 100):
        for cp in ("C", "P"):
            px = round(black76.price(cp, f, k, R, sigma, t), 1)
            rows.append(OptionRow(d, "TXO", code, float(k), cp,
                                  None, None, None, None, max(px, 0.1), 100, 10, "一般"))
    store.upsert_options(rows)
    store.upsert_futures([FuturesRow(d, "TX", code, None, None, None, f, f, 1, 1, "一般")])


# ---------------- criteria（判定邏輯本身；門檻數字受鐵律 2 保護不另測值） ----------------

def test_criteria_all_pass():
    rep = evaluate("x", 35, Decimal(500), 0.12, 0.28, Decimal(10000))
    assert rep.passed and all(c.passed for c in rep.checks)


@pytest.mark.parametrize("kw,fail_name", [
    ({"n_closed": 29}, "樣本數"),
    ({"avg_net_twd": Decimal(0)}, "期望值"),
    ({"max_drawdown": 0.21}, "MDD"),
    ({"peak_utilization": 0.31}, "保證金"),
    ({"total_net_2x_slippage": Decimal(-1)}, "滑價"),
])
def test_criteria_each_dimension_fails(kw, fail_name):
    base = {"n_closed": 35, "avg_net_twd": Decimal(500), "max_drawdown": 0.12,
            "peak_utilization": 0.28, "total_net_2x_slippage": Decimal(10000)}
    base.update(kw)
    rep = evaluate("x", **base)
    assert not rep.passed
    assert any(fail_name in c.name and not c.passed for c in rep.checks)


# ---------------- 價差保證金與成交價 ----------------

def test_vertical_spread_margin_is_strike_diff():
    assert vertical_spread_margin(22000, 21600) == Decimal(20000)
    assert vertical_spread_margin(21600, 22000) == Decimal(20000)


def test_fill_slippage_and_floor():
    assert _fill(100.0, +1, 2) == 102.0   # 買進付高（tick=1）
    assert _fill(100.0, -1, 2) == 98.0    # 賣出收低
    assert _fill(0.2, -1, 2) == 0.1       # 地板：不出現 0 或負價


# ---------------- 引擎端到端 ----------------

def _quiet_market(store: Store, sigma: float = 0.20) -> list[dt.date]:
    """7/20 進場（dte=30）→ 盤整 → 8/17（dte=2）強制平倉的無事故行情。"""
    days = [dt.date(2026, 7, 20), dt.date(2026, 7, 27),
            dt.date(2026, 8, 3), dt.date(2026, 8, 10), dt.date(2026, 8, 17)]
    for d in days:
        _insert_day(store, d, 22000.0, sigma)
    return days


def test_strangle_theta_decay_and_force_close(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _quiet_market(store)
        strat = ShortStrangle(0.25, 2.0, 0.45)
        res = run_backtest(store, strat, _cfg())
    assert res.n_closed == 1
    t = res.trades[0]
    assert t.reason == "expiry_week" and t.open_date == dt.date(2026, 7, 20)
    assert t.close_date == dt.date(2026, 8, 17)
    assert t.net_twd > 0                     # 盤整市：收足時間價值，扣成本後仍貺
    assert t.costs_twd > 0
    assert res.peak_utilization <= 0.35      # 進場口數以佔用上限決定
    assert res.equity_curve[-1][1] == INITIAL_EQUITY + t.net_twd


def test_stop_loss_on_vol_explosion(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _insert_day(store, dt.date(2026, 7, 20), 22000.0, 0.20)
        _insert_day(store, dt.date(2026, 7, 22), 22000.0, 0.90)  # 波動率事件
        _insert_day(store, dt.date(2026, 7, 24), 22000.0, 0.90)
        strat = ShortStrangle(0.25, 2.0, 0.45)
        res = run_backtest(store, strat, _cfg())
    assert res.n_closed >= 1
    t = res.trades[0]
    assert t.reason == "stop" and t.close_date == dt.date(2026, 7, 22)
    assert t.net_twd < 0                     # 停損就是認賠，不裝沒事
    # 停損當日不得回頭再進場（冷靜規則）
    assert all(x.open_date != dt.date(2026, 7, 22) for x in res.trades)


def test_credit_spreads_open_with_capped_size(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _quiet_market(store)
        for strat in (VerticalSpread(0.25, 0.10, 2.0), IronCondor(0.20, 0.10, 2.0)):
            res = run_backtest(store, strat, _cfg())
            assert res.n_closed == 1, strat.name
            assert res.trades[0].lots >= 1
            assert res.peak_utilization <= 0.35, strat.name


def test_entry_skipped_when_margin_exceeds_budget(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _quiet_market(store)
        # 10 萬資金 × 30% = 3 萬 < 勒式每口保證金 → 連 1 口都開不了
        res = run_backtest(store, ShortStrangle(0.25, 2.0, 0.45),
                           _cfg(initial=Decimal(100_000)))
    assert res.n_closed == 0 and res.skipped_entries >= 1


def test_backtest_is_bit_identical_on_rerun(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _quiet_market(store)
        strat = ShortStrangle(0.25, 2.0, 0.45)
        a = run_backtest(store, strat, _cfg())
        b = run_backtest(store, strat, _cfg())
    assert a.trades == b.trades
    assert a.equity_curve == b.equity_curve
    assert a.utilization_curve == b.utilization_curve


def test_end_close_falls_back_when_last_day_unusable(tmp_path):
    """最後一天只有選擇權、沒有 TX（timer 搜先於期交所上架的真實情境）——
    期末平倉要往回退到最後一個可評價日，不可炸掉。"""
    with Store(tmp_path / "t.sqlite") as store:
        _insert_day(store, dt.date(2026, 7, 20), 22000.0, 0.20)
        _insert_day(store, dt.date(2026, 7, 27), 22000.0, 0.20)
        # 7/31：只有選擇權列，futures_daily 空 → build_chain 會 ChainError
        d = dt.date(2026, 7, 31)
        t = (EXPIRY - d).days / 365.0
        rows = [OptionRow(d, "TXO", "202608", float(k), cp, None, None, None, None,
                          max(round(black76.price(cp, 22000.0, k, R, 0.20, t), 1), 0.1),
                          100, 10, "一般")
                for k in range(21000, 23001, 100) for cp in ("C", "P")]
        store.upsert_options(rows)
        res = run_backtest(store, ShortStrangle(0.25, 2.0, 0.45), _cfg())
    assert res.n_closed == 1
    assert res.trades[0].reason == "end"
    assert res.trades[0].close_date == dt.date(2026, 7, 27)  # 退回最後可評價日


# ---------------- B1 買方基準：debit 部位、預算制 sizing、IV rank 閘門 ----------------

def _b1(window: int = 3):
    from txolab.backtest.strategies import LongStrangleLowIV
    return LongStrangleLowIV(buy_delta=0.25, iv_rank_max=30.0, iv_rank_window=window,
                             premium_budget=0.05, take_profit_mult=2.0, stop_value_frac=0.5)


def test_long_strangle_waits_for_window_and_low_rank(tmp_path):
    """滿窗口前不進場；rank 高檔（IV 相對窗口在高點）也不進場。"""
    with Store(tmp_path / "t.sqlite") as store:
        # 3 天窗口，但 IV 一路走高 → 第 3 天 rank=100 → 永不進場
        for i, sig in enumerate([0.15, 0.20, 0.30, 0.35]):
            _insert_day(store, dt.date(2026, 7, 20) + dt.timedelta(days=i), 22000.0, sig)
        res = run_backtest(store, _b1(window=3), _cfg())
    assert res.n_closed == 0 and res.skipped_entries == 0  # 閘門擋下，不算 skip


def test_long_strangle_take_profit_on_vol_spike(tmp_path):
    """IV 走低到 rank=0 進場 → 隔日波動率爆發 → 市值 >= 2 倍停利。"""
    with Store(tmp_path / "t.sqlite") as store:
        days = [(dt.date(2026, 7, 20), 0.30), (dt.date(2026, 7, 21), 0.25),
                (dt.date(2026, 7, 22), 0.20),   # rank=0 → 進場
                (dt.date(2026, 7, 24), 0.55)]   # 波動率爆發 → 停利
        for d, sig in days:
            _insert_day(store, d, 22000.0, sig)
        res = run_backtest(store, _b1(window=3), _cfg())
    assert res.n_closed == 1
    t = res.trades[0]
    assert t.open_date == dt.date(2026, 7, 22) and t.reason == "take_profit"
    assert t.net_twd > 0
    assert t.entry_credit_points < 0            # debit：進場為淨付權利金
    assert res.peak_utilization == 0.0          # 買方無保證金
    # 預算制 sizing：權利金支出 <= 權益 5%
    assert abs(t.entry_credit_points) * 50 * t.lots <= float(INITIAL_EQUITY) * 0.05


def test_long_strangle_stop_on_theta_bleed(tmp_path):
    """進場後波動率塌掉 → 市值 <= 50% 停損認賠。"""
    with Store(tmp_path / "t.sqlite") as store:
        days = [(dt.date(2026, 7, 20), 0.30), (dt.date(2026, 7, 21), 0.25),
                (dt.date(2026, 7, 22), 0.20),   # rank=0 → 進場
                (dt.date(2026, 7, 24), 0.08)]   # IV 塌掉 → 市值腰斬
        for d, sig in days:
            _insert_day(store, d, 22000.0, sig)
        res = run_backtest(store, _b1(window=3), _cfg())
    assert res.n_closed == 1
    t = res.trades[0]
    assert t.reason == "stop" and t.net_twd < 0


def test_double_slippage_hurts(tmp_path):
    with Store(tmp_path / "t.sqlite") as store:
        _quiet_market(store)
        strat = ShortStrangle(0.25, 2.0, 0.45)
        r1 = run_backtest(store, strat, _cfg(1))
        r2 = run_backtest(store, strat, _cfg(2))
    assert r2.total_net < r1.total_net       # 滑價加倍 → 淨損益必然變差
