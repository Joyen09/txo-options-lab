"""Discord 通知 — 沿用 tw-stock-strategy-framework 的 Webhook 模式（CLAUDE.md 通知通道）。

設定（1 分鐘搞定）：
1. Discord 選一個頻道 → 頻道設定 ⚙ → 整合 → Webhook → 新增 → 複製 Webhook URL
2. 寫進 VM 上專案根目錄的 .env：
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/....."
3. 測試：uv run txolab notify-test

訊息直接用 Discord Markdown；單則上限 2000 字，超長自動分段送出。
通知失敗不丟例外（回 False）——監控流程不該被通知環節炸掉。
"""
from __future__ import annotations

import json
import os
import urllib.request

_CHUNK = 1900  # 留餘裕給分段，Discord 單則上限 2000


class DiscordNotifier:
    def __init__(self, webhook_url: str | None = None, timeout: int = 10,
                 dry_run: bool = False):
        self.webhook_url = webhook_url or os.getenv("DISCORD_WEBHOOK_URL")
        self.timeout = timeout
        self.dry_run = dry_run

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def _post(self, content: str) -> bool:
        """POST 一段訊息到 webhook。獨立成方法方便測試替換。"""
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"content": content}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "txolab"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status in (200, 204)  # webhook 成功回 204 No Content

    def send(self, text: str) -> bool:
        """送出訊息，成功回 True。dry-run 只印出；未設定 webhook 回 False。"""
        if self.dry_run:
            print("[dry-run] Discord 訊息：\n" + text)
            return True
        if not self.webhook_url:
            print("[Discord] 未設定 DISCORD_WEBHOOK_URL，訊息未送出")
            return False
        try:
            ok = True
            for i in range(0, len(text), _CHUNK):
                ok = self._post(text[i:i + _CHUNK]) and ok
            return ok
        except Exception as e:  # noqa: BLE001 — 通知失敗不應中斷監控主流程
            print(f"[Discord] 通知失敗: {e}")
            return False
