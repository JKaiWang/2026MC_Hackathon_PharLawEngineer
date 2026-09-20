"""Skill 層：回答「這間教室在哪、在幾樓、長什麼樣子」。

把三件事串成一張教室導覽卡：
- 位置：成大 GIS 的大樓名稱與中心點座標，加上一條導航連結。
- 平面圖：成大 GeoServer 的 WMS 圖層，還會把目標教室在圖上框出來，
  讓使用者到了大樓還找得到門；那棟沒有圖層時才退回 picture/ 的人工截圖。
- 結論：一句話講清楚在幾樓。

樓層有兩個來源，這支刻意兩個都算再對照：成大 GIS 直接回 floor，是官方資料；
教室代碼的規則（大樓代號後那一碼）是推算，GIS 查不到時才頂上。
兩邊不一致時不挑一個藏起來 —— 一律以 GIS 為準，但把差異講出來，
因為那通常代表代碼規則在這棟大樓不適用，使用者值得知道。
"""

from __future__ import annotations

from commute_agent.tools.floor_plan import describe_floor, get_floor_plan
from commute_agent.tools.ncku_campusmap import (campus_map_url, get_building_footprint,
                                                is_on_main_campus)
from commute_agent.tools.ncku_floorplan import (floor_plan_url, get_rooms_on_floor,
                                                locate_room_on_plan)
from commute_agent.skills.locate_place import locate_course_place
from commute_agent.tools.ncku_geo import get_building_centroid
from commute_agent.tools.ncku_room import lookup_room
from commute_agent.tools.route_link import build_route_link


def _live_plan(build_id: str, floor: str, room_code: str) -> dict | None:
    """成大 GeoServer 的即時平面圖，順便算出目標教室在圖上的位置。

    成大地圖網站自己就是這樣畫平面圖的（WMS 圖層 gis_room:<大樓>_<樓層>），
    全校一千多層都在，比人工截圖涵蓋得廣，也不會因為改建而過期。
    """
    if not build_id or not floor:
        return None
    image = floor_plan_url(build_id, floor)
    if image["status"] != "ok":
        return None

    highlight = None
    if room_code:
        rooms = get_rooms_on_floor(build_id, floor)
        for room in rooms.get("rooms", []):
            if room["room_code"] == room_code:
                highlight = locate_room_on_plan(room["bounds"], image["bbox"])
                break

    return {"status": "ok", "source": "geoserver", "url": image["url"],
            "caption": f"{build_id} {floor} 平面圖（成大地理資訊系統）",
            "floor": floor, "floors": image["floors"], "layer": image["layer"],
            "width": image["width"], "height": image["height"],
            "highlight": highlight,
            "available": True}


def _campus_plan(build_id: str, floor: str = "", room_code: str = "") -> dict | None:
    """校園全圖：把目標大樓在整個主校區的位置框出來。

    跟 _live_plan 框教室是同一套作法，只是範圍換成主校區、圖層換成全校建物
    外框。不在主校區索引裡的大樓（力行、建國等其他校區）沒有這張圖。
    """
    if not build_id or not is_on_main_campus(build_id):
        return None
    image = campus_map_url()
    if image["status"] != "ok":
        return None

    footprint = get_building_footprint(build_id)
    highlight = locate_room_on_plan(footprint, image["bbox"]) if footprint else None
    highlight_label = room_code or build_id
    if room_code and floor:
        rooms = get_rooms_on_floor(build_id, floor)
        for room in rooms.get("rooms", []):
            if room.get("room_code") == room_code and room.get("bounds"):
                highlight = locate_room_on_plan(room["bounds"], image["bbox"])
                break

    return {"status": "ok", "source": "geoserver", "url": image["url"],
            "caption": f"{build_id} 在主校區的位置（成大地理資訊系統）",
            "highlight": highlight, "highlight_label": highlight_label,
            "available": True}


def _static_plan(plan: dict) -> dict:
    """picture/ 裡的人工截圖。GeoServer 沒有那一層時的備援。"""
    image = plan.get("plan")
    if plan["status"] != "ok" or not image:
        return {"status": "not_found", "source": "picture", "url": None,
                "caption": "", "floors": [], "highlight": None,
                "available": False,
                "error_message": plan.get("error_message", "沒有這一層的平面圖")}
    return {"status": "ok", "source": "picture", "url": image["url"],
            "caption": image["caption"], "floor": plan.get("plan_floor", ""),
            "floors": [], "highlight": None, "available": image["available"]}


def _pick(candidates: list[dict]) -> dict | None:
    """完全相符的優先，沒有就退回第一筆模糊結果。"""
    exact = [c for c in candidates if c["exact_match"]]
    return exact[0] if exact else (candidates[0] if candidates else None)


def _conclusion(room: str, building: str, floor: str, floor_source: str) -> str:
    """一句話的結論。沒有樓層就照實說不知道，不要含糊帶過。"""
    where = building or "查不到大樓"
    if not floor:
        return f"{room} 在{where}，但查不到樓層資訊。"
    said = describe_floor(floor)
    if floor_source == "gis":
        return f"{room} 在{where} {said}。"
    if floor_source == "floor_plan":
        return f"{room} 在{where} {said}（依平面圖標示，成大 GIS 查不到這個代碼）。"
    return f"{room} 依教室代碼推算在{where} {said}（成大 GIS 查不到，這是推算值）。"


