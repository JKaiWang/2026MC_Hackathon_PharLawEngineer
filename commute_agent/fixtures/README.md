# Fixtures

這裡的檔案都是**真實 API 回應的原始錄製**，不是手寫的假資料。
`PROVIDER_MODE=fixture` 時，Tool 會讀這裡的檔案而不打網路。

| 檔案 | 來源 | 錄製日期 |
| --- | --- | --- |
| `ncku_gis/roominfo/4264.json` | `db.nckumap.ncku.edu.tw/nckugis/public/roominfo.htm?q=4264` | 2026-09-19 |
| `ncku_gis/roominfo/65304.json` | `db.nckumap.ncku.edu.tw/nckugis/public/roominfo.htm?q=65304` | 2026-09-19 |
| `ncku_gis/buildinfo/%E8%B3%87...%E9%A4%A8.json`（查詢字「資訊工程系館」網址編碼後的檔名） | `db.nckumap.ncku.edu.tw/nckugis/public/buildinfo.htm?q=資訊工程系館` | 2026-09-19 |
| `ncku_parking/C_moto.html` | `apss.oga.ncku.edu.tw/park/index.php/park11215/read`（campus=C 自強, tab=moto 機車） | 2026-09-19 |
| `ncku_parking/G_moto.html` | 同上（campus=G 勝利, tab=moto）；真實回應為「查無符合條件之停車場資料」 | 2026-09-19 |

新增錄製：`python scripts/smoke_ncku_gis.py --record <查詢字>`

資料著作權屬國立成功大學（總務處資產保管組），僅供本專案開發測試使用，勿另行散布。

**檔名編碼說明**：fixture 檔名一律用 `urllib.parse.quote(查詢字, safe='')`
處理過的網址編碼，不直接用中文檔名。原因：曾在 Linux 打包 zip、Windows
解壓縮的流程中，因為 zip 檔沒有正確標記 UTF-8 旗標，導致中文檔名在
Windows 上被解成亂碼，程式找不到檔案。改用網址編碼後，檔名一律是純英數
字元與 `%`，任何系統、任何壓縮工具都不會出錯。新增 fixture 時請沿用
這個規則，不要直接用中文字存檔。
