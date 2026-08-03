"""模擬帳本：一組在倉部位 + 已平倉紀錄 + 現金，落地 sqlite（冪等、可重啟）。

金額一律以字串存 Decimal（鐵律 4：金錢不進 float）；點數是定價量，用 float。
一次只允許一組部位（與回測引擎相同的紀律：空手才進場）。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Self

from ..backtest.engine import EntrySignal, Leg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_position (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    strategy            TEXT NOT NULL,
    open_date           TEXT NOT NULL,
    expiry              TEXT NOT NULL,
    kind                TEXT NOT NULL,
    legs_json           TEXT NOT NULL,
    lots                INTEGER NOT NULL,
    entry_credit_points REAL NOT NULL,
    entry_costs_twd     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trade (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy            TEXT NOT NULL,
    open_date           TEXT NOT NULL,
    close_date          TEXT NOT NULL,
    reason              TEXT NOT NULL,
    lots                INTEGER NOT NULL,
    entry_credit_points REAL NOT NULL,
    exit_debit_points   REAL NOT NULL,
    costs_twd           TEXT NOT NULL,
    net_twd             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class OpenPosition:
    strategy: str
    open_date: dt.date
    signal: EntrySignal
    lots: int
    entry_credit_points: float   # 每口淨收權利金（負 = 淨付，買方）
    entry_costs: Decimal


@dataclass(frozen=True)
class PaperTrade:
    strategy: str
    open_date: dt.date
    close_date: dt.date
    reason: str
    lots: int
    entry_credit_points: float
    exit_debit_points: float
    costs_twd: Decimal
    net_twd: Decimal


class PaperLedger:
    """sqlite 模擬帳本。用法：`with PaperLedger(path, initial_equity) as led: ...`"""

    def __init__(self, path: Path, initial_cash: Decimal):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_meta(key, value) VALUES ('cash', ?)",
            (str(initial_cash),))
        self.conn.commit()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.close()

    # ---- 現金 ----

    @property
    def cash(self) -> Decimal:
        row = self.conn.execute(
            "SELECT value FROM paper_meta WHERE key = 'cash'").fetchone()
        return Decimal(row[0])

    def _set_cash(self, value: Decimal) -> None:
        self.conn.execute("UPDATE paper_meta SET value = ? WHERE key = 'cash'",
                          (str(value),))

    # ---- 執行紀錄（避免同一交易日重複跑）----

    @property
    def last_run_date(self) -> dt.date | None:
        row = self.conn.execute(
            "SELECT value FROM paper_meta WHERE key = 'last_run_date'").fetchone()
        return dt.date.fromisoformat(row[0]) if row else None

    def mark_run(self, date: dt.date) -> None:
        self.conn.execute(
            "INSERT INTO paper_meta(key, value) VALUES ('last_run_date', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (date.isoformat(),))
        self.conn.commit()

    # ---- 在倉部位 ----

    def open_position(self) -> OpenPosition | None:
        row = self.conn.execute(
            "SELECT strategy, open_date, expiry, kind, legs_json, lots, "
            "entry_credit_points, entry_costs_twd FROM paper_position WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        legs = tuple(Leg(c, float(k), cp, int(q)) for c, k, cp, q in json.loads(row[4]))
        return OpenPosition(
            strategy=row[0], open_date=dt.date.fromisoformat(row[1]),
            signal=EntrySignal(legs=legs, expiry=dt.date.fromisoformat(row[2]), kind=row[3]),
            lots=int(row[5]), entry_credit_points=float(row[6]),
            entry_costs=Decimal(row[7]))

    def record_open(self, pos: OpenPosition, cash_flow: Decimal) -> None:
        """開倉：寫入部位並結算現金流（賣方為收入、買方為支出，皆已扣稅費）。"""
        if self.open_position() is not None:
            raise ValueError("已有在倉部位——模擬帳本一次只持有一組（同回測紀律）")
        legs = [[l.expiry_code, l.strike, l.cp, l.qty] for l in pos.signal.legs]
        self.conn.execute(
            "INSERT INTO paper_position(id, strategy, open_date, expiry, kind, legs_json, "
            "lots, entry_credit_points, entry_costs_twd) VALUES (1,?,?,?,?,?,?,?,?)",
            (pos.strategy, pos.open_date.isoformat(), pos.signal.expiry.isoformat(),
             pos.signal.kind, json.dumps(legs), pos.lots,
             pos.entry_credit_points, str(pos.entry_costs)))
        self._set_cash(self.cash + cash_flow)
        self.conn.commit()

    def record_close(self, trade: PaperTrade, cash_flow: Decimal) -> None:
        """平倉：刪除部位、寫入已平倉紀錄、結算現金流。"""
        if self.open_position() is None:
            raise ValueError("沒有在倉部位可平倉")
        self.conn.execute("DELETE FROM paper_position WHERE id = 1")
        self.conn.execute(
            "INSERT INTO paper_trade(strategy, open_date, close_date, reason, lots, "
            "entry_credit_points, exit_debit_points, costs_twd, net_twd) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (trade.strategy, trade.open_date.isoformat(), trade.close_date.isoformat(),
             trade.reason, trade.lots, trade.entry_credit_points, trade.exit_debit_points,
             str(trade.costs_twd), str(trade.net_twd)))
        self._set_cash(self.cash + cash_flow)
        self.conn.commit()

    def closed_trades(self) -> list[PaperTrade]:
        rows = self.conn.execute(
            "SELECT strategy, open_date, close_date, reason, lots, entry_credit_points, "
            "exit_debit_points, costs_twd, net_twd FROM paper_trade ORDER BY id").fetchall()
        return [PaperTrade(
            strategy=r[0], open_date=dt.date.fromisoformat(r[1]),
            close_date=dt.date.fromisoformat(r[2]), reason=r[3], lots=int(r[4]),
            entry_credit_points=float(r[5]), exit_debit_points=float(r[6]),
            costs_twd=Decimal(r[7]), net_twd=Decimal(r[8])) for r in rows]

    @property
    def realized_net(self) -> Decimal:
        return sum((t.net_twd for t in self.closed_trades()), Decimal(0))
