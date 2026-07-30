"""Option chain 組裝：某日 × 各到期日 × 履約價 × C/P，對齊 forward（TX 期貨）。

forward 對齊規則（市場慣例 + 已知限制，誠實記錄）：
  - 月/季選擇權：用「同到期月份」的 TX 期貨收盤/結算價
  - 週選擇權：TX 沒有同到期的週期貨 → 用「到期日 >= 該週選到期日的最近月 TX」
    近似。這會帶進少量期現基差誤差，對短天期 ATM IV 影響有限；
    之後若要更精確可改用 put-call parity 反推 implied forward（M4 延伸）。
T 的計算：日曆日/365（SPEC 預設）；到期日當天視為 0（走結算，不算 IV）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .parser import FuturesRow, OptionRow, expiry_code_to_date


@dataclass(frozen=True)
class ExpirySlice:
    expiry_code: str
    expiry: dt.date
    forward: float          # 對齊到的 TX 期貨價（結算價優先，其次收盤）
    forward_code: str       # 用了哪個月份的 TX（誠實記錄近似來源）
    t_years: float          # 日曆日/365
    rows: list[OptionRow] = field(default_factory=list)


@dataclass(frozen=True)
class Chain:
    trade_date: dt.date
    slices: list[ExpirySlice]  # 依到期日排序


class ChainError(RuntimeError):
    pass


def _fut_price(r: FuturesRow) -> float | None:
    return r.settlement if r.settlement else r.close


def build_chain(trade_date: dt.date, options: list[OptionRow],
                futures: list[FuturesRow],
                holidays: frozenset[dt.date] = frozenset()) -> Chain:
    """由當日行情列組裝 chain。到期日已過或當天到期的 slice 略過（T=0 走結算）。"""
    fut_by_code: dict[str, float] = {}
    for r in futures:
        px = _fut_price(r)
        if px is not None and len(r.expiry_code) == 6:  # 只用月/季 TX，價差單等代碼略過
            fut_by_code[r.expiry_code] = px
    if not fut_by_code:
        raise ChainError(f"{trade_date} 沒有可用的 TX 期貨價，無法定 forward")
    fut_expiries = sorted((expiry_code_to_date(c, holidays), c) for c in fut_by_code)

    by_code: dict[str, list[OptionRow]] = {}
    for r in options:
        by_code.setdefault(r.expiry_code, []).append(r)

    slices: list[ExpirySlice] = []
    for code, rows in by_code.items():
        expiry = expiry_code_to_date(code, holidays)
        days = (expiry - trade_date).days
        if days <= 0:
            continue  # 已到期/當天到期：損益走 settlement.expiry_payoff_twd
        month_code = code[:6]
        if month_code in fut_by_code:
            fwd, fwd_code = fut_by_code[month_code], month_code
        else:  # 週選：最近一個到期不早於該週選的 TX 月契約
            later = [(d, c) for d, c in fut_expiries if d >= expiry]
            fwd_code = later[0][1] if later else fut_expiries[-1][1]
            fwd = fut_by_code[fwd_code]
        slices.append(ExpirySlice(
            expiry_code=code, expiry=expiry, forward=fwd, forward_code=fwd_code,
            t_years=days / 365.0,
            rows=sorted(rows, key=lambda r: (r.strike, r.cp)),
        ))
    slices.sort(key=lambda s: s.expiry)
    return Chain(trade_date=trade_date, slices=slices)
