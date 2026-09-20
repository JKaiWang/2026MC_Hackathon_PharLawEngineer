"""課表視覺化頁面的後端。

這層做的是「串接」：先問課表下一堂是什麼，再用教室代碼查大樓，最後組導航連結。
這其實就是 README 規劃中 Skill 層要做的事，等 SkillToolset 上線後可以搬過去；
在那之前先放這裡，Tool 層維持各自獨立、互不呼叫。

課表來源分兩份，刻意不共用：
- `data/class_schedule.json` 是版控裡的範例，給 Agent 工具與測試用，本頁不會寫它；
- 使用者上傳辨識出來的課表寫進 `USER_SCHEDULE_PATH`（預設 data/user_schedule.json），
  沒有這個檔時頁面就是空的 —— 「預設空課表，上傳後才有東西」是刻意的。

啟動：
    ./.venv/bin/python -m web.server        # http://localhost:8080
"""

from __future__ import annotations

import json
import os
import asyncio
import hashlib
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from api import load_settings
from commute_agent.tools.class_schedule import PROJECT_ROOT, find_classes, load_courses
from commute_agent.tools.floor_plan import PICTURE_DIR, PICTURE_URL_PREFIX
from commute_agent.tools.ncku_floorplan import floor_plan_url
from commute_agent.tools.ncku_room import lookup_room
from commute_agent.tools.schedule_ocr import OCRError, extract_schedule_from_image
from commute_agent.skills.bike_plan import plan_bike_journey
from commute_agent.skills.campus_walk import plan_campus_walk
from commute_agent.scenario import SCENARIOS, active as active_scenarios
from commute_agent.scenario import parse_names as parse_scenarios, use as use_scenarios
from commute_agent.skills.calendar_export import build_google_calendar_url, build_ics
from commute_agent.skills.class_transition import find_transition
from commute_agent.skills.classroom_guide import locate_classroom
from commute_agent.skills.attendance import check_attendance
from commute_agent.skills.late_notice import build_late_notice, build_leave_notice
from commute_agent.skills.replan import replan
from commute_agent.skills.scan_room_sign import confirm_room, scan_room_sign
from commute_agent.tools.rain_observation import get_rain_now
from commute_agent.skills.locate_place import locate_course_place
from commute_agent.skills.departure_notify import notify_departure
from commute_agent.skills.departure_plan import plan_departure
from commute_agent.skills.mail_update import apply_course_mail
from commute_agent.skills.recommend_plan import recommend_plan
from commute_agent.skills.parking_plan import load_lot_locations, plan_parking
from commute_agent.skills.road_watch import check_route_events
from commute_agent.skills.trip_plan import estimate_trip
from commute_agent.tools.tdx_bus import get_bus_eta
from commute_agent.tools.youbike import get_bike_status
from commute_agent.tools.route_link import TRAVEL_MODE_LABELS, build_route_link
from commute_agent.tools import gmail_sync
from commute_agent.tools.schedule_changes import changes, record, undo, overlay

WEB_DIR = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(app):
    async def poll():
        while True:
            if user_schedule_path().is_file() and gmail_sync.token_path().is_file():
                await asyncio.to_thread(gmail_sync.sync, user_schedule_path())
            await asyncio.sleep(60)
    task = asyncio.create_task(poll())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="NCKU Smart Commute", lifespan=lifespan)


@app.middleware('http')
async def local_mail_access(request: Request, call_next):
    if request.url.path.startswith(('/api/gmail', '/api/mail')):
        if request.client.host not in ('127.0.0.1', '::1', 'testclient') or request.url.hostname not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'error': '信箱功能目前限本機使用'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': '不接受跨網站操作'}, status_code=403)
    return await call_next(request)

# 平面圖截圖放在版本庫的 picture/，直接以靜態檔供應；資料夾不存在時不掛，
# 免得整個服務起不來（這些圖是選配，沒有圖頁面照常運作）
if PICTURE_DIR.is_dir():
    app.mount(PICTURE_URL_PREFIX, StaticFiles(directory=PICTURE_DIR), name="picture")


def _abs_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def user_schedule_path() -> Path:
    """使用者上傳辨識出來的課表位置。內含個人課表，已被 .gitignore 擋掉。"""
    return _abs_path(os.getenv("USER_SCHEDULE_PATH", "data/user_schedule.json").strip()
                     or "data/user_schedule.json")


