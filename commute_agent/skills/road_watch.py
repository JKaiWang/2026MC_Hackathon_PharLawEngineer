"""Skill 層：出發前檢查起點與目的地附近是否有車禍、施工或封閉。

只查得到成大 GIS 認得的校內地點（跟 trip_plan.estimate_trip 同樣的限制）；
起點若是校外地址（例如住家），GIS 查不到座標，這裡會明確標記該端
resolved=False，而不是拿附近的地標充數，也不會假裝查過了。

城市固定查台南（成大所在城市）；校外地址若不在台南市內，這個假設會不準，
但目前 TDX RoadEvent 端點就是以城市為單位查詢，PoC 階段先這樣處理。
"""

from __future__ import annotations

from commute_agent.tools.google_routes import compute_route
from commute_agent.tools.ncku_geo import resolve_place
from commute_agent.tools.road_events import (
    DEFAULT_RADIUS_M,
    get_road_events,
    get_road_events_along_route,
)

CITY = "Tainan"


def _route_point(place: dict | None, query: str):
    if place and place.get("status") == "ok":
        return place["lat"], place["lon"]
    return query


def _side(query: str, radius_m: float, resolved_place: dict | None = None) -> dict:
    # 課表流程若已經取得 GIS 座標，直接沿用它；重新用建築物名稱搜尋
    # 可能觸發模糊比對，把相似的大樓（例如 A006/A105）混在一起。
    place = resolved_place if resolved_place is not None else resolve_place(query)
    if place["status"] != "ok":
        return {
            "query": query,
            "resolved": False,
            "name": None,
            "events": [],
            "count": 0,
            "note": f"「{query}」不在成大地理資訊系統裡（可能是校外地址），無法查詢周邊路況。",
        }

    result = get_road_events(CITY, place["lat"], place["lon"], radius_m)
    if result["status"] != "ok":
        return {
            "query": query,
            "resolved": True,
            "name": place["name"],
            "events": [],
            "count": 0,
            "note": f"路況查詢失敗：{result.get('error_message', '未知錯誤')}",
        }

    return {
        "query": query,
        "resolved": True,
        "name": place["name"],
        "events": result["events"],
        "count": result["count"],
        "note": "",
    }


def check_route_events(origin: str, destination: str, radius_m: float = DEFAULT_RADIUS_M,
                       *, origin_place: dict | None = None,
                       destination_place: dict | None = None,
                       travel_mode: str = "walking") -> dict:
    """檢查起點與目的地周邊 radius_m 公尺內，現在是否有車禍、施工或封閉等路況事件。

    適用時機：使用者要出發前，想知道路上會不會不順時使用；也適合在
    estimate_trip 給出路線之後主動補問一次，讓使用者知道這條路現在順不順。

    Args:
        origin: 起點名稱，例如 "圖書館" 或住家地址。校外地址查不到座標時，
            起點這端會標記 resolved=False，只回報目的地周邊的路況。
        destination: 目的地名稱，例如 "B501 資訊工程系館"。
        radius_m: 判定「周邊」的半徑（公尺），預設 500。

    Returns:
        dict，包含：
        - status: "ok"（至少查成功一端）或 "error"（兩端都查不到座標）
        - radius_m: 使用的半徑
        - origin、destination: 各自的 dict，含 resolved（是否查到座標）、
          name、events（清單，每筆含 description、road_name、distance_m 等，
          見 road_events.get_road_events 的回傳說明）、count、note
        - has_events: 兩端加總是否有任何事件，方便快速判斷要不要提醒使用者
        - note: 整體說明，例如兩端都查不到座標時會說明原因
    """
    route = compute_route(
        _route_point(origin_place, origin),
        _route_point(destination_place, destination),
        travel_mode,
    )
    route_points = route.get("route_points", []) if route.get("status") == "ok" else []
    if len(route_points) >= 2:
        events = get_road_events_along_route(CITY, route_points, radius_m)
        route_side = {
            "query": f"{origin} → {destination}",
            "resolved": events["status"] == "ok",
            "name": "Google Maps 路線",
            "events": events.get("events", []),
            "count": events.get("count", 0),
            "note": (events.get("error_message", "")
                     if events["status"] != "ok" else ""),
        }
        empty_side = lambda query: {
            "query": query, "resolved": True, "name": query,
            "events": [], "count": 0,
            "note": "已納入 Google Maps 路線沿途查詢。",
        }
        return {
            "status": "ok" if route_side["resolved"] else "error",
            "radius_m": radius_m,
            "origin": empty_side(origin),
            "destination": empty_side(destination),
            "route": route_side,
            "has_events": route_side["count"] > 0,
            "note": route_side["note"],
        }

    origin_side = _side(origin, radius_m, origin_place)
    destination_side = _side(destination, radius_m, destination_place)

    if not origin_side["resolved"] and not destination_side["resolved"]:
        return {
            "status": "error",
            "radius_m": radius_m,
            "origin": origin_side,
            "destination": destination_side,
            "route": {"resolved": False, "name": "Google Maps 路線",
                      "events": [], "count": 0,
                      "note": route.get("error_message", "無法取得 Google Maps 路線")},
            "has_events": False,
            "note": "起點與目的地都查不到座標，無法查詢周邊路況。",
        }

    has_events = origin_side["count"] > 0 or destination_side["count"] > 0
    return {
        "status": "ok",
        "radius_m": radius_m,
        "origin": origin_side,
        "destination": destination_side,
        "route": {"resolved": False, "name": "Google Maps 路線",
                  "events": [], "count": 0,
                  "note": route.get("error_message", "無法取得 Google Maps 路線")},
        "has_events": has_events,
        "note": "",
    }
