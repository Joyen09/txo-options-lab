"""Phase 6 模擬交易：帳本冪等性、與回測共用規則的一致性、不含下單路徑。

最重要的一條測試是 test_paper_matches_backtest_on_same_data——paper trade 與
回測跑同一份資料必須得到同一筆交易，否則「共用規則」只是說說而已。
"""
import ast
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from txolab.backtest.criteria import INITIAL_EQUITY
from txolab.backtest.engine import run_backtest
from txolab.data.store import Store
from txolab.paper.ledger import PaperLedger
from txolab.paper.runner import format_report, run_paper_day

from .test_backtest import _b3, _cfg, _insert_day


def _ledger(tmp_path: Path) -> PaperLedger:
    return PaperLedger(tmp_path / "paper.sqlite", INITIAL_EQUITY)


def _uptrend(store: Store) -> list[dt.date]:
    """與 test_backtest 的 B3 停利情境同一份行情。"""
    days = [(dt.date(2026, 7, 20), 22000.0), (dt.date(2026, 7, 21), 22150.0),
            (dt.date(2026, 7, 22), 22300.0),   # index > MA(3) → 進場
            (dt.date(2026, 7, 24), 23500.0)]   # 大漲 → 停利
    for d, f in days:
        _insert_day(store, d, f, 0.20)
    return [d for d, _ in days]


def test_paper_opens_then_takes_profit(tmp_path):
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = _uptrend(store)
        acts = [run_paper_day(store, led, _b3(ma=3), _cfg(), d).action for d in days]
        assert acts == ["flat", "flat", "open", "close"]
        trades = led.closed_trades()
        assert len(trades) == 1
        assert trades[0].reason == "take_profit" and trades[0].net_twd > 0
        assert led.open_position() is None
        assert led.cash == INITIAL_EQUITY + trades[0].net_twd


def test_paper_matches_backtest_on_same_data(tmp_path):
    """同一份行情：paper trade 的成交與損益必須與回測逐欄相同。"""
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = _uptrend(store)
        for d in days:
            run_paper_day(store, led, _b3(ma=3), _cfg(), d)
        bt = run_backtest(store, _b3(ma=3), _cfg())
        paper = led.closed_trades()
    assert len(paper) == len(bt.trades) == 1
    p, b = paper[0], bt.trades[0]
    assert (p.open_date, p.close_date, p.reason, p.lots) == (
        b.open_date, b.close_date, b.reason, b.lots)
    assert p.entry_credit_points == b.entry_credit_points
    assert p.exit_debit_points == b.exit_debit_points
    assert p.costs_twd == b.costs_twd
    assert p.net_twd == b.net_twd


def test_paper_is_idempotent_per_day(tmp_path):
    """同一交易日重跑不得重複開倉（timer 重試、手動補跑都會發生）。"""
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = _uptrend(store)
        for d in days[:3]:
            run_paper_day(store, led, _b3(ma=3), _cfg(), d)
        pos = led.open_position()
        assert pos is not None
        again = run_paper_day(store, led, _b3(ma=3), _cfg(), days[2])
        assert again.action == "hold" and "已處理過" in again.detail
        assert led.open_position() == pos          # 部位一字未動
        assert not led.closed_trades()


def test_paper_survives_restart(tmp_path):
    """帳本落地 sqlite：關掉再開，在倉部位與現金原樣還原。"""
    with Store(tmp_path / "t.sqlite") as store:
        days = _uptrend(store)
        with _ledger(tmp_path) as led:
            for d in days[:3]:
                run_paper_day(store, led, _b3(ma=3), _cfg(), d)
            before, cash = led.open_position(), led.cash
        with _ledger(tmp_path) as led2:                 # 重新開檔
            assert led2.open_position() == before
            assert led2.cash == cash
            day = run_paper_day(store, led2, _b3(ma=3), _cfg(), days[3])
    assert day.action == "close"


def test_paper_blocked_by_trend_gate(tmp_path):
    """下跌序列：閘門不開 → 全程空手、帳本零變動。"""
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = [dt.date(2026, 7, 20) + dt.timedelta(days=i) for i in range(4)]
        for d, f in zip(days, [23000.0, 22800.0, 22600.0, 22400.0], strict=True):
            _insert_day(store, d, f, 0.20)
        acts = [run_paper_day(store, led, _b3(ma=3), _cfg(), d).action for d in days]
        assert acts == ["flat"] * 4
        assert led.cash == INITIAL_EQUITY and not led.closed_trades()


def test_paper_report_states_simulation(tmp_path):
    """報告必須自己講明是模擬——這是要送進 Discord 給人看的。"""
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = _uptrend(store)
        day = run_paper_day(store, led, _b3(ma=3), _cfg(), days[0])
        msg = format_report(day, "bull_call_spread_trend")
    assert "模擬" in msg and "未下單" in msg


def test_ledger_refuses_second_position(tmp_path):
    with Store(tmp_path / "t.sqlite") as store, _ledger(tmp_path) as led:
        days = _uptrend(store)
        for d in days[:3]:
            run_paper_day(store, led, _b3(ma=3), _cfg(), d)
        pos = led.open_position()
        assert pos is not None
        with pytest.raises(ValueError, match="已有在倉部位"):
            led.record_open(pos, Decimal(0))


def test_paper_package_contains_no_order_placement():
    """鐵律 1 的機械化把關：paper/ 的**程式碼**不得出現任何下單路徑。

    只看 AST（真正會執行的識別字與 import），不看註解與 docstring——
    文件裡本來就要寫「沒有 place_order」，那不算違規。
    """
    src = Path(__file__).resolve().parent.parent / "src" / "txolab" / "paper"
    banned_names = {"place_order", "cancel_order", "update_order", "Order", "Contract"}
    banned_modules = {"shioaji"}
    files = list(src.rglob("*.py"))
    assert files, "找不到 paper 套件——這條把關不能因為路徑錯就靜默通過"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in banned_names, f"{path.name}: {node.id}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in banned_names, f"{path.name}: .{node.attr}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in banned_modules, f"{path.name}: {a.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned_modules, f"{path.name}"