def sample_schedule_path() -> Path:
    """版控裡的範例課表。只讀不寫，作為 demo 時辨識失敗的備援。"""
    return _abs_path(load_settings().class_schedule_path)


def resolve_now(raw: str | None, tz: str) -> tuple[datetime, bool, str | None]:
    """決定「現在」是幾點。

    回傳 (時間, 是否為模擬時間, 錯誤訊息)。前端的時間模擬會傳 ISO 字串進來，
    讓 demo 可以跳到上課前幾分鐘 —— 查的仍是真實 API，只是換個時間點問，
    所以不會有假資料，但畫面上必須標示出來，這就是第二個回傳值的用途。
    """
    zone = ZoneInfo(tz)
    if not (raw or "").strip():
        return datetime.now(zone), False, None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return datetime.now(zone), False, f"無法解析的時間格式：{raw!r}"
    # 前端的 datetime-local 不帶時區，一律當成設定裡的時區
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed, True, None


BUILDING_MARKERS = ("大樓", "系館", "校區", "學院", "館")


def _needs_building_correction(location: str, resolved_building: str) -> bool:
    """Only flag a correction when the timetable explicitly names a building.

    Labels such as ``共同教室-A1302`` identify a room, not a building.  GIS
    may still enrich them with the building and floor, but that is not a
    correction to the timetable's location.
    """
    original = (location or "").strip()
    resolved = (resolved_building or "").strip()
    if not original or not resolved or resolved in original:
        return False
    return any(marker in original for marker in BUILDING_MARKERS)


def _resolve_building(entry: dict) -> dict:
    """用教室代碼查出真正的大樓，查不到就退回課表上的原始寫法。

    課表寫的大樓名稱不一定正確（例如「資訊系館」實際可能是 B501 或 B502），
    所以一律以 lookup_room 的 building_name 為準，並標記是否做過修正。
    """
    enriched = {**entry, "building_name": "", "floor": "", "lookup_status": "skipped",
                "corrected": False}
    query = entry.get("room_query")
    if not query:
        enriched["lookup_status"] = "no_room_code"
        return enriched

    result = lookup_room(query)
    enriched["lookup_status"] = result["status"]
    exact = [c for c in result.get("candidates", []) if c["exact_match"]]
    chosen = exact[0] if exact else (result.get("candidates") or [None])[0]
    if chosen:
        enriched["building_name"] = chosen["building_name"]
        enriched["floor"] = chosen["floor"]
        # 課表寫「資訊系館」但實際是「B501 資訊工程系館」，值得提醒使用者
        enriched["corrected"] = _needs_building_correction(
            entry.get("location", ""), chosen["building_name"])
    return enriched


def _with_route(entry: dict | None, origin: str, travel_mode: str) -> dict | None:
    """把課表上那一行變成可以按下去導航的目的地。

    地點一律先過 locate_course_place：課表寫的「社科院大樓階梯教室－心理」
    這種字串，Google 會對到名字相近的別棟，成大 GIS 則根本查不到，
    那支會依序用教室代碼、GIS、Gemini 讀出的關鍵字去換出經過驗證的座標。
    """
    if entry is None:
        return None
    enriched = _resolve_building(entry)
    place = locate_course_place(entry["location"], entry.get("room_query", ""))
    if place["status"] == "ok":
        target = (place["lat"], place["lon"])
        # GIS 查不到教室代碼時，仍然把解析出的大樓名稱補上，別讓畫面空著
        if not enriched["building_name"] and place["name"]:
            enriched["building_name"] = place["name"]
            enriched["corrected"] = _needs_building_correction(
                entry.get("location", ""), place["name"])
    else:
        target = enriched["building_name"] or enriched["location"]
    enriched["place"] = {k: place.get(k) for k in
                         ("status", "source", "is_verified", "name", "build_id",
                          "lat", "lon", "gemini_keyword", "error_message")}
    enriched["route_link"] = build_route_link(target, origin=origin or None,
                                              travel_mode=travel_mode)
    # 「加到 Google 日曆」：只是一個網址，點開後由使用者在 Google 端確認才會加入，
    # 不需要授權，也不經過我們保存任何資料
    enriched["calendar_url"] = build_google_calendar_url(
        entry["name"], entry["starts_at"], entry["ends_at"],
        location=enriched["building_name"] or entry["location"],
        details=f"課表地點：{entry['location']}")
    enriched["origin"] = origin
    enriched["travel_mode"] = travel_mode
    return enriched


