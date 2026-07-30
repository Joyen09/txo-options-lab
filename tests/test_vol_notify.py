"""M4 隱波監控 + Discord 通知：合成 chain 的 IV 還原、rank/percentile、事件偵測、
通知分段與 no-throw。定價來源用 black76 以已知 σ 產生報價 → surface 要能還原。"""
import datetime as dt

import pytest

from txolab.data.chain import ExpirySlice
from txolab.data.parser import OptionRow
from txolab.data.store import Store
from txolab.notify import DiscordNotifier
from txolab.pricing import black76
from txolab.vol.monitor import MonitorConfig, run_monitor
from txolab.vol.surface import atm_iv, build_smile, iv_percentile, iv_rank

R = 0.015
TRADE = dt.date(2026, 7, 29)


def _slice(code: str, expiry: dt.date, f: float, sigma: float) -> ExpirySlice:
    """用 Black-76 在已知 σ 下產生整條 slice 的理論結算價。"""
    t = (expiry - TRADE).days / 365.0
    rows = []
    for k in range(int(f * 0.94) // 100 * 100, int(f * 1.06) // 100 * 100 + 1, 100):
        for cp in ("C", "P"):
            px = black76.price(cp, f, k, R, sigma, t)
            rows.append(OptionRow(TRADE, "TXO", code, float(k), cp,
                                  None, None, None, None, round(px, 1), 100, 10, "一般"))
    return ExpirySlice(code, expiry, f, code[:6], t, rows)


# ---------------- surface：合成 chain 還原已知 σ ----------------

def test_smile_recovers_known_sigma():
    sl = _slice("202608", dt.date(2026, 8, 19), 23050.0, 0.22)
    sm = build_smile(sl, R)
    a = atm_iv(sm)
    # 結算價四捨五入到 0.1 點會帶進少量誤差，容差放 0.5 vol pt
    assert a == pytest.approx(0.22, abs=0.005)


def test_iv_rank_and_percentile():
    series = [0.15, 0.18, 0.20, 0.25, 0.35]
    assert iv_rank(series, 0.35) == pytest.approx(100.0)
    assert iv_rank(series, 0.15) == pytest.approx(0.0)
    assert iv_rank(series, 0.25) == pytest.approx(50.0)
    assert iv_percentile(series, 0.21) == pytest.approx(60.0)
    assert iv_rank([0.2], 0.2) is None  # 窗口不足


# ---------------- notify：dry-run、未設定、分段 ----------------

def test_notifier_dry_run_and_disabled(capsys):
    assert DiscordNotifier(webhook_url=None, dry_run=True).send("hi") is True
    assert "hi" in capsys.readouterr().out
    assert DiscordNotifier(webhook_url=None).send("hi") is False


def test_notifier_chunks_long_message(monkeypatch):
    n = DiscordNotifier(webhook_url="https://example.invalid/hook")
    sent: list[str] = []
    monkeypatch.setattr(n, "_post", lambda c: sent.append(c) or True)
    assert n.send("x" * 4000) is True
    assert len(sent) == 3 and all(len(c) <= 1900 for c in sent)  # 2000 上限內


def test_notifier_never_raises(monkeypatch):
    n = DiscordNotifier(webhook_url="https://example.invalid/hook", timeout=1)

    def boom(_):
        raise OSError("network down")
    monkeypatch.setattr(n, "_post", boom)
    assert n.send("hi") is False  # 通知失敗不得炸掉監控流程


# ---------------- monitor：端到端（合成資料進 store → 事件 → 訊息） ----------------

def _store_with_history(tmp_path, sigmas: list[float]) -> Store:
    """塞 N 天近月 ATM IV 歷史 + 最後一天的完整行情（用合成 slice 轉回行情列）。"""
    s = Store(tmp_path / "t.sqlite")
    d = TRADE - dt.timedelta(days=len(sigmas))
    for i, sig in enumerate(sigmas):
        day = d + dt.timedelta(days=i)
        s.upsert_iv(day, "202608", sig, 23050.0)
    return s


def test_monitor_end_to_end_high_rank_event(tmp_path, capsys):
    store = _store_with_history(tmp_path, [0.15, 0.16, 0.15, 0.17, 0.16])
    # 當日行情：近月 σ=0.30（歷史高檔）、次月 σ=0.22 → rank 高檔 + 倒掛 + 跳升三事件
    from txolab.data.parser import FuturesRow
    for sl in (_slice("202608", dt.date(2026, 8, 19), 23050.0, 0.30),
               _slice("202609", dt.date(2026, 9, 16), 22960.0, 0.22)):
        store.upsert_options(sl.rows)
    store.upsert_futures([
        FuturesRow(TRADE, "TX", "202608", None, None, None, 23050.0, 23050.0, 1, 1, "一般"),
        FuturesRow(TRADE, "TX", "202609", None, None, None, 22960.0, 22960.0, 1, 1, "一般"),
    ])
    cfg = MonitorConfig(r=R, window=60, rank_high=80, rank_low=20,
                        jump_vol_pts=0.03, daily_summary=False)
    res = run_monitor(TRADE, store, cfg, DiscordNotifier(dry_run=True))
    out = capsys.readouterr().out
    assert res.front_code == "202608"
    assert res.front_atm_iv == pytest.approx(0.30, abs=0.005)
    assert res.rank == pytest.approx(100.0, abs=1.0)
    kinds = "".join(res.events)
    assert "rank" in kinds and "倒掛" in kinds and "單日" in kinds
    assert "TXO 隱波日報" in out  # dry-run 有印出訊息
    # 當日 IV 已寫回歷史（隔日 rank 窗口會包含今天）
    assert store.front_iv_series(10)[-1][1] == pytest.approx(0.30, abs=0.005)
    store.close()


def test_monitor_quiet_day_no_events_no_message(tmp_path):
    store = _store_with_history(tmp_path, [0.19, 0.20, 0.21, 0.20])
    from txolab.data.parser import FuturesRow
    for sl in (_slice("202608", dt.date(2026, 8, 19), 23050.0, 0.20),
               _slice("202609", dt.date(2026, 9, 16), 22960.0, 0.21)):
        store.upsert_options(sl.rows)
    store.upsert_futures([
        FuturesRow(TRADE, "TX", "202608", None, None, None, 23050.0, 23050.0, 1, 1, "一般"),
        FuturesRow(TRADE, "TX", "202609", None, None, None, 22960.0, 22960.0, 1, 1, "一般"),
    ])
    cfg = MonitorConfig(r=R, daily_summary=False)
    res = run_monitor(TRADE, store, cfg, DiscordNotifier(dry_run=True))
    assert res.events == [] and res.message == ""  # 沒事件、不送訊息
    store.close()
