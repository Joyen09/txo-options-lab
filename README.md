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
| `data/` | 期交所每日行情下載器（禮貌 rate limit）、嚴格表頭驗證 parser、sqlite 落地（冪等）、option chain 組裝（TX forward 對齊） | 合成樣本測試；**真實檔驗證待 VM 首跑** |
| `vol/` | smile / ATM IV（forward 內插）/ term structure / IV rank・percentile / 25Δ skew、事件偵測（rank 門檻・倒掛・單日跳升） | 合成 chain 還原已知 σ |
| `notify.py` | Discord webhook（沿用 tw-stock 模式；分段、no-throw、dry-run） | 單元測試 |
| `cli.py` | `txolab fetch / monitor / chain / notify-test` | 冒煙測試 |

## 在 VM 上跑（每日收盤後自動監控 → Discord）

```bash
git clone <repo> ~/txo && cd ~/txo
bash deploy/setup_vm.sh        # 安裝 + .env 樣板
# 填 .env 的 DISCORD_WEBHOOK_URL → txolab notify-test
# 首次 fetch = parser 真實格式驗證（見 deploy/README_DEPLOY.md 步驟三）
```

完整步驟（含 systemd timer 排程、Phase 0 收尾查證清單）見 **deploy/README_DEPLOY.md**。

## 接下來（按 SPEC Phase 順序）

1. **Phase 0 收尾（VM 首跑時做）**：核對期交所現行規則、抄 A/B/C 現值進
   `config/margin.toml`、確認期交稅率——**config 目前是佔位值，填完才可跑回測**
2. **Phase 3 收尾**：VM 上 `txolab fetch` 用真實檔案驗證 parser（鐵律 6 防呆
   會嚴格把關；不符會列出實際表頭）；確認週五契約的到期代碼格式
3. Phase 5 回測（criteria 先寫死）→ Phase 6 paper trade

## 與 SPEC 的差異（誠實記錄）

- 通知：SPEC 寫 LINE，改用 **Discord webhook**（LINE Notify 已於 2025-03 停服；
  tw-stock 系統現行通道就是 Discord）
- Python：SPEC 訂 3.12+，初版在 3.11 開發驗證（語法相容），`requires-python >= 3.11`
- 開發環境連不到期交所：SPEC 第 2 節 facts 沿用規格書內 2026-07-30 的查證結果，
  **Phase 0 收尾時必須重新上網核對**
- M3 parser 的欄名取自公開文件與社群慣例，「不是」照本 repo 自抓的真實樣本寫的
  （鐵律 6 的已知妥協）：因此 parser 全面 fail-loud——欄位用名稱找、缺欄整批拒收、
  未知到期代碼直接報錯，**首次 VM fetch 即真實格式驗證**，原始檔自動留存 data/samples/
- 週選 forward：TX 無同到期週期貨，用「到期不早於該週選的最近月 TX」近似
  （帶少量基差誤差；更精確可日後改 put-call parity 反推 implied forward）
- VIX 同向性 sanity 對照（SPEC M4 DoD 之一）尚未實作——需要官方波動率指數
  歷史檔，留待 VM 上有真實資料後補
