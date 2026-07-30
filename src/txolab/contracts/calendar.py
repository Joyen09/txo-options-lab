"""TXO 到期日與加掛行事曆——整個專案最容易寫錯的一關。

規則 (SPEC 第 2 節，2026-07-30 查證)：
- 月契約：交割月「第 3 個星期三」為最後交易日；連續 3 近月 + 2 個接續季月 (3/6/9/12)
- 週三週契約：每週三加掛「次二週星期三」到期之契約，
  但「每月第一個星期三」不加掛——因為那天 +14 天正是第 3 個星期三，月契約已存在
- 週五週契約：每週五加掛「次二週星期五」到期之契約 (無第一週例外)
- 到期日遇假日：順延至次一營業日

假日表由呼叫端傳入 (期交所行事曆 → data/samples/holidays_YYYY.csv)；
本模組不猜假日，沒傳就只避開週六日。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

WEDNESDAY, FRIDAY = 2, 4
QUARTER_MONTHS = (3, 6, 9, 12)


def is_business_day(d: dt.date, holidays: frozenset[dt.date] = frozenset()) -> bool:
    return d.weekday() < 5 and d not in holidays


def next_business_day(d: dt.date, holidays: frozenset[dt.date] = frozenset()) -> dt.date:
    while not is_business_day(d, holidays):
        d += dt.timedelta(days=1)
    return d


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """某年某月的第 n 個星期 weekday (0=一 … 6=日)。"""
    first = dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    d = first + dt.timedelta(days=offset + 7 * (n - 1))
    if d.month != month:
        raise ValueError(f"{year}-{month:02d} 沒有第 {n} 個星期{weekday}")
    return d


def monthly_expiry(year: int, month: int,
                   holidays: frozenset[dt.date] = frozenset()) -> dt.date:
    """月契約最後交易日 = 第 3 個星期三，遇假日順延次一營業日。"""
    return next_business_day(nth_weekday(year, month, WEDNESDAY, 3), holidays)


def is_first_wednesday(d: dt.date) -> bool:
    return d.weekday() == WEDNESDAY and d.day <= 7


def weekly_wed_listing(d: dt.date,
                       holidays: frozenset[dt.date] = frozenset()) -> dt.date | None:
    """星期三加掛：回傳當日新掛週契約的到期日 (次二週星期三)，不加掛回 None。

    每月第一個星期三不加掛——+14 天恰為第 3 個星期三，該到期日已有月契約。"""
    if d.weekday() != WEDNESDAY:
        raise ValueError(f"{d} 不是星期三，非週三加掛日")
    if is_first_wednesday(d):
        return None
    return next_business_day(d + dt.timedelta(days=14), holidays)


def weekly_fri_listing(d: dt.date,
                       holidays: frozenset[dt.date] = frozenset()) -> dt.date:
    """星期五加掛：回傳當日新掛週契約的到期日 (次二週星期五)。"""
    if d.weekday() != FRIDAY:
        raise ValueError(f"{d} 不是星期五，非週五加掛日")
    return next_business_day(d + dt.timedelta(days=14), holidays)


@dataclass(frozen=True)
class Expiry:
    date: dt.date
    kind: str  # 'monthly' | 'quarterly' | 'weekly_wed' | 'weekly_fri'


def active_expiries(today: dt.date,
                    holidays: frozenset[dt.date] = frozenset()) -> list[Expiry]:
    """某日所有存續到期日：3 近月 + 2 接續季月 + 已掛牌未到期之週契約。

    到期日當天仍可交易 (至 13:30)，故 expiry >= today 視為存續。
    注意：這是「規則推導」的清單；Phase 3 之後要拿真實行情檔的掛牌清單回驗。
    """
    out: list[Expiry] = []

    # --- 3 個連續近月 ---
    y, m = today.year, today.month
    months: list[tuple[int, int]] = []
    while len(months) < 3:
        if monthly_expiry(y, m, holidays) >= today:
            months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    out += [Expiry(monthly_expiry(yy, mm, holidays), "monthly") for yy, mm in months]

    # --- 2 個接續季月 (3/6/9/12，不與近月重複) ---
    quarters: list[Expiry] = []
    while len(quarters) < 2:
        if m in QUARTER_MONTHS and (y, m) not in months:
            quarters.append(Expiry(monthly_expiry(y, m, holidays), "quarterly"))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    out += quarters

    # --- 週契約：回看 15 天內的加掛日，收集尚未到期者 ---
    monthly_dates = {e.date for e in out}
    for back in range(15):
        d = today - dt.timedelta(days=back)
        if d.weekday() == WEDNESDAY:
            exp = weekly_wed_listing(d, holidays)
            if exp is not None and exp >= today and exp not in monthly_dates:
                out.append(Expiry(exp, "weekly_wed"))
        elif d.weekday() == FRIDAY:
            exp = weekly_fri_listing(d, holidays)
            if exp >= today:
                out.append(Expiry(exp, "weekly_fri"))

    # 去重 (同到期日以先出現者為準) 並按日期排序
    seen: set[dt.date] = set()
    uniq = []
    for e in sorted(out, key=lambda e: e.date):
        if e.date not in seen:
            seen.add(e.date)
            uniq.append(e)
    return uniq


def load_holidays(rows: Iterable[str]) -> frozenset[dt.date]:
    """從 'YYYY-MM-DD' 字串序列載入假日表 (data/samples/holidays_YYYY.csv 一行一日)。"""
    out = set()
    for r in rows:
        r = r.strip()
        if r and not r.startswith("#"):
            out.add(dt.date.fromisoformat(r.split(",")[0]))
    return frozenset(out)
