"""市況基準（regime）——回答「策略賺的是 edge 還是 beta？」

回測報表只講策略賺多少，不講同期市場長什麼樣子。本模組用與回測引擎
**完全相同的指數定義**（近月 TX 結算價 forward）算出兩件事：

  - 期間指數報酬與最大回撤：策略報酬要跟這個比才有意義
  - 指數落在 N 日均線之上的日數佔比：趨勢過濾策略「有多少時間被允許進場」，
    直接量化這份樣本對做多策略有多友善

刻意不提供槓桿對齊的「買進持有 P&L」：TX 一口名目 = 指數 × 200，在本專案的
初始資金下遠超可承受部位，硬換算只會生出一個沒人會真的執行的對照組。
此處只描述市況，不假裝那是一個可交易基準。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..data.chain import build_chain
from ..data.store import Store


@dataclass(frozen=True)
class RegimeStats:
    """一段期間的市況描述（指數口徑同引擎：近月 TX forward）。"""

    n_days: int
    first_index: float
    last_index: float
    total_return: float      # 期末/期初 − 1
    max_drawdown: float      # 指數峰值到谷底的最大跌幅（比例）
    ma_days: int
    ma_defined_days: int     # 均線滿窗口、可判定多空的日數
    days_above_ma: int
    share_above_ma: float    # days_above_ma / ma_defined_days（暖機期不計入）


def index_series(store: Store, start: dt.date | None = None,
                 end: dt.date | None = None) -> list[tuple[dt.date, float]]:
    """逐日近月 TX forward 序列；資料不完整的日子跳過（與引擎同一套判準）。"""
    out: list[tuple[dt.date, float]] = []
    for s in store.trade_dates():
        d = dt.date.fromisoformat(s)
        if (start is not None and d < start) or (end is not None and d > end):
            continue
        options, futures = store.options_on(d), store.futures_on(d)
        if not options or not futures:
            continue
        try:
            chain = build_chain(d, options, futures)
        except Exception:  # noqa: BLE001, S112 — 該日缺 TX 等不完整資料就跳過
            continue
        if not chain.slices:
            continue
        monthly = [sl for sl in chain.slices if len(sl.expiry_code) == 6]
        out.append((d, monthly[0].forward if monthly else chain.slices[0].forward))
    return out


def regime_stats(series: list[tuple[dt.date, float]], ma_days: int) -> RegimeStats:
    """由指數序列算市況統計。序列為空或 ma_days < 1 直接 raise（無效輸入早爆）。"""
    if not series:
        raise ValueError("指數序列為空——該期間沒有可用的行情資料")
    if ma_days < 1:
        raise ValueError(f"ma_days 必須 >= 1（收到 {ma_days}）")

    px = [x for _, x in series]
    peak = px[0]
    mdd = 0.0
    for x in px:
        peak = max(peak, x)
        mdd = max(mdd, (peak - x) / peak)

    above = 0
    for i in range(ma_days - 1, len(px)):
        ma = sum(px[i - ma_days + 1: i + 1]) / ma_days
        if px[i] > ma:
            above += 1
    defined = max(0, len(px) - ma_days + 1)

    return RegimeStats(
        n_days=len(px),
        first_index=px[0],
        last_index=px[-1],
        total_return=px[-1] / px[0] - 1.0,
        max_drawdown=mdd,
        ma_days=ma_days,
        ma_defined_days=defined,
        days_above_ma=above,
        share_above_ma=above / defined if defined else 0.0,
    )
