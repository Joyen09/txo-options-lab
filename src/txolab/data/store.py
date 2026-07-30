"""sqlite 落地與查詢。單機夠用；增量寫入、重跑冪等（INSERT OR REPLACE）。

資料表：
  option_daily / futures_daily：每日行情（主鍵含 session，一般與盤後分列）
  iv_history：每日各到期日的 ATM IV（monitor 算 IV rank/percentile 的窗口來源）
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Self

from .parser import FuturesRow, OptionRow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_daily (
    trade_date TEXT NOT NULL, contract TEXT NOT NULL, expiry_code TEXT NOT NULL,
    strike REAL NOT NULL, cp TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, settlement REAL,
    volume INTEGER NOT NULL, oi INTEGER, session TEXT NOT NULL,
    PRIMARY KEY (trade_date, contract, expiry_code, strike, cp, session)
);
CREATE TABLE IF NOT EXISTS futures_daily (
    trade_date TEXT NOT NULL, contract TEXT NOT NULL, expiry_code TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, settlement REAL,
    volume INTEGER NOT NULL, oi INTEGER, session TEXT NOT NULL,
    PRIMARY KEY (trade_date, contract, expiry_code, session)
);
CREATE TABLE IF NOT EXISTS iv_history (
    trade_date TEXT NOT NULL, expiry_code TEXT NOT NULL,
    atm_iv REAL NOT NULL, forward REAL NOT NULL,
    PRIMARY KEY (trade_date, expiry_code)
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.conn.commit()
        self.close()

    # ---------------- 寫入（冪等） ----------------

    def upsert_options(self, rows: list[OptionRow]) -> int:
        self.conn.executemany(
            "INSERT OR REPLACE INTO option_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.trade_date.isoformat(), r.contract, r.expiry_code, r.strike, r.cp,
              r.open, r.high, r.low, r.close, r.settlement, r.volume, r.oi, r.session)
             for r in rows])
        self.conn.commit()
        return len(rows)

    def upsert_futures(self, rows: list[FuturesRow]) -> int:
        self.conn.executemany(
            "INSERT OR REPLACE INTO futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(r.trade_date.isoformat(), r.contract, r.expiry_code,
              r.open, r.high, r.low, r.close, r.settlement, r.volume, r.oi, r.session)
             for r in rows])
        self.conn.commit()
        return len(rows)

    def upsert_iv(self, trade_date: dt.date, expiry_code: str,
                  atm_iv: float, forward: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO iv_history VALUES (?,?,?,?)",
                          (trade_date.isoformat(), expiry_code, atm_iv, forward))
        self.conn.commit()

    # ---------------- 查詢 ----------------

    def options_on(self, trade_date: dt.date, session: str = "一般") -> list[OptionRow]:
        cur = self.conn.execute(
            "SELECT * FROM option_daily WHERE trade_date=? AND session=?",
            (trade_date.isoformat(), session))
        return [OptionRow(dt.date.fromisoformat(r[0]), r[1], r[2], r[3], r[4],
                          r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12])
                for r in cur.fetchall()]

    def futures_on(self, trade_date: dt.date, session: str = "一般") -> list[FuturesRow]:
        cur = self.conn.execute(
            "SELECT * FROM futures_daily WHERE trade_date=? AND session=?",
            (trade_date.isoformat(), session))
        return [FuturesRow(dt.date.fromisoformat(r[0]), r[1], r[2],
                           r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10])
                for r in cur.fetchall()]

    def atm_iv_series(self, expiry_kind_codes: list[str] | None = None,
                      window: int = 60) -> list[tuple[str, str, float]]:
        """近 window 個交易日的 (trade_date, expiry_code, atm_iv)，舊到新。"""
        cur = self.conn.execute(
            "SELECT trade_date, expiry_code, atm_iv FROM iv_history "
            "ORDER BY trade_date DESC LIMIT ?", (window * 8,))  # 每日多個到期日，抓寬一點
        rows = list(reversed(cur.fetchall()))
        if expiry_kind_codes is not None:
            rows = [r for r in rows if r[1] in expiry_kind_codes]
        return rows

    def front_iv_series(self, window: int = 60) -> list[tuple[str, float]]:
        """每日「最近月契約（純 YYYYMM 代碼中最小者）」的 ATM IV 序列，舊到新。

        IV rank/percentile 的基準序列：跨日比較要用同型契約（近月），
        避免週契約與季月混進來造成期限結構雜訊。
        """
        cur = self.conn.execute(
            "SELECT trade_date, MIN(expiry_code), atm_iv FROM iv_history "
            "WHERE LENGTH(expiry_code)=6 GROUP BY trade_date "
            "ORDER BY trade_date DESC LIMIT ?", (window,))
        rows = [(r[0], self._iv_for(r[0], r[1])) for r in cur.fetchall()]
        return list(reversed(rows))

    def _iv_for(self, trade_date: str, expiry_code: str) -> float:
        cur = self.conn.execute(
            "SELECT atm_iv FROM iv_history WHERE trade_date=? AND expiry_code=?",
            (trade_date, expiry_code))
        return cur.fetchone()[0]

    def trade_dates(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT DISTINCT trade_date FROM option_daily ORDER BY trade_date")
        return [r[0] for r in cur.fetchall()]