def _parking_for(entry: dict | None, vehicle_type: str, origin: str) -> dict | None:
    """騎車或開車時才需要停車建議；目的地查不到大樓就不猜。

    這裡只列停車場，不算分段時間——那由 plan_departure 負責，
    兩邊都算會讓同一趟行程重複呼叫 Google 兩次。
    """
    if entry is None or not entry.get("building_name"):
        return None
    plan = plan_parking(entry["building_name"], vehicle_type)
    recommended = plan.get("recommended")
    if recommended:
        # 停車場名稱 Google 多半找不到，但對照表裡有座標，直接用座標最準
        known = load_lot_locations().get(recommended["name"], {})
        target = ((known["lat"], known["lon"]) if known.get("lat") is not None
                  else recommended["name"])
        recommended["route_link"] = build_route_link(
            target, origin=origin or None, travel_mode="driving")
    return plan


def _save_schedule(schedule: dict) -> Path:
    path = user_schedule_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _bikes_for(entry: dict | None, origin: str) -> dict | None:
    """騎 YouBike 時，起點要借得到車、終點要還得了車，兩邊都要查。"""
    if entry is None or not origin:
        return None
    borrow = get_bike_status(origin, "bike")
    ret = get_bike_status(entry["building_name"] or entry["location"], "dock")
    return {"borrow": borrow, "return": ret}


@app.get("/api/state")
def state(mode: str = "walking", vehicle: str = "機車", origin: str | None = None,
          now: str | None = None, scenario: str | None = None, lat: float | None = None,
          lon: float | None = None, accuracy: float | None = None) -> JSONResponse:
    # 模擬情境只在這一次請求內有效（見 commute_agent/scenario.py），請求結束就還原。
    # lat／lon／accuracy 是使用者按下「用我的目前位置」後瀏覽器給的定位，
    # 只拿來這一次判斷在不在教室，後端不保存
    with use_scenarios(parse_scenarios(scenario)):
        return _state(mode, vehicle, origin, now, lat, lon, accuracy)


