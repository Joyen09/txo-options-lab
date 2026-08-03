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
| `margin/` | 賣方保證金（A/B 取大、深度價外 ×1.2/×1.5 加收）、跨勒式與垂直價差保證金、期交稅+手續費 | fixtures M-1/M-2 |
| `data/` | 期交所每日行情下載器（禮貌 rate limit）、嚴格表頭驗證 parser、sqlite 落地（冪等）、option chain 組裝（TX forward 對齊） | 2026-07-30 真實檔驗證通過 |
| `vol/` | smile / ATM IV（forward 內插）/ term structure / IV rank・percentile / 25Δ skew、事件偵測（rank 門檻・倒掛・單日跳升） | 合成 chain 還原已知 σ |
| `notify.py` | Discord webhook（沿用 tw-stock 模式；分段、no-throw、dry-run） | 單元測試 |
| `backtest/` | **criteria.py（pre-registered，寫死不可回改）**、逐日重放引擎（結算價±滑價、稅費全含、保證金逐日重算、到期前強制平倉、無隨機性；debit 部位與 IV rank 條件化進場）、三賣方基準 + B1 買方基準 | 機制測試綠燈 |
| `cli.py` | `txolab fetch / backfill / monitor / chain / backtest / notify-test` | 冒煙測試 |

## 在 VM 上跑（每日收盤後自動監控 → Discord）

```bash
git clone <repo> ~/txo && cd ~/txo
bash deploy/setup_vm.sh        # 安裝 + .env 樣板
# 填 .env 的 DISCORD_WEBHOOK_URL → txolab notify-test
# 首次 fetch = parser 真實格式驗證（見 deploy/README_DEPLOY.md 步驟三）
```

完整步驟（含 systemd timer 排程、Phase 0 收尾查證清單）見 **deploy/README_DEPLOY.md**。

## Phase 5 回測結論（2026-07-31，2023-08 ~ 2026-07 真實日資料，728 交易日）

| 策略 | 筆數 | 總淨損益 | 每筆期望值 | MDD | 佔用峰值 | 滑價×2 | criteria |
|---|---|---|---|---|---|---|---|
| vertical_spread | 38 | −28,316 | −745 | 44.8% | 25.2% | −13,283 | ❌ FAIL |
| iron_condor | 37 | −414,346 | −11,199 | 47.2% | 25.7% | −381,357 | ❌ FAIL |
| short_strangle | 55 | −205,060 | −3,728 | 23.3% | 32.0% | −214,691 | ❌ FAIL |
| **long_strangle_low_iv (B1)** | 30 | **+470,159** | **+15,672** | 26.5% | 0.0% | **+446,846** | ❌ FAIL |

**賣方三基準**：無條件、月月進場的收租策略在本樣本非正期望值——樣本涵蓋
2024-08 日圓套利平倉與 2025-04 關稅兩次股災（停損集中於此）與指數
22k→40k 大多頭（輾壓 call 邊）。

**B1 買方基準**：五項過四項，是本專案第一個正期望值策略（勝率 47%、
賠貺比 1:2.3、滑價加倍仍賺 44.7 萬），但 **MDD 26.5% > 20% → FAIL**。
回撤來自 2024-09~2025-02 盤整期的**連續 8 筆停損 −250,305**（低實現波動 →
theta 磨損），是買方性格的真實呈現，非實作錯誤。
註記（不作為翻案理由）：criteria 的 MDD 分母是「初始資金」；若改用業界
常見的「峰值權益」分母則為 18.4%。該定義在看資料前即寫死，事後更換等同
curve-fitting，故判決不變。

四個基準皆不進 Phase 6；**criteria.py 未動過一字**。

## 接下來

1. 四個基準皆未過 criteria → **Phase 6 前提未成立**，paper trade 不啟動
   （鐵律 1：本 repo 一律禁止真實下單，無論回測結果如何）
2. 若要再測新策略：規則必須**先寫死再跑**；且對同一份三年資料的第 N 次測試
   要在報告註明多重比較偏誤（證據力弱於一次就過）
3. 每日隱波監控（M4）持續運行，累積樣本外資料——這是未來真正的驗證機會
4. 掛牌清單回驗 active_expiries、costs.toml 稅率查證、VIX 同向性對照

## 與 SPEC 的差異（誠實記錄）

- 通知：SPEC 寫 LINE，改用 **Discord webhook**（LINE Notify 已於 2025-03 停服；
  tw-stock 系統現行通道就是 Discord）
- Python：SPEC 訂 3.12+，初版在 3.11 開發驗證（語法相容），`requires-python >= 3.11`
- 開發環境連不到期交所：SPEC 第 2 節 facts 沿用規格書內 2026-07-30 的查證結果，
  **Phase 0 收尾時必須重新上網核對**（已於 2026-07-30 完成：A/B/C 現值入 config）
- M3 parser 欄名原取自公開文件；2026-07-30 已用 VM 真實檔驗證全數吻合，
  週五契約代碼 'YYYYMMFn' 依真實樣本擴充（原始檔存 data/samples/）
- 週選 forward：TX 無同到期週期貨，用「到期不早於該週選的最近月 TX」近似
  （帶少量基差誤差；更精確可日後改 put-call parity 反推 implied forward）
- 回測保證金：指數以最近月 TX 結算價近似（非 TAIEX 現貨）；iron condor 收兩邊
  價差保證金（部分期貨商只收單邊，本引擎寧高勿低）；到期前強制平倉故不依賴
  最後結算價資料源
- 2026-07-31 結構修正（一次性，記錄於 config/backtest.toml）：翼寬改 delta 制、
  sizing 改 25% 目標佔用；criteria 未動
- VIX 同向性 sanity 對照（SPEC M4 DoD 之一）尚未實作——需要官方波動率指數
  歷史檔，留待 VM 上有真實資料後補
