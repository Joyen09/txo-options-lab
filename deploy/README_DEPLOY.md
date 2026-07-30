# 部署到 VM（每日收盤後自動監控 + Discord 通知）

跟 tw-stock-strategy-framework 一樣是「跑一次→算完→退出」的排程程式，
不是常駐 bot——平常 CPU/記憶體都是 0，`e2-micro` 等級就夠，可與現有 VM 共用。

## 步驟一：放上 VM 並安裝

```bash
git clone <你的 repo 網址> ~/txo
cd ~/txo
bash deploy/setup_vm.sh        # venv + 安裝 + 產生 .env 樣板
```

## 步驟二：設 Discord webhook 並測試

1. Discord 選頻道 → 頻道設定 ⚙ → 整合 → Webhook → 新增 → 複製 URL
2. 填進 `~/txo/.env`：`DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."`
3. 測試：

```bash
cd ~/txo && set -a && source .env && set +a
./.venv/bin/txolab notify-test        # Discord 頻道應出現測試訊息
```

## 步驟三：首次抓資料（= parser 格式驗證，重要）

repo 的 parser 是在連不到期交所的環境寫的（欄名取自公開文件），
**第一次 fetch 就是格式驗證**（CLAUDE.md 鐵律 6 的防呆會嚴格把關）：

```bash
./.venv/bin/txolab fetch --days 5     # 抓近幾個交易日
./.venv/bin/txolab chain              # 看 chain 摘要（forward / T / ATM IV）
./.venv/bin/txolab monitor --dry-run  # 印出日報訊息（不送 Discord）
```

- fetch 失敗會列出「實際表頭 vs 預期欄位」——照訊息修
  `src/txolab/data/parser.py` 的 COLUMN_ALIASES（原始檔已自動留在
  `data/samples/`，就是 parser 的開發基準）
- 若看到「未知到期代碼」（例如週五契約），拿 data/samples 檔案裡的實際代碼
  擴充 `expiry_code_to_date`

IV rank/percentile 需要歷史窗口（預設 60 個交易日）——上線初期 rank 會是
n/a，累積夠天數後自動開始計算。

## 步驟四：Phase 0 收尾（第一次上 VM 時順手做）

VM 連得到期交所，把沙盒做不到的查證補完：

1. https://www.taifex.com.tw/cht/2/tXO 核對 SPEC.md 第 2 節 facts
2. https://www.taifex.com.tw/cht/5/indexMarging 抄 A/B/C 現值進
   `config/margin.toml`，填 `verified_date`
3. https://www.taifex.com.tw/cht/4/feeSchedules 核對期交稅率 → `config/costs.toml`

## 步驟五：排程（systemd timer，週一~五 15:30 台北時間）

```bash
sed "s/YOUR_USER/$(whoami)/g" deploy/txolab-daily.service | sudo tee /etc/systemd/system/txolab-daily.service
sed "s/YOUR_USER/$(whoami)/g" deploy/txolab-daily.timer   | sudo tee /etc/systemd/system/txolab-daily.timer
sudo systemctl daemon-reload && sudo systemctl enable --now txolab-daily.timer

systemctl list-timers txolab-daily.timer   # 確認下次觸發時間
sudo systemctl start txolab-daily.service  # 手動跑一次驗證全流程
journalctl -u txolab-daily -n 50           # 看 log
```

沒有 systemd 的環境用 cron 等價（VM 時區非台北時，記得換算）：

```cron
30 15 * * 1-5 cd /home/YOUR_USER/txo && ./.venv/bin/txolab fetch && ./.venv/bin/txolab monitor >> monitor.log 2>&1
```

## 日常維運

- 監控訊息門檻在 `config/monitor.toml`（rank 高低檔、單日跳升、每日摘要開關）
- 補抓漏掉的日子：`txolab fetch --date 2026-08-03`
- DB 在 `data/db/txolab.sqlite`（gitignore）；`data/samples/` 的原始 CSV
  建議保留——它是 parser 的規格基準
