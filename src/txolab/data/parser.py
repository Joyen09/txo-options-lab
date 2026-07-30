"""期交所每日行情 CSV parser。

鐵律 6：parser 只依 data/samples 的真實檔案開發，禁止臆測欄位。
誠實聲明：本檔在連不上期交所的環境寫成，欄位「名稱」取自期交所公開文件
與社群 parser 的已知格式，尚未對過本 repo 自己抓的真實樣本。因此採嚴格防呆：

  - 必要欄位用「名稱」尋找（容許欄序不同、容許多出欄位），
    缺任何一欄 → raise ParserError 並列出實際表頭，絕不用位置猜
  - 數值空欄/「-」視為 None；解析失敗整列報錯（附行號與原始內容）
  - 到期代碼只認得已知格式（YYYYMM、YYYYMMWn），其餘 fail loud

第一次在 VM 拿到真實檔案時：`txolab fetch --date ...` 會下載 + 驗證 + 落地；
驗證不過就照錯誤訊息修 COLUMN_ALIASES / 到期代碼規則（真實樣本已存 data/samples/）。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass

from ..contracts.calendar import monthly_expiry, next_business_day, nth_weekday

WEDNESDAY, FRIDAY = 2, 4


class ParserError(ValueError):
    pass


# 必要欄位 → 可接受的表頭名稱（期交所欄名歷來有全形括號與空白差異，全部正規化後比對）
OPT_COLUMNS: dict[str, tuple[str, ...]] = {
    "trade_date": ("交易日期",),
    "contract": ("契約",),
    "expiry_code": ("到期月份(週別)", "到期月份（週別）"),
    "strike": ("履約價",),
    "cp": ("買賣權",),
    "open": ("開盤價",),
    "high": ("最高價",),
    "low": ("最低價",),
    "close": ("收盤價",),
    "volume": ("成交量",),
    "settlement": ("結算價",),
    "oi": ("未沖銷契約數",),
    "session": ("交易時段",),
}

FUT_COLUMNS: dict[str, tuple[str, ...]] = {
    "trade_date": ("交易日期",),
    "contract": ("契約",),
    "expiry_code": ("到期月份(週別)", "到期月份（週別）"),
    "open": ("開盤價",),
    "high": ("最高價",),
    "low": ("最低價",),
    "close": ("收盤價",),
    "volume": ("合計成交量", "成交量"),
    "settlement": ("結算價",),
    "oi": ("未沖銷契約數",),
    "session": ("交易時段",),
}

_CP_MAP = {"買權": "C", "賣權": "P", "call": "C", "put": "P", "c": "C", "p": "P"}


@dataclass(frozen=True)
class OptionRow:
    trade_date: dt.date
    contract: str
    expiry_code: str
    strike: float
    cp: str  # 'C' | 'P'
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settlement: float | None
    volume: int
    oi: int | None
    session: str  # '一般' | '盤後'


@dataclass(frozen=True)
class FuturesRow:
    trade_date: dt.date
    contract: str
    expiry_code: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settlement: float | None
    volume: int
    oi: int | None
    session: str


def _norm(s: str) -> str:
    return s.strip().lstrip("﻿").replace(" ", "").replace("（", "(").replace("）", ")")


def _index_columns(header: list[str], spec: dict[str, tuple[str, ...]]) -> dict[str, int]:
    normed = [_norm(h) for h in header]
    out: dict[str, int] = {}
    missing: list[str] = []
    for field, aliases in spec.items():
        for alias in aliases:
            if _norm(alias) in normed:
                out[field] = normed.index(_norm(alias))
                break
        else:
            missing.append(f"{field}({aliases[0]})")
    if missing:
        raise ParserError(
            f"CSV 缺少必要欄位: {', '.join(missing)}；實際表頭: {header}。"
            "格式可能改版——用 data/samples/ 的原始檔核對後修 COLUMN_ALIASES。")
    return out


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    return float(s)


def _int(s: str) -> int | None:
    v = _num(s)
    return None if v is None else int(v)


def _date(s: str) -> dt.date:
    s = s.strip().replace("/", "-")
    return dt.date.fromisoformat(s)


def _rows(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ParserError("CSV 內容不足（無表頭或無資料列）——該日可能非交易日")
    return rows[0], rows[1:]


def parse_options_csv(text: str, contract: str = "TXO") -> list[OptionRow]:
    """解析「選擇權每日交易行情」CSV，只留指定契約（預設 TXO）。"""
    header, body = _rows(text)
    col = _index_columns(header, OPT_COLUMNS)
    out: list[OptionRow] = []
    for lineno, r in enumerate(body, start=2):
        if r[col["contract"]].strip() != contract:
            continue
        try:
            cp_raw = r[col["cp"]].strip()
            cp = _CP_MAP.get(cp_raw) or _CP_MAP.get(cp_raw.lower())
            if cp is None:
                raise ParserError(f"未知買賣權值: {cp_raw!r}")
            out.append(OptionRow(
                trade_date=_date(r[col["trade_date"]]),
                contract=contract,
                expiry_code=r[col["expiry_code"]].strip(),
                strike=float(r[col["strike"]].replace(",", "")),
                cp=cp,
                open=_num(r[col["open"]]),
                high=_num(r[col["high"]]),
                low=_num(r[col["low"]]),
                close=_num(r[col["close"]]),
                settlement=_num(r[col["settlement"]]),
                volume=_int(r[col["volume"]]) or 0,
                oi=_int(r[col["oi"]]),
                session=r[col["session"]].strip() or "一般",
            ))
        except ParserError:
            raise
        except Exception as e:
            raise ParserError(f"第 {lineno} 行解析失敗: {e}；原始列: {r}") from e
    return out


def parse_futures_csv(text: str, contract: str = "TX") -> list[FuturesRow]:
    """解析「期貨每日交易行情」CSV，只留指定契約（預設 TX，臺股期貨）。"""
    header, body = _rows(text)
    col = _index_columns(header, FUT_COLUMNS)
    out: list[FuturesRow] = []
    for lineno, r in enumerate(body, start=2):
        if r[col["contract"]].strip() != contract:
            continue
        try:
            out.append(FuturesRow(
                trade_date=_date(r[col["trade_date"]]),
                contract=contract,
                expiry_code=r[col["expiry_code"]].strip(),
                open=_num(r[col["open"]]),
                high=_num(r[col["high"]]),
                low=_num(r[col["low"]]),
                close=_num(r[col["close"]]),
                settlement=_num(r[col["settlement"]]),
                volume=_int(r[col["volume"]]) or 0,
                oi=_int(r[col["oi"]]),
                session=r[col["session"]].strip() or "一般",
            ))
        except Exception as e:
            raise ParserError(f"第 {lineno} 行解析失敗: {e}；原始列: {r}") from e
    return out


def expiry_code_to_date(code: str,
                        holidays: frozenset[dt.date] = frozenset()) -> dt.date:
    """到期代碼 → 到期日。

    已知格式（社群慣例，待真實樣本回驗）：
      'YYYYMM'    月/季契約 → 該月第 3 個星期三（假日順延）
      'YYYYMMWn'  週三週契約 → 該月第 n 個星期三（假日順延）
    其餘（含可能的週五契約代碼）一律 fail loud——拿到真實樣本看到實際代碼
    再來擴充，不猜。
    """
    code = code.strip()
    if len(code) == 6 and code.isdigit():
        return monthly_expiry(int(code[:4]), int(code[4:6]), holidays)
    if len(code) == 8 and code[:6].isdigit() and code[6].upper() == "W" and code[7].isdigit():
        y, m, n = int(code[:4]), int(code[4:6]), int(code[7])
        return next_business_day(nth_weekday(y, m, WEDNESDAY, n), holidays)
    raise ParserError(
        f"未知到期代碼格式: {code!r}——請拿 data/samples/ 真實檔案核對後擴充 "
        "expiry_code_to_date（可能是週五契約或新規則）")
