"""Tool 層：時間來源的統一接口。

上層（trip_plan、網頁、agent）只呼叫 get_travel_time，不必知道時間是哪裡來的。
目前有兩個來源：

- estimate：用成大 GIS 座標算直線距離再換算，免費、免金鑰，但只涵蓋校內地點，
  而且是估算值。
- google_routes：Google Routes API，真實路網與大眾運輸班次，起訖點可以是任意
  地址（校外住家也算得出來），但需要金鑰且會計費。

由 TRAVEL_TIME_PROVIDER 決定策略：

- auto（預設）：免費的先試，答不出來才花一次 Google。校內兩點之間走路騎車
  不花錢，只有校外起點與大眾運輸這種免費算不出來的情況才付費。
- estimate：完全不碰付費 API，算不出來就算不出來。
- google：一律走 Google。

任一來源失敗都會換用另一個，並在 fallback_reason 說明原因——寧可給一個
標明來源的數字，也不要整個功能掛掉。is_estimate 讓介面知道該不該寫「約」。
"""

from __future__ import annotations

from api import load_settings
from commute_agent.tools.google_routes import compute_route
from commute_agent.tools.ncku_geo import (
    MODES_WITHOUT_ESTIMATE,
    estimate_minutes,
    haversine_meters,
    resolve_place,
)

PROVIDERS = ("auto", "estimate", "google")


def _resolve_waypoint(place: str | tuple[float, float]) -> dict:
    """Normalize a place name or an already-resolved GIS coordinate."""
    if isinstance(place, (tuple, list)) and len(place) == 2:
        return {"status": "ok", "name": "", "lat": float(place[0]),
                "lon": float(place[1])}
    return resolve_place(place)


def _by_estimate(origin: str | tuple[float, float],
                 destination: str | tuple[float, float], travel_mode: str) -> dict:
    """用 GIS 座標估算。只有兩端都在成大校內才算得出來。"""
    result = {"status": "ok", "provider": "estimate", "is_estimate": True,
              "minutes": None, "distance_m": None, "unresolved": None,
              "origin_name": "", "destination_name": ""}

    start, end = _resolve_waypoint(origin), _resolve_waypoint(destination)
    if start["status"] != "ok":
        return {**result, "status": "unavailable", "unresolved": "origin",
                "reason": f"起點「{origin}」不在成大地理資訊系統裡，無法估算"}
    if end["status"] != "ok":
        return {**result, "status": "unavailable", "unresolved": "destination",
                "reason": f"目的地「{destination}」查不到座標，無法估算"}

    distance = haversine_meters(start["lat"], start["lon"], end["lat"], end["lon"])
    minutes = estimate_minutes(distance, travel_mode)
    result.update(origin_name=start["name"], destination_name=end["name"],
                  distance_m=round(distance), minutes=minutes)
    if minutes is None:
        result.update(status="unavailable",
                      reason="大眾運輸受班次影響過大，估算會誤導，故不提供")
    return result


def to_waypoint(place: str | tuple[float, float]) -> str | tuple[float, float]:
    """校內地點換成座標再送給 Google，校外地址原樣送出。

    Google 不認得「圖書館」這種校內簡稱，補上校名後又容易對到隔壁棟
    （「B406 三系館」曾被解析成材料系館）。GIS 查座標是免費的，能查到就用座標，
    對 Google 來說也最精確。查不到多半是校外地址，交給 Google 自己解析。
    """
    if isinstance(place, (tuple, list)):
        return place
    found = resolve_place(place)
    return (found["lat"], found["lon"]) if found["status"] == "ok" else place


def _by_google(origin: str | tuple[float, float],
               destination: str | tuple[float, float], travel_mode: str) -> dict:
    route = compute_route(to_waypoint(origin), to_waypoint(destination), travel_mode)
    if route["status"] != "ok":
        return {"status": "unavailable", "provider": "google_routes",
                "is_estimate": False, "minutes": None, "distance_m": None,
                "reason": route.get("error_message", "Google Routes API 查詢失敗")}
    name = lambda p: p if isinstance(p, str) else ""
    return {"status": "ok", "provider": "google_routes", "is_estimate": False,
            "minutes": route["minutes"], "distance_m": route["distance_m"],
            "unresolved": None,
            "origin_name": name(origin), "destination_name": name(destination)}


def get_travel_time(origin: str | tuple[float, float],
                    destination: str | tuple[float, float],
                    travel_mode: str = "walking",
                    provider: str | None = None) -> dict:
    """取得從起點到目的地的時間，來源由設定決定。

    Args:
        origin: 起點名稱或地址。
        destination: 目的地名稱或地址。
        travel_mode: "walking"、"bicycling"、"driving" 或 "transit"。
        provider: 強制指定 "auto"、"estimate" 或 "google"；
            留空則用 TRAVEL_TIME_PROVIDER（預設 "auto"）。

    Returns:
        dict，包含：
        - status: "ok" 或 "unavailable"（算不出來，reason 說明原因）
        - minutes、distance_m: 時間與距離
        - provider: 實際使用的來源（"estimate" 或 "google_routes"）
        - is_estimate: True 代表是估算值，介面要標明「約」
        - fallback_reason: 換用另一個來源時，說明為什麼換
    """
    chosen = provider or load_settings().travel_time_provider
    if chosen not in PROVIDERS:
        chosen = "auto"

    if chosen == "estimate":
        return _by_estimate(origin, destination, travel_mode)

    if chosen == "auto":
        # 免費的先試。校內兩點之間、又不是大眾運輸時它就答得出來，不必花錢。
        free = _by_estimate(origin, destination, travel_mode)
        if free["status"] == "ok":
            return free
        # 免費的答不出來（起點在校外、或是大眾運輸）才值得花一次 Google。
        routed = _by_google(origin, destination, travel_mode)
        if routed["status"] == "ok":
            routed["fallback_reason"] = free.get("reason")
            return routed
        free["fallback_reason"] = routed["reason"]
        return free

    routed = _by_google(origin, destination, travel_mode)
    if routed["status"] == "ok":
        return routed

    # Google 失敗就退回估算，並保留失敗原因讓使用者知道為什麼是估算值
    fallback = _by_estimate(origin, destination, travel_mode)
    fallback["fallback_reason"] = routed["reason"]
    return fallback


def mode_supports_estimate(travel_mode: str) -> bool:
    """這個模式在 estimate 來源下算不算得出時間。"""
    return travel_mode not in MODES_WITHOUT_ESTIMATE