def _state(mode: str, vehicle: str, origin: str | None, now: str | None,
           lat: float | None = None, lon: float | None = None,
           accuracy: float | None = None) -> JSONResponse:
    if mode not in TRAVEL_MODE_LABELS:
        return JSONResponse({"error": f"不支援的交通模式：{mode}"}, status_code=400)

    settings = load_settings()
    moment, simulated, time_error = resolve_now(now, settings.timezone)
    if time_error:
        return JSONResponse({"error": time_error}, status_code=400)

    # 使用者在頁面上填了出發地就用它，沒填才退回 .env 的預設地址
    start = (origin or "").strip() or settings.default_origin

    path = user_schedule_path()
    base = {
        "now": moment.isoformat(timespec="seconds"),
        "simulated": simulated,
        "origin": start,
        "default_origin": settings.default_origin,
        "using_default_origin": start == settings.default_origin,
        "travel_mode": mode,
        "travel_mode_label": TRAVEL_MODE_LABELS[mode],
        "vehicle_type": vehicle,
        "modes": TRAVEL_MODE_LABELS,
        "has_sample": sample_schedule_path().is_file(),
    }

    # 還沒上傳課表是正常起始狀態，不是錯誤：回空課表讓前端顯示上傳引導
    if not path.is_file():
        return JSONResponse({**base, "has_schedule": False, "schedule_source": "",
                             "current_class": None, "next_class": None,
                             "departure": None, "parking": None, "bikes": None,
                             "bus": None, "trip": None, "road_events": None,
                             "courses": []})

    try:
        courses = load_courses(path)
        source = json.loads(path.read_text(encoding="utf-8")).get("source", "")
    except (ValueError, OSError) as exc:
        return JSONResponse({**base, "has_schedule": False, "courses": [],
                             "current_class": None, "next_class": None,
                             "departure": None, "parking": None, "bikes": None,
                             "bus": None, "trip": None, "road_events": None,
                             "schedule_source": "",
                             "error": f"課表檔案讀取失敗：{exc}"}, status_code=200)

    current, upcoming = find_classes(courses, moment)
    next_class = _with_route(upcoming, start, mode)

    trip = None
    road_events = None
    if next_class:
        destination = next_class.get("building_name") or next_class["location"]
        trip = estimate_trip(start, destination, mode) if start else None
        # 起點與目的地都查得到座標才有意義；查不到座標的那端 check_route_events
        # 自己會標成 resolved=False，這裡只是省掉明知道會兩端都落空的呼叫
        destination_place = next_class.get("place")
        road_events = check_route_events(
            start,
            destination,
            travel_mode=mode,
            destination_place=(destination_place
                               if destination_place and destination_place.get("status") == "ok"
                               else None),
        ) if start else None
    # 課間轉場：正在上課或剛下課、下一堂又很快開始時，出發地就是上一堂的教室，
    # 不必使用者再填。用同一個（可能是模擬的）時間，才不會跟畫面上的課對不起來
    transition = find_transition(courses, moment, current, upcoming)

    # 出席檢查：正在上課時，比較「你現在的位置」與「上課的教室」，不在就算遲到或缺席。
    # 「你現在的位置」只認使用者自己輸入的（或即時定位），不用 .env 的預設地址：
    # 那是「出發地」的預設值（通常是住家），拿來當現在位置會把還沒輸入位置的人誤判成缺席
    attendance = (check_attendance(current, moment, place_text=(origin or "").strip(),
                                   lat=lat, lon=lon, accuracy_m=accuracy)
                  if current else None)
    # 出發規劃一次算完路程、緩衝與天氣；騎車模式的分段時間也在裡面，
    # 所以上面的停車查詢不再重算，避免同一趟路重複呼叫 Google。
    # 傳入使用者上傳的那份課表，否則出發時間會依範例課表算，跟畫面顯示的課不同堂；
    # 也要傳入同一個（可能是模擬的）時間，否則模擬「上課前 20 分」時，
    # 該不該出發的判斷卻還是拿真實時間在算
    departure = (plan_departure(start, mode, vehicle, str(path), now=moment)
                 if (next_class and start) else None)

    return JSONResponse({
        **base,
        "has_schedule": True,
        "schedule_source": source,
        "current_class": _with_route(current, start, mode),
        "next_class": next_class,
        "trip": trip,
        "road_events": road_events,
        # 只有騎車開車才需要停車位，步行與大眾運輸不查，省掉七次連線
        "transition": transition,
        "attendance": attendance,
        "departure": departure,
        # 每個模式只查自己用得到的資料：停車位要掃七個校區、公車有速率限制、
        # YouBike 要下載 6 MB，全部都查會讓每次換模式都變慢又浪費額度。
        "parking": _parking_for(next_class, vehicle, start) if mode == "driving" else None,
        "bikes": _bikes_for(next_class, start) if mode == "bicycling" else None,
        "bus": get_bus_eta(start) if (mode == "transit" and start) else None,
        "courses": courses,
        "schedule_changes": changes(path),
        # 雨量站的即時讀數。模擬時間下「真實的現在」沒有意義，所以略過，
        # 除非 Demo 明確開了豪雨情境（那時覆寫值就是要被採用的）
        "rain_now": (get_rain_now()
                     if (not simulated or "heavy_rain" in active_scenarios()) else None),
        "scenarios": {"active": sorted(active_scenarios()), "options": SCENARIOS},
    })


@app.get("/api/recommend")
def recommend(origin: str | None = None, vehicle: str = "機車", now: str | None = None,
              preference: str | None = None, scenario: str | None = None) -> JSONResponse:
    """比較四種交通方式並請 Gemini 給建議。

    頁面載入時就自動打，不必等使用者按按鈕 —— 建議本來就是這頁要回答的問題，
    讓人多按一下只是把答案藏起來。但它要跑四次路程規劃（約十幾秒）又會用掉
    一次 Gemini 額度，所以前端以「課＋出發地＋車種」為鍵去重，
    切換交通方式或背景輪詢時不會重打。
    """
    settings = load_settings()
    start = (origin or "").strip() or settings.default_origin
    if not start:
        return JSONResponse({"error": "沒有出發地"}, status_code=400)

    path = user_schedule_path()
    if not path.is_file():
        return JSONResponse({"error": "還沒有課表，無法給建議"}, status_code=400)

    moment, simulated, time_error = resolve_now(now, settings.timezone)
    if time_error:
        return JSONResponse({"error": time_error}, status_code=400)

    # preference 是使用者在頁面上自己設定的偏好（只存在他的瀏覽器），每次請求帶上來；
    # 沒帶就退回 .env 的 COMMUTE_PREFERENCE
    with use_scenarios(parse_scenarios(scenario)):
        result = recommend_plan(start, vehicle, str(path), preference=preference or "",
                                now=moment if simulated else None)
    return JSONResponse(result,
                        status_code=200 if result["status"] == "ok" else 400)