def locate_classroom(room_query: str, origin: str = "",
                     travel_mode: str = "walking") -> dict:
    """查一間教室在哪棟大樓、哪一層，並附上該樓層的平面圖與導航連結。

    適用時機：使用者問「這間教室在哪」、「在幾樓」、「教室怎麼走」，
    或要在畫面上標出教室平面圖時使用。

    Args:
        room_query: 教室代碼或名稱，例如 "4264"、"27103"、"格致廳小講堂"。
        origin: 導航起點。留空則產生不帶起點的連結。
        travel_mode: "walking"、"bicycling"、"driving" 或 "transit"。

    Returns:
        dict，包含：
        - status: "ok"、"not_found"（GIS 查無此教室）或 "error"
        - building_name、building_id: 實際所在大樓（以 GIS 為準）
        - building_source: 大樓是怎麼查出來的 —— "gis_room"（教室代碼，最準），
          或 locate_course_place 的來源（"gis_prefix"、"gemini_building" 等）
        - floor、floor_source: 樓層，以及它的來源 —— "gis"（官方資料）、
          "floor_plan"（平面圖上的標示）或 "room_code"（由代碼推算）
        - floor_rule: 代碼推算規則的說明文字
        - floor_conflict: GIS 與代碼推算不一致時的說明，一致或無從比較時為 None
        - location: 大樓中心點座標（lat、lon）與 GIS 上的正式名稱
        - floor_plan: 該樓層的平面圖。source 為 "geoserver" 時是成大即時圖層，
          highlight 是目標教室在圖上的位置（百分比），floors 是這棟還有哪幾層；
          source 為 "picture" 時是人工截圖的備援
        - campus_map: 主校區全圖，highlight 是目標大樓在圖上的位置（百分比）；
          大樓不在主校區索引裡時退回沒有 highlight 的靜態截圖
        - route_link: 前往該大樓的導航連結
        - conclusion: 一句話結論，可直接顯示給使用者
    """
    query = (room_query or "").strip()
    if not query:
        return {"status": "error", "error_message": "沒有給教室代碼或名稱"}

    found = lookup_room(query)
    if found["status"] == "error":
        return {"status": "error", "room_query": query,
                "error_message": found.get("error_message", "教室查詢失敗")}

    chosen = _pick(found.get("candidates", []))
    building = chosen["building_name"] if chosen else ""
    room_code = (chosen["room_code"] if chosen else "") or query
    room_name = chosen["room_name"] if chosen else ""
    gis_floor = (chosen["floor"] if chosen else "") or ""

    plan = get_floor_plan(room_code, room_name or query, gis_floor)
    guessed = plan["floor_by_room_code"]

    floor = gis_floor or (guessed or "")
    floor_source = "gis" if gis_floor else ("room_code" if guessed else None)

    # 教室代碼查不到就放棄，是這張卡最大的問題：「社科院大樓 階梯教室－心理」
    # 本來就不是代碼，roominfo 永遠查不到，畫面上就只剩一句「查不到大樓」。
    # 改成交給 locate_course_place：它會依序用 GIS 原文、剪短的前綴、
    # Gemini 讀出的關鍵字去換出大樓，座標一律由 GIS 給，模型只負責讀懂那串字。
    place = None
    if not building:
        place = locate_course_place(query, "")
        if place["status"] == "ok":
            building = place["name"]
            if not floor and place.get("floor"):
                floor, floor_source = place["floor"], "gis"

    # 平面圖對照表認得的話，至少還能补上大樓與樓層
    if plan["status"] == "ok":
        building = building or plan.get("plan_building", "")
        if not floor:
            floor, floor_source = plan["plan_floor"], "floor_plan"

    conflict = None
    if gis_floor and guessed and gis_floor.upper() != guessed.upper():
        # 代碼規則在這棟不適用。以 GIS 為準，但講出來——這是規則要補的例外
        conflict = (f"教室代碼推算是 {describe_floor(guessed)}，"
                    f"但成大 GIS 記的是 {describe_floor(gis_floor)}，以 GIS 為準。")

    # 有 build_id 就直接用代碼取座標：GIS 的名稱搜尋碰到「A006 唯農大樓」
    # 這種帶編號前綴的名字會對到別棟
    build_id = (chosen["building_id"] if chosen
                else (place or {}).get("build_id", "") or "")
    building_source = "gis_room" if chosen else (place or {}).get("source")

    if place and place["status"] == "ok" and place.get("lat") is not None:
        # locate_course_place 已經拿過座標了，不必再打一次 GIS
        located = {"status": "ok", "name": place["name"],
                   "lat": place["lat"], "lon": place["lon"]}
    else:
        located = get_building_centroid(build_id) if build_id else {"status": "skipped"}

    target = ((located["lat"], located["lon"])
              if located.get("status") == "ok" and located.get("lat") is not None
              else (building or query))

    # 即時圖層只在大樓是由教室代碼查出來時才用：那條路才確定
    # 「這間教室屬於這棟的這一層」。用文字換出來的大樓配上別處來的樓層，
    # 有可能拼出一張根本不含那間教室的平面圖，寧可用截圖
    image = (_live_plan(build_id, floor, room_code) if chosen else None) or _static_plan(plan)
    campus_map = _campus_plan(build_id, floor, room_code) or plan["campus_map"]

    return {
        "status": "ok" if chosen else "not_found",
        "room_query": query,
        "room_code": room_code,
        "room_name": room_name,
        "building_name": building,
        "building_id": build_id,
        "building_source": building_source,
        "exact_match_count": found["exact_match_count"],
        "floor": floor or None,
        "floor_source": floor_source,
        "floor_rule": plan["floor_rule"],
        "floor_conflict": conflict,
        "location": {"lat": located.get("lat"), "lon": located.get("lon"),
                     "name": located.get("name", ""),
                     "status": located.get("status")},
        "floor_plan": image,
        "floor_plan_lookup": plan,
        "campus_map": campus_map,
        "route_link": build_route_link(target, origin=origin or None,
                                       travel_mode=travel_mode),
        "conclusion": _conclusion(room_code or query, building, floor, floor_source),
        "source": found["source"],
    }
