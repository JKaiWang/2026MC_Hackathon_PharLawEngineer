# NCKU Smart Commute（成大智慧通勤 Agent）

## 架構

- **Application 層**：`commute_agent/agent.py`（目前為 Step 0 煙霧測試版）
- **Skill 層**：`commute_agent/skills/`（Step 4 加入）
- **Tool 層**：`commute_agent/tools/`，只打 API、不做決策
- **設定**：`api.py`，只讀環境變數，不含任何金鑰

## 安裝（Windows PowerShell）

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # .env 放在專案根目錄，跟 api.py 同一層
```

打開 `.env`，填入 `GEMINI_API_KEY`。

**執行 `adk web` 請用獨立的 PowerShell 視窗**（不要用 VS Code 內建終端機），
VS Code 的 Python 擴充功能可能會干擾長時間執行的伺服器程序。

## 驗證

```powershell
python -m pytest -q                              # 單元測試，不需網路與金鑰（目前 410 項）
python scripts/smoke_ncku_gis.py 4264 65304 格致廳   # 打真實成大教室 GIS
python scripts/smoke_tdx_road_events.py           # 打真實 TDX 路況事件（需 TDX_CLIENT_ID/SECRET）
adk web                                          # 開 http://localhost:8000
```

在 `adk web` 問「4264 教室在哪？」，左側 Events/Trace 應看到 `lookup_room` 的真實呼叫。
要打真實 API，把 `.env` 的 `PROVIDER_MODE` 改成 `live`。

## 目前已完成的 Tool

| Tool | 檔案 | 說明 |
| --- | --- | --- |
| `lookup_room` | `tools/ncku_room.py` | 教室代碼／名稱 → 大樓與樓層 |
| `get_parking_availability` | `tools/ncku_parking.py` | 校區＋車種 → 即時剩餘車位（依車位數排序） |
| `build_route_link` | `tools/route_link.py` | 目的地 → Google Maps 導航連結，可選步行／自行車／機車開車／大眾運輸（純函式，不需金鑰） |
| `get_next_class` | `tools/class_schedule.py` | 讀 `data/class_schedule.json`，依現在時間算出正在上的課與下一堂 |
| `get_building_location` | `tools/ncku_geo.py` | 大樓名稱 → 經緯度，另含距離與時間估算的純函式 |
| `plan_parking` | `skills/parking_plan.py` | 目的地大樓＋車種 → 最近且車位足夠的停車場（Skill 層，會串多個 Tool） |
| `estimate_trip` | `skills/trip_plan.py` | 起點＋目的地 → 距離與估算時間，起點在校外時明確回報無法估算 |
| `get_road_events` | `tools/road_events.py` | 座標＋半徑 → 周邊 TDX 即時路況事件（車禍、施工、封閉），需 `TDX_CLIENT_ID`/`TDX_CLIENT_SECRET` |
| `check_route_events` | `skills/road_watch.py` | 起點＋目的地 → 各自周邊有沒有路況事件（Skill 層，串 `resolve_place` 與 `get_road_events`） |
| `estimate_trip` | `skills/trip_plan.py` | 起點＋目的地 → 距離與時間，並標明來源是估算還是實際路線 |
| `plan_ride_and_walk` | `skills/trip_plan.py` | 騎車行程拆成「騎到停車場」＋「走到教室」兩段，整趟只呼叫一次 Google |
| `get_travel_time` | `tools/travel_time.py` | 時間來源的統一接口，依設定選 `estimate` 或 Google Routes，後者失敗會自動退回前者 |
| `compute_route` | `tools/google_routes.py` | Google Routes API，真實路線時間（需金鑰、會計費） |
| `plan_departure` | `skills/departure_plan.py` | 倒推「該幾點出發」，含進教室緩衝；考試與報告自動加長緩衝 |
| `plan_next_transition` | `skills/class_transition.py` | 課間轉場：正在上課或剛下課時，從上一堂教室走到下一堂來不來得及；用 GIS 座標估算，不呼叫計費 API |
| `replan_commute` | `skills/replan.py` | 環境變化後重新規畫：檢查目前的交通方式還行不行，不行就換並說明原因；先只檢查目前這一種，有問題才比較全部（省計費的路線查詢） |
| `get_rain_now` | `tools/rain_observation.py` | 雨量站即時觀測（實測，不是預報），與天氣預報共用 `CWA_API_KEY` |
| `draft_late_notice`、`draft_leave_notice` | `skills/late_notice.py` | 遲到／請假通知信草稿：只產生、不寄出；原因與署名由使用者自己填，不代編 |
| `check_attendance_now` | `skills/attendance.py` | 出席檢查：課堂進行中，比較使用者輸入的位置（或即時定位）與上課大樓，不在就算遲到或缺席，並建議寫遲到信還是請假信；只判斷到「大樓附近」 |
| （網頁）日曆匯出 | `skills/calendar_export.py` | 「加到 Google 日曆」連結與 `.ics` 下載；不走 Calendar API，不需要授權 |
| （網頁）拍門牌 | `skills/scan_room_sign.py` | 拍教室門牌，Gemini 讀字、成大 GIS 驗證，說明目標教室相對位置 |
| `get_bike_status` | `tools/youbike.py` | 某地點附近 YouBike 可借車輛或可還空位（免金鑰） |
| `get_weather` | `tools/weather.py` | 中央氣象署臺南市鄉鎮預報，降雨機率與體感溫度（需 `CWA_API_KEY`） |
| `get_bus_eta` | `tools/tdx_bus.py` | TDX 臺南市公車即時到站（需 `TDX_CLIENT_ID`／`SECRET`） |
| `guess_building` | `tools/building_match.py` | 本機 Gemma 對著 `data/ncku_buildings.json`（180 棟）判斷模糊地點文字是哪一棟，答案對回清單才算數 |
| `locate_course_place` | `skills/locate_place.py` | 課表地點 → 座標。順序：教室代碼 → GIS 原文 → **本機 Gemma** → 雲端 Gemini → Google，愈前面愈可信 |
| `classify_course_mail` | `tools/course_mail.py` | 教授／助教的信 → 教室異動／停課／改線上／考試／報告（**只用本機 Gemma**，信不上雲端） |
| `apply_course_mail` | `skills/mail_update.py` | 讀信 → 對到課表哪門課 → 新教室經 GIS 驗證 → 提出 patch；只提議不改課表，`needs_confirmation` 永遠 True |
| `normalize_road_text` | `tools/road_text.py` | TDX 路況的髒地點文字（`null北外環…`）→ 行政區／路名／路段（本機 Gemma） |
| `send_push` | `tools/push_notify.py` | 推一則通知到手機（ntfy）；fixture 模式回 dry_run 不真的送 |
| `notify_departure` | `skills/departure_notify.py` | `plan_departure` 的結論變成手機推播：該出發＝high、來不及＝urgent、還早＝不吵人 |

### 為什麼用本機 Gemma（Ollama）

課表、教授信、住家附近的描述是個資。能在自己機器上讀完，就不必送雲端；
沒網路或 Gemini 額度用完時也還能動。這是題目要 Gemma 的理由（行動端／離線／隱私），
不是為了湊模型數。分工原則：**個資類文字 → 本機 Gemma；公開的城市資料（路況、
天氣、公車）→ 雲端 Gemini**。

```powershell
winget install Ollama.Ollama
ollama pull gemma3:4b                     # 3.3GB，CPU 也跑得動，每筆約 4–8 秒
python scripts/build_ncku_buildings.py     # 重撈大樓清單（已附一份，可不跑）
python scripts/smoke_gemma_locate.py       # 拿真實課表寫法試，順便走完 GIS 驗證
```

本機 Gemma 現在做三件事，都有 smoke 腳本：
`scripts/smoke_gemma_locate.py`（地點→大樓）、`scripts/smoke_course_mail.py`（讀信，
範例信在 `fixtures/course_mail/`，是手寫的、不是真信）、`scripts/smoke_road_text.py`（路況文字）。

2026-09-20 用 gemma3:4b 對十筆真實輸入實測：九筆對，校外地址兩筆都正確拒絕；
唯一錯的「資訊大樓格致廳小講堂」在流程裡會先被 GIS 教室查詢接走，輪不到模型。
模型只准挑清單裡的名字，回的名稱對不回清單就當編造；座標一律由 GIS 提供，
模型不准講經緯度。Ollama 沒起來時 `guess_building` 回 `unavailable`，整條流程
自動退回雲端 Gemini，不會炸。

### 騎車行程為什麼要拆兩段

Google 只會算到大樓門口的騎車時間，但實際上得先停車再走過去。
`plan_ride_and_walk` 因此把行程拆開：

| 路段 | 來源 | 花錢 |
| --- | --- | --- |
| 出發地 → 停車場 | Google Routes API（真實路網） | 是，整趟一次 |
| 停車場 → 教室 | 成大 GIS 座標估算 | 否 |

送給 Google 的是**座標**而不是名稱——校內大樓帶編號前綴（`B406 三系館鋼構區`）
時 Google 常對到隔壁棟，實測就曾被解析成材料系館。座標由免費的 GIS 提供。

騎車段會明確指定走 Google，不受 `TRAVEL_TIME_PROVIDER` 影響——那段本來就是
付費才有意義的部分。走路與自行車則交給預設的 `auto` 自行判斷要不要花錢。

### 手機推播（ntfy）

1. 手機裝 ntfy app（iOS／Android），訂閱一個自己取的長隨機 topic。
2. `.env` 填 `NTFY_TOPIC=<同一個 topic>`（topic 就是收件位址，當秘密保管）。
3. 網頁按「推播到手機」，或問 agent「該走的時候通知我手機」。
   `PROVIDER_MODE=fixture` 時回 `dry_run`，不會真的送。

只在需要打斷人的時候推：時間還很充裕會回 `skipped`；同一堂課同一個結論有
`dedupe_key`，輪詢時不重推。之後上 Cloud Run 有 HTTPS 再加 Web Push 也不衝突。

## 課表視覺化頁面

```powershell
./.venv/bin/python -m web.server     # http://localhost:8080
```

顯示本週課表、下一堂課與導航連結，可切換交通模式；選「機車／開車」時會
自動比對全校停車場，推薦最近且剩餘車位 ≥ 30 的那一個。

出發地可以在頁面上自己填（例如「成大圖書館」），留空則用 `.env` 的
`DEFAULT_ORIGIN`。校內地點用免費估算，校外地址（住家、車站）會自動改用
Google 的實際路線，介面會標明這次的時間是估算還是實際路線。

## 模擬情境與自動調整（Demo 用）

> 目前前端**預設隱藏**這組功能（操作不夠直觀）；後端、測試與 Agent 工具都還在。
> 要打開，把 `web/index.html` 的 `SHOW_ADAPTIVE` 改成 `true`。

真實資料不會在 Demo 現場剛好下大雨或把 YouBike 借光，所以頁面的「展示控制」有四個情境按鈕：
**豪雨、YouBike 歸零、停車場全滿、公車停駛**。它們只在 Tool 的回傳值上覆寫被指定的那一項
（`commute_agent/scenario.py`），其餘仍是真實查詢，畫面會標示「模擬」。
情境只綁在**當次請求**（前端每次帶 `scenario=`），後端不保存，Cloud Run 多實例也不會互相干擾。

勾選「自動調整交通方式」後，每次更新都會呼叫 `/api/replan`（`skills/replan.py`）：

- **硬失效**（一定換）：借不到車、停車場全滿、會遲到、公車沒班次。
- **軟風險**（有更好的才換）：會淋雨、車位偏少；要有「風險明顯更少、又不會慢超過 10 分鐘」的替代方案才換，避免降雨機率在門檻上下跳時來回切換。
- **省錢**：每輪先只檢查目前這一種；出現硬失效或「沒見過的新風險」才比較全部。前端把已接受的風險（`known_soft`）帶回來，同樣的風險不會每輪重算。
- 換了交通方式會記在「調整記錄」，並說明原因；決策全是確定性程式，不用 Gemini 額度。
- 模擬時間下不採用「真實現在」的即時觀測（雨量、公車），除非明確啟用對應情境。

## 已知的資料限制

- **時間有兩個來源**，由 `.env` 的 `TRAVEL_TIME_PROVIDER` 決定策略：
  - `auto`（預設，建議）：免費的先試，答不出來才花一次 Google。校內兩點走路
    騎車不計費，只有校外起點與大眾運輸這種免費算不出來的情況才付費。
  - `estimate`：直線距離乘 1.3 繞路係數再除以平均速度。免費、免金鑰，
    但只涵蓋校內地點，而且偏樂觀（圖書館→資訊系館估 4 分，實際路線是 6 分）。
  - `google`：Google Routes API，真實路網與大眾運輸班次，起訖點可以是任意地址，
    因此校外住家也算得出來。需要 `GOOGLE_MAPS_API_KEY` 且**會計費**。
    金鑰缺失或 API 失敗時會自動退回 `estimate`，並在 `fallback_reason` 說明原因

  上層一律呼叫 `tools/travel_time.py` 的 `get_travel_time`，不必知道來源是哪個；
  回傳的 `is_estimate` 用來決定介面要不要寫「約」。
- **`estimate` 來源只算得出校內起點**：距離來自成大 GIS 的大樓座標，校外地址
  （住家、火車站）查不到。預設的 `auto` 會在這種情況自動改用 Google，
  所以校外起點仍算得出來；硬設成 `estimate` 才會回 `can_estimate=False`。
  任何情況下都不會拿附近大樓的座標充數。
- **6 個停車場沒有座標**：校門（光復前門、成功前門、勝利後門）、路名（林森路）
  與成杏校區兩個停車場在 GIS 查不到同名大樓，不會被硬填座標，排序時排最後。
  重建對照表：`./.venv/bin/python scripts/build_parking_locations.py`
- **兩套校區代碼不相通**：GIS 的 `campusId` 是大樓編號前綴（A104 → A），
  與停車系統的 `CAMPUS_CODES`（A=光復、B=成功…）不是同一套，不可互相套用。
- **路況事件是即時 feed，fixture 只是某一刻的快照**：`parse_road_events`
  （`tools/road_events.py`）已對真實 TDX 端點驗證過（2026-09-19），欄位命名
  不是猜的；但事件內容本身一直在變，`fixtures/ncku_traffic/Tainan.json`
  錄製當下成大周邊 500m 內剛好沒有事件，這是真實結果不是抓錯半徑，細節見
  `fixtures/ncku_traffic/README.md`。事件的分類代碼（`type_code`）沒有官方
  對照表，只在連 `description`／`category` 都沒有時才會是 `type_is_code=True`
  （目前實測沒遇過），這種情況不要自己編一個中文分類名稱。

## 對照 CampusPulse 規劃的進度

`CampusPulse.md` 描述的 Agent Loop 是 Perception → Planning → Action → Reflection。
目前完成到 Planning，Action 只做到「產生導航連結」，Reflection 尚未開始。

| CampusPulse 規劃的 Function | 狀態 | 備註 |
| --- | --- | --- |
| 課表理解 | 完成（JSON） | 截圖辨識尚未做，目前靠手動維護 `data/class_schedule.json` |
| 路線與時間 | 完成 | `estimate_trip`、`plan_ride_and_walk` |
| 出發時間推算 | 完成 | `plan_departure` |
| `get_bike_status()` | 完成 | YouBike 2.0 官方端點，免金鑰 |
| `get_weather()` | 完成 | 資料集 `F-D0047-077`（臺南市鄉鎮），不是縣市層級的 `-089` |
| `get_bus_eta()` | 完成 | 到站端點不支援 `nearby`，改以站牌 UID 過濾 |
| `get_flood_sensors()` | 未做 | 水利署，需註冊 |
| `get_air_quality()` | 未做 | 環境部，需註冊 |
| `read_course_email()`／`send_email()` | 未做 | Gmail API，需 OAuth |
| `update_schedule()` | 未做 | Google Calendar API，需 OAuth |
| 天氣影響出發建議 | 完成 | `plan_departure` 會看出發當下的降雨機率，騎車淋雨時建議改公車 |
| 持續監控與自動重新規畫 | 未做 | 目前都是使用者主動詢問才執行，還不會自己盯著環境變化 |

## 已完成

- 室內樓層平面圖（`buildinfo.htm?action=getBoundByBuildId` 等，資料格式待確認）
- 課表截圖辨識

## 團隊規範

分支、commit 與 PR 規範沿用 CampusPulse 的 CONTRIBUTING.md。
Gemini key 只能在後端使用；不要透過截圖、聊天或 PR 傳遞任何金鑰。
