# Gmail 收信設定

這個功能只讀取 `noreply@ncku.edu.tw` 的 Moodle 通知信，使用
`gmail.readonly` 權限。它不會寄信、刪信或修改 Gmail。

1. 在 Google Cloud Console 的專案中啟用 Gmail API。
2. 建立 OAuth 同意畫面，將自己的成大 Gmail 帳號加入測試使用者。
3. 建立「桌面應用程式」OAuth Client，下載 JSON。
4. 將檔案命名為 `client.json`，放到專案的 `.gmail-local/`。
5. 啟動本機 App，按「連接成大 Gmail」，選擇成大帳號並同意唯讀權限。

授權完成後，token 只會存在 `.gmail-local/token.json`，該資料夾被 Git
與 Docker 忽略。App 每 60 秒同步一次，也可以按「立即收信」。

明確格式的時間異動會只套用到信件指定日期；原始每週課表不會被改寫。教室、
日期、課名或結束時間有歧義時，通知會保留為待確認。