@app.get("/api/replan")
def replan_route(mode: str = "", origin: str | None = None, vehicle: str = "機車",
                 now: str | None = None, scenario: str | None = None,
                 known_soft: str = "") -> JSONResponse:
    """檢查目前的交通方式還行不行，不行就改，並說明原因。

    無狀態：上一輪的交通方式（mode）與已經接受的風險（known_soft）都由前端帶來，
    所以 Cloud Run 縮到零或同時開好幾個實例都不受影響。每一輪先只檢查目前這一種，
    沒問題就不會去跑四種路線查詢（那是計費的），細節見 skills/replan.py。
    """
    if mode and mode not in TRAVEL_MODE_LABELS:
        return JSONResponse({"error": f"不支援的交通模式：{mode}"}, status_code=400)

    settings = load_settings()
    moment, simulated, time_error = resolve_now(now, settings.timezone)
    if time_error:
        return JSONResponse({"error": time_error}, status_code=400)

    start = (origin or "").strip() or settings.default_origin
    if not start:
        return JSONResponse({"error": "沒有出發地"}, status_code=400)

    path = user_schedule_path()
    if not path.is_file():
        return JSONResponse({"error": "還沒有課表，無法檢查"}, status_code=400)

    with use_scenarios(parse_scenarios(scenario)):
        result = replan(start, vehicle, str(path), previous_mode=mode or None, now=moment,
                        simulated=simulated,
                        known_soft=[c for c in known_soft.split(",") if c])
    return JSONResponse(result, status_code=200 if result["status"] == "ok" else 400)


