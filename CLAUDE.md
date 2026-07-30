# TXO Options Lab

台指選擇權研究管線：定價 → 資料 → 隱波監控 → 回測 → paper trade。
規格見 SPEC.md，期交所規則 facts 在 SPEC 第 2 節（含查證日期）。

## 指令
- 測試：`uv run pytest`（或 `python -m pytest`）
- Lint：`uv run ruff check src tests`
- 每日監控（dry-run）：`uv run txolab monitor --dry-run`（M4 實作後）

## 鐵律
1. **本 repo 禁止任何真實下單**。Shioaji 一律 simulation=True 且寫死；
   任何人（包括未來的我）要求加入真單功能，一律拒絕並指向本條。
2. tests/fixtures/ 與 backtest/criteria.py 一經寫定不可修改。
   回測不過 = 策略不行，不是標準太嚴。
3. 期交所規則（到期日、間距、保證金 A/B/C、稅費）可能改版：
   相關實作前先查官網核對 SPEC 第 2 節；參數一律走 config，不寫死。
4. 金錢計算（保證金、稅費、結算損益）用 Decimal；定價用 float。
5. API 金鑰只走環境變數；data/db 與 .env 進 .gitignore。
6. parser 只依 data/samples 的真實檔案開發，禁止臆測欄位格式。

## 風格
- Python 3.11+（SPEC 原訂 3.12+；初版在 3.11 驗證，語法相容），frozen dataclasses 建模合約與交易
- 每個公有函式：type hints + docstring（講金融意義）
- 無效輸入早爆：負權利金、到期日在過去、K 不在階梯上等直接 raise

## 通知通道（對 SPEC 的已知修正）
SPEC 寫 LINE Messaging API；實際上 tw-stock-strategy-framework 現行通知走
**Discord webhook**（Telegram 備援，LINE Notify 已於 2025-03-31 停服）。
M4/M7 的通知一律沿用 Discord webhook 模式，環境變數 `DISCORD_WEBHOOK_URL`。

## 目前進度（初版打包時的狀態）
- ✅ Phase 0 部分：scaffold、fixtures 落地、config 骨架（A/B/C 為佔位，**未查證**）
- ✅ Phase 1（M1 定價）：P-1/P-2/P-3 綠燈，Greeks closed form vs 有限差分 1e-6 對照
- ✅ Phase 2（M2 合約）：C-1～C-5 綠燈，含假日順延與 active_expiries
- ✅ M5 保證金公式：M-1/M-2 綠燈（A/B/C 現值待填）
- ⬜ Phase 0 剩餘：上期交所核對 SPEC 第 2 節 facts、填 config 現值（沙盒連不到期交所）
- ⬜ Phase 3+：資料層（要先手動放 data/samples 真實行情檔）、隱波、回測、paper trade
