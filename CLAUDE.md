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

## 目前進度
- ✅ Phase 0 部分：scaffold、fixtures 落地、config 骨架（A/B/C 為佔位，**未查證**）
- ✅ Phase 1（M1 定價）：P-1/P-2/P-3 綠燈，Greeks closed form vs 有限差分 1e-6 對照
- ✅ Phase 2（M2 合約）：C-1～C-5 綠燈，含假日順延與 active_expiries
- ✅ M5 保證金公式：M-1/M-2 綠燈（A/B/C 現值待填）
- ✅ Phase 3（M3 資料層）：下載器 + fail-loud parser + sqlite + chain
  ——2026-07-30 已於 VM 以真實行情檔驗證欄名；週五契約代碼 YYYYMMFn 據實擴充
- ✅ Phase 4（M4 隱波監控）：smile/ATM IV/term structure/IV rank/25Δ skew、
  事件偵測 → Discord（`txolab monitor`，config/monitor.toml 調門檻）
  ——VIX 同向性 sanity 對照未實作（待真實資料）
- ✅ VM 部署：deploy/（setup_vm.sh + systemd timer 週一~五 15:30 Asia/Taipei）
- ✅ Phase 0 收尾：A/B/C 現值已填（2026-07-30 查證，config/margin.toml）
- ✅ Phase 5（M6 回測）程式面：**criteria.py 已寫死**（2026-07-30 拍板標準組：
  MDD≤20%、保證金佔用峰值≤30%、≥30 筆、扣成本期望值>0、滑價×2 不虧；
  初始資金 100 萬）、逐日重放引擎（結算價±滑價、稅費全含、保證金逐日重算、
  到期前強制平倉、bit-identical）、三基準策略、`txolab backfill / backtest`
- ✅ Phase 5 正式報告（2026-07-31，2023-08~2026-07 三年真實資料，728 交易日）：
  **賣方三基準全數 FAIL criteria**——vertical −2.8萬/MDD 44.8%、
  iron_condor −41.4萬/MDD 47.2%、short_strangle −20.5萬/佔用 32%。
  結論照實記錄：無條件月月賣租在本樣本非正期望值，皆不進 Phase 6。
  （過程中做過一次性結構修正並記錄於 config/backtest.toml：delta 翼寬、25% sizing）
- ✅ B1 買方基準 long_strangle_low_iv（2026-07-31 pre-registered）：
  **5 項過 4 項但 FAIL**——+47.0萬 / 每筆 +15,672 / 滑價×2 +44.7萬 / 佔用 0%，
  唯 MDD 26.5% > 20%（2024-09~2025-02 連續 8 筆停損 −25.0萬）。
  第一個正期望值策略，但規則就是規則，不進 Phase 6；criteria 未動。
- ✅ B2 long_strangle_low_iv_2pct（2026-08-03）：sizing 2% 把 MDD 壓到 8.5%，
  但權利金貴時 lots 歸零 → 25 筆 < 30 **FAIL**（降風險有效、樣本不足）
- ✅ B3 bull_call_spread_trend（2026-08-03）：39 筆 / +110,773 / 每筆 +2,840 /
  MDD 8.3% / 佔用 0% / 滑價×2 +68,369 → **五項全過 PASS**，
  本專案第一個通過 pre-registered criteria 的策略。
  誠實標註：同一份資料第四次測試（多重測試）、樣本為 +154.6% 大多頭而
  200MA 閘門使其結構性做多（可能賺 beta 非 edge）、39 筆屬小樣本。
  **樣本外（2027-02-01 起評估）才是最終仲裁**；criteria 未動。
- ✅ S1 空頭壓力測試（2026-08-03，pre-registered 後才 backfill 2021-01~2023-07）：
  該區間指數 +15.0%、MDD 31.5%、僅 51.5% 日數在 200MA 之上（原樣本 85.3%）。
  B3 於此區間 28 筆 / +5,615 / **每筆 +201（原樣本 +2,840，塌 93%）** / MDD 7.9%。
  判讀：三條預寫規則皆未完全命中——照常交易、不 whipsaw、但幾乎不賺錢。
  結論：**B3 的優勢高度條件於強多頭市況**；不受重傷但無全天候 edge。
  依 pre-registration，S1 不撤銷也不加強已記錄的 PASS；criteria 未動。
  （順帶更正：`backfill` 原記「期交所僅提供近三年」為誤，實測 2022 資料可取得）
- ⬜ Phase 3 剩餘：掛牌清單回驗 active_expiries；costs.toml 稅率 verified_date
- ✅ Phase 6 第一階段（2026-08-03，`txolab paper`）：sqlite 模擬帳本（一次一組部位、
  Decimal 落地、每日冪等、可重啟）+ 每日 runner + Discord 報告。
  **paper/ 完全不含下單程式碼**（比鐵律 1 更嚴：不是靠旗標，是根本沒有那條路徑），
  另有 AST 測試把關。進出場規則抽出為 engine.close_reason / entry_gates_ok /
  size_lots / trade_legs_twd，**回測與 paper trade 共用同一份實作**並有逐欄一致性測試。
  已知限制：日行情無 bid/ask，本階段成交價與回測同源，只驗管線不驗滑價假設。
- ⬜ Phase 6 第二階段：接即時報價（**唯讀**）比對真實 bid/ask 與「結算價 ± 1 tick」
  假設的落差。仍不得出現任何下單路徑。
