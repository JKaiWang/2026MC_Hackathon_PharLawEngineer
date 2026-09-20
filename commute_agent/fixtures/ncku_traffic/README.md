# ncku_traffic fixtures

`Tainan.json` 是 TDX 路況事件（RoadEvent LiveEvent）的**真實錄製回應**，
跟 `fixtures/README.md` 的規則一致：不是手寫的假資料。

| 檔案 | 來源 | 錄製日期 |
| --- | --- | --- |
| `Tainan.json` | `https://tdx.transportdata.tw/api/basic/v1/Traffic/RoadEvent/LiveEvent/City/Tainan`（OAuth2 client_credentials） | 2026-09-19 |

錄製指令：`python scripts/smoke_tdx_road_events.py --record`

**跟其他 fixture 不一樣的地方：這是即時事件 feed，內容本來就會一直變。**
錄製當下（2026-09-19 約 19:59）台南市共 30 筆事件，距成大成功校區
（22.9997, 120.2220）最近的一筆約 1.1 公里（機車事故，怡東路），
所以 `DEFAULT_RADIUS_M=500` 查校區周邊在這個快照裡會是 0 筆——這是真實
資料，不是 bug。測試因此用較大的半徑（1500–2000m）驗證有抓到事件，
用 500m 驗證「附近沒事件」這個誠實的空結果。重錄一次幾乎不可能拿到
一模一樣的事件內容，這是預期中的事，只有格式（欄位、WKT 座標寫法）
應該保持穩定。

**格式重點**（寫在 `commute_agent/tools/road_events.py` 開頭有更完整說明）：
- 事件陣列在頂層物件的 `LiveEvents` 底下。
- 座標在 `Positions` 欄位，WKT 字串 `"POINT (經度 緯度)"`，不是巢狀物件。
- 地點文字在 `Location.Other`。
- `Description` 錄到的 30 筆全部都有現成中文說明；少數開頭有字面上的
  `"null"`（例如 `"null北外環…"`），是台南市交通局來源資料本身的問題，
  本檔原樣帶出、不做清洗。
- `EventType`/`EventSubType` 是數字碼，沒有官方中文對照表，不轉譯。
