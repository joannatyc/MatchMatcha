# 🍵 丸久小山園抹茶補貨 LINE 通知

每 5 分鐘檢查一次丸久小山園國際網路商店的抹茶頁面，一有商品從「缺貨」變成「有貨」，就傳 LINE 訊息給你。

> LINE Notify 已在 2025 年停止服務，所以這裡用的是 **LINE Messaging API**（LINE 官方帳號），個人使用免費。

---

## 第 1 步：建立 LINE 機器人（約 10 分鐘）

1. 到 [LINE Developers](https://developers.line.biz/console/)，用你的 LINE 帳號登入。
2. 建立一個 **Provider**（名稱隨意，例如 `matcha`）。
3. 在 Provider 底下建立 **Messaging API channel**（依畫面指示建立 LINE 官方帳號，名稱例如「抹茶補貨通知」）。
4. 進入這個 channel：
   - **Basic settings** 分頁最下面有 **Your user ID**（`U` 開頭的一串），複製下來 → 這是 `LINE_USER_ID`
   - **Messaging API** 分頁最下面的 **Channel access token (long-lived)** 按 Issue，複製 → 這是 `LINE_CHANNEL_ACCESS_TOKEN`
   - 同一頁有 QR code，**用手機掃描把機器人加為好友**（沒加好友就收不到訊息）

## 第 2 步：放到 GitHub 自動執行（免費）

1. 註冊 / 登入 [GitHub](https://github.com)，建立一個新的 repository（建議選 **Private**）。
2. 把這個資料夾的所有檔案上傳進去（包括 `.github/workflows/monitor.yml`）。
   - 網頁上傳時，`.github` 資料夾可能看不到：可以在 repo 裡按 **Add file → Create new file**，檔名輸入 `.github/workflows/monitor.yml`，再把內容貼上。
3. 到 repo 的 **Settings → Secrets and variables → Actions**：
   - **Secrets** 分頁按 **New repository secret**，新增兩個：
     - `LINE_CHANNEL_ACCESS_TOKEN`
     - `LINE_USER_ID`
   - （選填）**Variables** 分頁新增 `WATCH_KEYWORDS`，例如 `Wakatake,Aoarashi,Isuzu`，就只會通知名稱有這些字的商品（不分大小寫）。不設就全部通知。
4. 到 **Actions** 分頁，如果有提示就按啟用，點左邊 **Matcha restock monitor → Run workflow** 手動跑一次。
5. 幾十秒後你應該會收到 LINE：「🍵 抹茶補貨監測已啟動…」。之後它會每 5 分鐘自動檢查。

## 收到的訊息長這樣

```
🍵 丸久小山園補貨了！（2026-11-28 09:35）

• Wakatake
  https://www.marukyu-koyamaen.co.jp/english/shop/products/...

快去搶 👉 https://www.marukyu-koyamaen.co.jp/english/shop/products/catalog/matcha
```

同一款商品只有在「缺貨 → 有貨」那一刻才會通知一次，不會一直洗版。

---

## 如果 GitHub 上一直失敗（被網站擋）

有些網站會擋雲端主機的連線。如果連續約 1 小時都讀不到，機器人會傳一則 ⚠️ 警告。這時改在**自己的電腦**上跑（網站比較不會擋一般家用網路）：

```bash
pip install -r requirements.txt

# Mac / Linux
export LINE_CHANNEL_ACCESS_TOKEN="你的token"
export LINE_USER_ID="U開頭的ID"
# Windows PowerShell 請改用：$env:LINE_CHANNEL_ACCESS_TOKEN="你的token"

python monitor.py --test       # 先測試 LINE 是否收得到
python monitor.py --debug      # 看看抓到哪些商品、判斷有沒有貨
python monitor.py --loop 300   # 每 5 分鐘檢查一次（電腦要開著）
```

## 檢查判斷對不對

第一次使用建議跑 `python monitor.py --debug`，然後打開網頁對照：

- 列出的商品名稱和網頁上一樣嗎？
- ✅ / ❌ 跟網頁上的庫存狀態一致嗎？

如果出現很多 ❔ 不確定，代表網站的版面跟程式預期的不同，可以把 `--debug` 的輸出（或網頁原始碼）傳給 Claude 調整。

## 其他說明

- **只想盯特定商品頁**：在 Variables 新增 `WATCH_URLS`，填商品頁網址，多個用逗號分隔。
- **GitHub 排程延遲**：GitHub 免費排程在尖峰時段可能晚幾分鐘才跑，不是每次都剛好 5 分鐘。
- **60 天限制**：repo 太久沒有任何更新的話，GitHub 可能會自動停用排程，屆時到 Actions 頁面重新啟用即可。
- **LINE 訊息額度**：免費方案每月有推播則數上限，補貨通知量很少，一般用不完；詳細額度以 LINE 官方帳號後台為準。
- 請不要把檢查頻率調得太密（例如每分鐘），對網站不友善，也可能被封鎖。
