"""M3 資料層：parser 嚴格驗證、到期代碼、store 冪等、chain 組裝與 forward 對齊。

⚠️ 這裡的 CSV 是「合成樣本」——依公開文件的已知欄名產生，用來測 parser 的
機制（欄名尋找、防呆、型別轉換）。格式是否與期交所現行檔案吻合，
必須在 VM 上用 `txolab fetch` 對真實檔案驗證（鐵律 6）；吻合前不可跑回測。
"""
import datetime as dt

import pytest

from txolab.data import store as store_mod
from txolab.data.chain import build_chain
from txolab.data.parser import (
    ParserError,
    expiry_code_to_date,
    parse_futures_csv,
    parse_options_csv,
)

OPT_HEADER = ("交易日期,契約,到期月份(週別),履約價,買賣權,開盤價,最高價,最低價,收盤價,"
              "成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,交易時段")
FUT_HEADER = ("交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
              "盤後交易時段成交量,一般交易時段成交量,合計成交量,未沖銷契約數,結算價,交易時段")


def _opt_csv(rows: list[str]) -> str:
    return OPT_HEADER + "\n" + "\n".join(rows) + "\n"


def _fut_csv(rows: list[str]) -> str:
    return FUT_HEADER + "\n" + "\n".join(rows) + "\n"


# ---------------- parser：機制與防呆 ----------------

def test_parse_options_basic_and_dash_as_none():
    text = _opt_csv([
        "2026/07/29,TXO,202608,22000,買權,310,330,300,325,1234,326,5678,324,326,一般",
        "2026/07/29,TXO,202608,22000,賣權,-,-,-,-,0,295,100,-,-,一般",
        "2026/07/29,TXO,202608,22000,買權,300,310,295,305,50,306,5600,-,-,盤後",
        "2026/07/29,TEO,202608,900,買權,10,10,10,10,5,10,10,-,-,一般",  # 非 TXO 要濾掉
    ])
    rows = parse_options_csv(text)
    assert len(rows) == 3  # TEO 被濾掉
    r0 = rows[0]
    assert r0.trade_date == dt.date(2026, 7, 29)
    assert (r0.expiry_code, r0.strike, r0.cp) == ("202608", 22000.0, "C")
    assert r0.settlement == 326 and r0.volume == 1234 and r0.oi == 5678
    r1 = rows[1]
    assert r1.cp == "P" and r1.open is None and r1.close is None and r1.volume == 0
    assert rows[2].session == "盤後"


def test_parse_options_missing_column_fails_loud():
    bad = OPT_HEADER.replace("履約價,", "") + "\n2026/07/29,TXO,202608,買權,1,1,1,1,1,1,1,1,1,一般\n"
    with pytest.raises(ParserError, match="缺少必要欄位"):
        parse_options_csv(bad)


def test_parse_options_header_order_independent():
    # 欄序打亂也要能解析（用名稱找欄位，不用位置）
    text = ("契約,交易日期,買賣權,履約價,到期月份(週別),收盤價,開盤價,最高價,最低價,"
            "結算價,成交量,未沖銷契約數,交易時段\n"
            "TXO,2026/07/29,賣權,21800,202608,290,280,295,275,291,999,1111,一般\n")
    rows = parse_options_csv(text)
    assert rows[0].strike == 21800.0 and rows[0].cp == "P" and rows[0].close == 290


def test_parse_options_unknown_cp_fails():
    text = _opt_csv(["2026/07/29,TXO,202608,22000,期貨,1,1,1,1,1,1,1,-,-,一般"])
    with pytest.raises(ParserError, match="買賣權"):
        parse_options_csv(text)


def test_parse_futures_prefers_total_volume():
    text = _fut_csv([
        "2026/07/29,TX,202608,22900,23100,22850,23050,150,0.65,2000,98000,100000,80000,23060,一般",
        "2026/07/29,MTX,202608,22900,23100,22850,23050,150,0.65,1,1,2,3,23060,一般",
    ])
    rows = parse_futures_csv(text)
    assert len(rows) == 1  # 只留 TX
    assert rows[0].volume == 100000 and rows[0].settlement == 23060


# ---------------- 到期代碼 ----------------

def test_expiry_code_monthly_and_weekly():
    assert expiry_code_to_date("202607") == dt.date(2026, 7, 15)   # 第 3 個星期三 (C-3)
    assert expiry_code_to_date("202607W1") == dt.date(2026, 7, 1)  # 第 1 個星期三
    assert expiry_code_to_date("202607W4") == dt.date(2026, 7, 22)


def test_expiry_code_unknown_fails_loud():
    with pytest.raises(ParserError, match="未知到期代碼"):
        expiry_code_to_date("202607F2")  # 可能的週五代碼——拿到真實樣本前不猜


# ---------------- store：冪等 ----------------

def test_store_upsert_idempotent(tmp_path):
    text = _opt_csv([
        "2026/07/29,TXO,202608,22000,買權,310,330,300,325,1234,326,5678,-,-,一般",
    ])
    rows = parse_options_csv(text)
    with store_mod.Store(tmp_path / "t.sqlite") as s:
        assert s.upsert_options(rows) == 1
        assert s.upsert_options(rows) == 1  # 重跑同資料
        got = s.options_on(dt.date(2026, 7, 29))
        assert len(got) == 1 and got[0] == rows[0]  # bit-identical roundtrip


# ---------------- chain：forward 對齊 ----------------

def _mk_day(trade: str = "2026/07/29"):
    opt = _opt_csv([
        f"{trade},TXO,202608,22000,買權,310,330,300,325,1234,326,5678,-,-,一般",
        f"{trade},TXO,202608,22000,賣權,250,270,240,265,1200,266,4000,-,-,一般",
        f"{trade},TXO,202608W1,22000,買權,150,160,140,155,500,156,900,-,-,一般",
        f"{trade},TXO,202607,22000,買權,90,95,85,92,100,93,300,-,-,一般",  # 7/15 已到期
    ])
    fut = _fut_csv([
        f"{trade},TX,202608,22900,23100,22850,23050,150,0.65,2000,98000,100000,80000,23060,一般",
        f"{trade},TX,202609,22850,23000,22800,22950,140,0.6,1000,40000,41000,50000,22960,一般",
    ])
    return parse_options_csv(opt), parse_futures_csv(fut)


def test_chain_aligns_forward_and_drops_expired():
    opts, futs = _mk_day()
    ch = build_chain(dt.date(2026, 7, 29), opts, futs)
    codes = [sl.expiry_code for sl in ch.slices]
    assert "202607" not in codes           # 7/15 已到期 → 略過
    assert codes == ["202608W1", "202608"]  # 依到期日排序 (8/5 在 8/19 前)
    wk = ch.slices[0]
    assert wk.expiry == dt.date(2026, 8, 5)
    assert wk.forward == 23060 and wk.forward_code == "202608"  # 週選用最近月 TX
    mo = ch.slices[1]
    assert mo.expiry == dt.date(2026, 8, 19) and mo.forward == 23060
    assert mo.t_years == pytest.approx(21 / 365)
