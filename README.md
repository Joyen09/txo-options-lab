# TXO Options Lab

台指選擇權 (TXO) 研究管線：定價 → 資料 → 隱波監控 → 回測 → paper trade。
完整規格見 `SPEC.md`；開發鐵律見 `CLAUDE.md`（**本 repo 禁止真實下單**）。

## 快速開始

```bash
uv sync --extra dev          # 或 pip install -e ".[dev]"
uv run pytest                # 全部測試需綠燈
```

## 已完成（初版）

| 模組 | 內容 | 驗收 |
|---|---|---|
| `pricing/` | Black-76（預設）、Black-Scholes（官方計算器對照）、Greeks（closed form + 有限差分互驗）、IV solver（無解回 None+原因） | fixtures P-1/P-2 + hypothesis 性質測試 P-3 |
| `contracts/` | tick 表、點值、漲跌幅、月/週三/週五到期與加掛規則（含「每月第一個星期三不加掛」）、假日順延、履約價階梯（±3% 細密序列）、最後結算價與到期損益 | fixtures C-1～C-5 |
| `margin/` | 賣方保證金（A/B 取大、深度價外 ×1.2/×1.5 加收）、跨勒式保證金、期交稅+手續費 | fixtures M-1/M-2 |

## 接下來（按 SPEC Phase 順序）

1. **Phase 0 收尾（要能上網的環境）**：核對期交所現行規則、抄 A/B/C 現值進
   `config/margin.toml`、確認期交稅率——**config 目前是佔位值，填完才可跑回測**
2. **Phase 3 資料層**：先手動下載 2–3 天期交所「選擇權/期貨每日行情」放
   `data/samples/`，再寫 parser（鐵律：不臆測欄位）
3. Phase 4 隱波監控 → Phase 5 回測（criteria 先寫死）→ Phase 6 paper trade

## 與 SPEC 的差異（誠實記錄）

- 通知：SPEC 寫 LINE，改用 **Discord webhook**（LINE Notify 已於 2025-03 停服；
  tw-stock 系統現行通道就是 Discord）
- Python：SPEC 訂 3.12+，初版在 3.11 開發驗證（語法相容），`requires-python >= 3.11`
- 初版由無法連期交所的沙盒環境產生：SPEC 第 2 節 facts 沿用規格書內
  2026-07-30 的查證結果，**Phase 0 收尾時必須重新上網核對**
