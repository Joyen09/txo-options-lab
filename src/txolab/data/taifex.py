"""期交所每日行情下載器。

端點（「交易資訊 → 資料下載專區」https://www.taifex.com.tw/cht/3/dlFutDailyMarketView
頁面表單的送出目標，社群 parser 普遍使用）：
  期貨：  POST https://www.taifex.com.tw/cht/3/futDataDown
  選擇權：POST https://www.taifex.com.tw/cht/3/optDataDown
  參數：  down_type=1, commodity_id=TX/TXO,
          queryStartDate=YYYY/MM/DD, queryEndDate=YYYY/MM/DD

⚠️ 誠實聲明：本檔在連不上期交所的沙盒環境寫成，端點與參數取自公開文件與
社群慣例，「未經本 repo 實測」。第一次在 VM 上執行 `txolab fetch` 時：
  - 下載內容一律先過 parser 的嚴格表頭驗證（鐵律 6 的防呆），不符整批拒收
  - 原始檔會存進 data/samples/（parser 開發基準，之後修格式就看這份）

禮貌性 rate limit：對期交所的連續請求間隔 >= REQUEST_INTERVAL 秒。
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import httpx

FUT_URL = "https://www.taifex.com.tw/cht/3/futDataDown"
OPT_URL = "https://www.taifex.com.tw/cht/3/optDataDown"
REQUEST_INTERVAL = 3.0  # 秒
_HEADERS = {"User-Agent": "txolab (research; polite rate-limited)"}

_last_request_at = 0.0


class DownloadError(RuntimeError):
    pass


def _rate_limit() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _decode(raw: bytes) -> str:
    """期交所 CSV 歷來是 Big5/MS950；也容忍 UTF-8(-BOM)。"""
    for enc in ("utf-8-sig", "ms950", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DownloadError("無法解碼下載內容（非 UTF-8 也非 Big5）")


def _download(url: str, commodity_id: str, date: dt.date, retries: int = 3) -> str:
    data = {
        "down_type": "1",
        "commodity_id": commodity_id,
        "queryStartDate": date.strftime("%Y/%m/%d"),
        "queryEndDate": date.strftime("%Y/%m/%d"),
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        _rate_limit()
        try:
            resp = httpx.post(url, data=data, headers=_HEADERS, timeout=30.0,
                              follow_redirects=True)
            resp.raise_for_status()
            text = _decode(resp.content)
            if "<html" in text[:500].lower():
                raise DownloadError(
                    f"{url} 回傳 HTML 而非 CSV——端點或參數可能已改版，"
                    "請對照資料下載專區頁面的表單修正 taifex.py")
            return text
        except Exception as e:  # noqa: BLE001 — 統一重試，最後一次才丟
            last_err = e
            time.sleep(2 ** attempt)
    raise DownloadError(f"下載失敗 {url} ({commodity_id} {date}): {last_err}")


def download_options_daily(date: dt.date) -> str:
    """TXO 選擇權每日交易行情 CSV（含一般與盤後時段）。"""
    return _download(OPT_URL, "TXO", date)


def download_futures_daily(date: dt.date) -> str:
    """TX 臺股期貨每日交易行情 CSV（Black-76 的 forward 輸入）。"""
    return _download(FUT_URL, "TX", date)


def save_sample(text: str, kind: str, date: dt.date, samples_dir: Path) -> Path:
    """把原始 CSV 存進 data/samples/——parser 的開發與除錯基準（鐵律 6）。"""
    samples_dir.mkdir(parents=True, exist_ok=True)
    path = samples_dir / f"{kind}_{date:%Y%m%d}.csv"
    path.write_text(text, encoding="utf-8")
    return path