@app.get("/api/calendar.ics")
def calendar_ics(weeks: int = 18) -> Response:
    """整份課表匯出成 .ics，Google、Apple、Outlook 日曆都能匯入。"""
    path = user_schedule_path()
    if not path.is_file():
        return JSONResponse({"error": "還沒有課表，沒有東西可以匯出"}, status_code=404)

    today = datetime.now(ZoneInfo(load_settings().timezone)).date()
    body = build_ics(load_courses(path), today, weeks=max(1, min(weeks, 30)))
    return Response(content=body.encode("utf-8"), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="ncku-schedule.ics"'})


@app.get("/api/late_notice")
def late_notice(course: str, starts_at: str, late: int = 1, place: str = "",
                reason: str = "", to: str = "", name: str = "",
                kind: str = "late") -> JSONResponse:
    """產生給老師的遲到（kind=late）或請假（kind=leave）通知信草稿。

    只是草稿，不寄信、不保存任何資料。
    """
    if kind not in ("late", "leave"):
        return JSONResponse({"error": f"kind 只能是 late 或 leave，收到 {kind!r}"},
                            status_code=400)
    try:
        notice = (build_leave_notice(course, starts_at, place, reason, to, name)
                  if kind == "leave"
                  else build_late_notice(course, starts_at, late, place, reason, to, name))
    except ValueError:
        return JSONResponse({"error": f"無法解析的時間：{starts_at!r}"}, status_code=400)
    return JSONResponse(notice)


@app.get("/api/here")
def here(code: str, target: str = "") -> JSONResponse:
    """使用者自己輸入或點選確認教室代碼：不經過 Gemini，只回成大 GIS 驗證。

    拍門牌讀錯字時，讓使用者點選相近的教室，或直接輸入看到的代碼。
    """
    result = confirm_room(code, target)
    return JSONResponse(result, status_code=200 if result["status"] != "error" else 400)


@app.post("/api/scan_room")
async def scan_room(file: UploadFile = File(...), target: str = "") -> JSONResponse:
    """收一張門牌照片，認出使用者現在在哪一間，並說明目標教室相對的位置。"""
    raw = await file.read()
    # Gemini 與成大 GIS 的呼叫都是阻塞的，放到執行緒池，別卡住整個伺服器
    result = await run_in_threadpool(scan_room_sign, raw,
                                     (file.content_type or "").lower(), target)
    return JSONResponse(result, status_code=200 if result["status"] != "error" else 400)


@app.get("/api/classroom")
def classroom(q: str, origin: str | None = None, mode: str = "walking") -> JSONResponse:
    """查一間教室在哪棟大樓、哪一層，並附上該層平面圖。

    跟 /api/state 分開是因為它多打兩次成大 GIS；課表頁載入時不必等它，
    畫面先出來、平面圖後補，比整頁慢兩秒好。
    """
    settings = load_settings()
    start = (origin or "").strip() or settings.default_origin
    result = locate_classroom(q, start, mode)
    return JSONResponse(result,
                        status_code=200 if result["status"] != "error" else 400)


@app.get("/api/floorplan")
def floorplan(build_id: str, floor: str) -> JSONResponse:
    """換一層樓的平面圖。

    只組圖層網址，不再查教室，所以在樓層之間切換是即時的；
    圖片本身由瀏覽器直接向成大 GeoServer 要，不經過這台伺服器。
    """
    result = floor_plan_url(build_id, floor)
    return JSONResponse(result,
                        status_code=200 if result["status"] == "ok" else 404)


@app.get("/api/campuswalk")
def campuswalk(lot: str, q: str, room: str = "") -> JSONResponse:
    """停好車之後怎麼走到大樓：校區圖 ＋ 路線 ＋ 指路文字。

    另開一支而不是併進 /api/state：它要打一次 Google Routes 與一次 Gemini，
    整頁等它會慢好幾秒，而使用者不選機車或開車時根本用不到。
    """
    lot_name = (lot or "").strip()
    known = load_lot_locations().get(lot_name)
    if not known:
        return JSONResponse({"status": "error",
                             "error_message": f"對照表裡沒有「{lot_name}」的座標"},
                            status_code=404)

    place = locate_course_place(q, room)
    if place["status"] != "ok":
        return JSONResponse({"status": "error",
                             "error_message": place.get("error_message", "查不到目的地座標")},
                            status_code=404)

    result = plan_campus_walk(lot_name, known["lat"], known["lon"],
                              place["name"] or q, place["lat"], place["lon"])
    return JSONResponse(result,
                        status_code=200 if result["status"] == "ok" else 400)


@app.get("/api/youbike/route")
def youbike_route(from_station: str, to_station: str,
                  origin: str | None = None) -> JSONResponse:
    """使用者在畫面上選好借還車站後，算整趟「走＋騎＋走」的時間。"""
    settings = load_settings()
    start = (origin or "").strip() or settings.default_origin
    if not start:
        return JSONResponse({"error": "沒有出發地"}, status_code=400)

    path = user_schedule_path()
    if not path.is_file():
        return JSONResponse({"error": "還沒有課表，無法得知目的地"}, status_code=400)

    moment, _, _ = resolve_now(None, settings.timezone)
    _, upcoming = find_classes(load_courses(path), moment)
    if upcoming is None:
        return JSONResponse({"error": "課表裡找不到接下來的課"}, status_code=400)

    target = _resolve_building(upcoming)
    plan = plan_bike_journey(start, target["building_name"] or target["location"],
                             from_station, to_station)
    return JSONResponse(plan, status_code=200 if plan["status"] == "ok" else 400)


@app.post("/api/schedule/import")
async def import_schedule(file: UploadFile = File(...)) -> JSONResponse:
    """收課表截圖，交給 Gemini 辨識，存成使用者課表。"""
    raw = await file.read()
    try:
        schedule = extract_schedule_from_image(
            raw, (file.content_type or "").lower(), source=file.filename or "上傳的圖片")
    except OCRError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if not schedule["courses"]:
        return JSONResponse(
            {"error": "這張圖片裡沒有辨識出任何課程，請確認是課表截圖、字有沒有被裁掉。",
             "skipped": schedule["skipped"]}, status_code=400)

    path = _save_schedule(schedule)
    return JSONResponse({"imported": len(schedule["courses"]),
                         "skipped": schedule["skipped"],
                         "source": schedule["source"],
                         "saved_to": str(path)})


@app.post("/api/schedule/sample")
def use_sample_schedule() -> JSONResponse:
    """把版控裡的範例課表複製成使用者課表。

    現場辨識萬一失敗（沒網路、被限流、照片太糊）時的備援，讓 demo 還能繼續。
    """
    sample = sample_schedule_path()
    if not sample.is_file():
        return JSONResponse({"error": f"找不到範例課表：{sample}"}, status_code=404)

    schedule = json.loads(sample.read_text(encoding="utf-8"))
    schedule["source"] = f"範例課表（{sample.name}）"
    schedule.setdefault("skipped", [])
    _save_schedule(schedule)
    return JSONResponse({"imported": len(schedule.get("courses", [])),
                         "source": schedule["source"]})


@app.delete("/api/schedule")
def clear_schedule() -> JSONResponse:
    """清掉使用者課表，回到空白狀態。demo 要重跑一次時用得上。"""
    path = user_schedule_path()
    existed = path.is_file()
    if existed:
        path.unlink()
    return JSONResponse({"cleared": existed})


# ---- 本機 Gemma 讀信 + 手機推播（與上面的課表流程獨立，方便個別 demo） ----

@app.post("/api/mail/apply")
async def mail_apply(payload: dict) -> JSONResponse:
    """貼一封課程信，用本機 Gemma 讀成「哪門課、哪裡變了」的建議。只提議，不改課表。"""
    subject = str(payload.get("subject") or "")
    body = str(payload.get("body") or "")
    if not subject.strip() and not body.strip():
        return JSONResponse({"error": "主旨與內文都是空的"}, status_code=400)
    path = user_schedule_path()
    if not path.is_file():
        return JSONResponse({'error': '請先匯入課表'}, status_code=400)
    out = await asyncio.to_thread(apply_course_mail, subject, body, str(path))
    if out['status'] == 'ok':
        message_id = 'paste:' + hashlib.sha256((subject + '\n' + body).encode()).hexdigest()
        out['change'] = record(path, message_id, subject, out['mail'])
    return JSONResponse(out, status_code=200 if out["status"] in ("ok", "unavailable") else 400)


@app.get('/api/gmail/status')
def gmail_status():
    return gmail_sync.status()


@app.post('/api/gmail/connect')
def gmail_connect():
    try:
        gmail_sync.authorize()
        return {'status': 'authorizing'}
    except (ValueError, ImportError) as exc:
        return JSONResponse({'error': str(exc)}, status_code=400)


@app.post('/api/gmail/sync')
def gmail_refresh():
    if not user_schedule_path().is_file():
        return JSONResponse({'error': '請先匯入課表'}, status_code=400)
    return gmail_sync.sync(user_schedule_path())


@app.get('/api/mail/changes')
def mail_changes():
    return {'changes': changes(user_schedule_path()) if user_schedule_path().is_file() else []}


@app.post('/api/mail/undo')
def mail_undo(payload: dict):
    try:
        return undo(user_schedule_path(), str(payload.get('id', '')))
    except ValueError as exc:
        return JSONResponse({'error': str(exc)}, status_code=404)


@app.post("/api/notify/departure")
def notify(mode: str = "walking", vehicle: str = "機車", origin: str | None = None,
           always: bool = False) -> JSONResponse:
    """把「該幾點出發」推到手機（ntfy）。fixture 模式回 dry_run 不真的送。"""
    if mode not in TRAVEL_MODE_LABELS:
        return JSONResponse({"error": f"不支援的交通模式：{mode}"}, status_code=400)
    path = user_schedule_path()
    out = notify_departure(origin=(origin or "").strip(), travel_mode=mode, vehicle_type=vehicle,
                           schedule_path=str(path) if path.is_file() else "", always=always)
    return JSONResponse(out, status_code=200 if out["status"] != "error" else 502)


@app.get("/")
def index() -> FileResponse:
    # 開發中頁面常改，被瀏覽器快取會讓人以為修正沒生效
    return FileResponse(WEB_DIR / "index.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    # 圖示不太會變，可以放心讓瀏覽器快取久一點
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
