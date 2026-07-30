"""每日隱波監控：收盤後排程跑（VM systemd timer），事件 → Discord。

流程：讀當日行情（store）→ 組 chain → 各到期日 smile/ATM IV → 寫 iv_history
→ 對近月 ATM IV 序列算 IV rank/percentile → 事件偵測 → Discord 通知。

事件（門檻在 config/monitor.toml）：
  - IV rank 突破上/下門檻（賣方機會 / 賣方危險區）
  - 期限結構倒掛（近月 ATM IV > 次月 ATM IV：事件風險訊號）
  - ATM IV 單日跳升超過門檻（波動率事件）
daily_summary=true 時每天都送一則摘要（沒事件也送，確認管線活著）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..data.chain import Chain, build_chain
from ..data.store import Store
from ..notify import DiscordNotifier
from .surface import Smile, atm_iv, build_smile, iv_percentile, iv_rank, skew_25d


@dataclass(frozen=True)
class MonitorConfig:
    r: float = 0.015              # 台幣短率近似值（只影響折現，短天期影響極小）
    window: int = 60              # IV rank/percentile 回看交易日數
    rank_high: float = 80.0       # rank 高檔門檻
    rank_low: float = 20.0        # rank 低檔門檻
    jump_vol_pts: float = 0.03    # ATM IV 單日跳升門檻（3 個 vol 百分點）
    daily_summary: bool = True


@dataclass
class MonitorResult:
    trade_date: dt.date
    front_code: str | None = None
    front_atm_iv: float | None = None
    rank: float | None = None
    percentile: float | None = None
    skew: float | None = None
    term: list[tuple[str, float]] = None  # (expiry_code, atm_iv) 依到期排序
    events: list[str] = None
    message: str = ""


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def _fmt_iv(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.2f}%"


def run_monitor(trade_date: dt.date, store: Store, cfg: MonitorConfig,
                notifier: DiscordNotifier) -> MonitorResult:
    res = MonitorResult(trade_date=trade_date, term=[], events=[])

    options = store.options_on(trade_date)
    futures = store.futures_on(trade_date)
    if not options or not futures:
        res.message = (f"⚠️ **TXO 監控 {trade_date}**\n"
                       f"資料不足（options={len(options)}, futures={len(futures)}）——"
                       "當日 fetch 可能失敗或非交易日")
        notifier.send(res.message)
        return res

    chain: Chain = build_chain(trade_date, options, futures)

    smiles: list[tuple[Smile, float | None]] = []
    for sl in chain.slices:
        sm = build_smile(sl, cfg.r)
        a = atm_iv(sm)
        smiles.append((sm, a))
        if a is not None:
            store.upsert_iv(trade_date, sl.expiry_code, a, sl.forward)
            res.term.append((sl.expiry_code, a))

    # 近月（純月份代碼中最早到期者）為 rank/skew 的基準
    monthly = [(sm, a) for sm, a in smiles
               if len(sm.expiry_code) == 6 and a is not None]
    if monthly:
        front_sm, front_iv = monthly[0]
        res.front_code, res.front_atm_iv = front_sm.expiry_code, front_iv
        res.skew = skew_25d(front_sm, cfg.r)
        series = [v for _, v in store.front_iv_series(cfg.window)]
        if series:
            res.rank = iv_rank(series, front_iv)
            res.percentile = iv_percentile(series, front_iv)
            # 事件：rank 門檻
            if res.rank is not None and res.rank >= cfg.rank_high:
                res.events.append(
                    f"🔺 IV rank {res.rank:.0f} ≥ {cfg.rank_high:.0f}（近 {cfg.window} 日高檔）")
            if res.rank is not None and res.rank <= cfg.rank_low:
                res.events.append(
                    f"🔻 IV rank {res.rank:.0f} ≤ {cfg.rank_low:.0f}（近 {cfg.window} 日低檔）")
            # 事件：單日跳升
            if len(series) >= 2 and front_iv - series[-2] >= cfg.jump_vol_pts:
                res.events.append(
                    f"⚡ ATM IV 單日 +{(front_iv - series[-2]) * 100:.1f} vol pts"
                    f"（{_fmt_iv(series[-2])} → {_fmt_iv(front_iv)}）")
        # 事件：期限結構倒掛（近月 vs 次月，只比月/季契約）
        if len(monthly) >= 2 and monthly[0][1] > monthly[1][1]:
            res.events.append(
                f"🔃 期限結構倒掛：{monthly[0][0].expiry_code} {_fmt_iv(monthly[0][1])} > "
                f"{monthly[1][0].expiry_code} {_fmt_iv(monthly[1][1])}")

    if res.events or cfg.daily_summary:
        term_str = " | ".join(f"{c[-4:] if len(c) > 6 else c[4:6] + '月'} {_fmt_iv(v)}"
                              for c, v in res.term[:6])
        lines = [f"📊 **TXO 隱波日報 {trade_date}**",
                 (f"近月 {res.front_code or 'n/a'}  ATM IV {_fmt_iv(res.front_atm_iv)}  "
                  f"rank {_fmt_pct(res.rank)}  pct {_fmt_pct(res.percentile)}"),
                 f"25Δ skew(P−C) {_fmt_iv(res.skew)}",
                 f"期限結構: {term_str or 'n/a'}"]
        if res.events:
            lines.append("**事件**")
            lines += [f"- {e}" for e in res.events]
        res.message = "\n".join(lines)
        notifier.send(res.message)
    return res
