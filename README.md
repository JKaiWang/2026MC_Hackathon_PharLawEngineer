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
python -m pytest -q                              # 單元測試，不需網路與金鑰（目前 47 項）
python scripts/smoke_ncku_gis.py 4264 65304 格致廳   # 打真實成大教室 GIS
adk web                                          # 開 http://localhost:8000
```

在 `adk web` 問「4264 教室在哪？」，左側 Events/Trace 應看到 `lookup_room` 的真實呼叫。
要打真實 API，把 `.env` 的 `PROVIDER_MODE` 改成 `live`。

## 目前已完成的 Tool

| Tool | 檔案 | 說明 |
| --- | --- | --- |
| `lookup_room` | `tools/ncku_room.py` | 教室代碼／名稱 → 大樓與樓層 |
| `lookup_building` | `tools/ncku_building.py` | 全校任一建築物名稱／關鍵字查詢，不限於已知教室 |
| `get_parking_availability` | `tools/ncku_parking.py` | 校區＋車種 → 即時剩餘車位（依車位數排序） |
| `estimate_travel_time` | `tools/travel_time.py` | 起點＋終點＋車種 → 真實通勤時間（**需要 `GOOGLE_MAPS_API_KEY`，未設定時誠實回報 unavailable，絕不編造數字**） |
| `build_walking_link` | `tools/walking_link.py` | 大樓名稱 → Google Maps 步行導航連結（純函式，不需金鑰，僅作為補充選項） |

Agent 的設計原則：不是查一個地點就丟一個地圖連結給使用者，而是主動比較
機車／汽車的即時停車位與（有金鑰時的）真實通勤時間，用文字給出「建議騎
什麼車、停哪裡、怎麼走」的具體結論，Google Maps 連結只作為想要即時路況
導航時的補充選項。

## 團隊 `.env` 設定注意事項

每個人請執行 `Copy-Item .env.example .env`，**只需要改 `GEMINI_API_KEY` 這一行**，
其他項目都有預設值，不要自行更動，以免大家本機行為對不上。
**每個人請申請自己的 Gemini API Key**，不要共用同一把：AI Studio 免費額度是
以帳號為單位計算，共用會互搶額度，demo 當天容易被限流。

## 尚未完成

- 室內樓層平面圖（`buildinfo.htm?action=getBoundByBuildId` 等，資料格式待確認）
- 課表截圖辨識
- Skill 層（`SkillToolset`）與自我修正 loop

## 團隊規範

分支、commit 與 PR 規範沿用 CampusPulse 的 CONTRIBUTING.md。
Gemini key 只能在後端使用；不要透過截圖、聊天或 PR 傳遞任何金鑰。
