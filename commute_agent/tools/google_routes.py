"""Tool 層：Google Maps Routes API，取得真實路線的距離與時間。

與 ncku_geo 的估算不同，這支拿得到含實際路網與大眾運輸班次的時間，
代價是需要 GOOGLE_MAPS_API_KEY 且會計費，因此不是預設來源——
由 travel_time.py 依設定決定要不要用它。

Routes API 的起訖點可以直接給地址字串，不必自己先做地理編碼，
所以它也是目前唯一能算「校外住家 → 教室」的方式。
"""

from __future__ import annotations

import requests

from api import load_settings

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
USER_AGENT = "NCKU-Smart-Commute/0.1 (DevJam TW 2026 hackathon prototype)"

# 本專案的交通模式 → Routes API 的 travelMode
ROUTES_TRAVEL_MODE = {
    "walking": "WALK",
    "bicycling": "BICYCLE",
    "driving": "DRIVE",
    "transit": "TRANSIT",
}

# 只要這幾個欄位，FieldMask 越小計費層級越低
FIELD_MASK = "routes.duration,routes.distanceMeters"
# 要把路線畫在地圖上時才加 polyline：它會把計費拉到較高的層級
FIELD_MASK_WITH_PATH = FIELD_MASK + ",routes.polyline.encodedPolyline"


class RoutesError(RuntimeError):
    """Routes API 回應無法使用。"""


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """解開 Google 的 encoded polyline，回傳 (lat, lon) 清單。純函式。

    格式是 Google 自己的變長編碼：每個數字先乘 1e5 取整、與前一個值取差、
    左移一位（負數再取補數），然後每 5 bits 一組、除了最後一組都加 0x20，
    最後每組加 63 變成可列印字元。這裡就是把這串步驟反過來做。
    """
    points: list[tuple[float, float]] = []
    index = lat = lon = 0

    while index < len(encoded):
        for axis in ("lat", "lon"):
            shift = result = 0
            while index < len(encoded):
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == "lat":
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))

    return points


def parse_route(payload: dict) -> dict:
    """把 Routes API 回應轉成統一格式。

    duration 是 "1234s" 這種字串，要自己去掉尾巴的 s 再轉成數字。
    """
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not routes:
        raise RoutesError("回應沒有 routes，可能是起訖點無法連通")

    route = routes[0]
    duration = route.get("duration")
    if not isinstance(duration, str) or not duration.endswith("s"):
        raise RoutesError(f"無法解析 duration：{duration!r}")

    seconds = float(duration[:-1])
    encoded = (route.get("polyline") or {}).get("encodedPolyline")
    points = decode_polyline(encoded) if encoded else []
    return {
        "minutes": max(1, round(seconds / 60)),
        "distance_m": route.get("distanceMeters"),
        "seconds": round(seconds),
        "points": points,
        # road_watch 在既有介面使用這個欄位；保留相同資料以維持相容。
        "route_points": points,
    }


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode Google's encoded polyline into ``(latitude, longitude)`` pairs."""
    if not isinstance(encoded, str):
        raise RoutesError("Google 路線 polyline 格式異常")

    points = []
    index = lat = lon = 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise RoutesError("Google 路線 polyline 不完整")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        points.append((lat / 1e5, lon / 1e5))
    return points


def waypoint(place: str | tuple[float, float]) -> dict:
    """把起訖點轉成 Routes API 的 waypoint。

    給座標時用 latLng，比地名可靠得多：校內大樓的正式名稱（例如
    "B406 三系館鋼構區"）帶著編號前綴，Google 的地理編碼常常對不到，
    或對到隔壁棟。校外地址沒有座標，才退回字串讓 Google 自己解析。
    """
    if isinstance(place, (tuple, list)):
        lat, lon = place
        return {"location": {"latLng": {"latitude": float(lat), "longitude": float(lon)}}}
    return {"address": str(place)}


def compute_route(origin: str | tuple[float, float],
                  destination: str | tuple[float, float],
                  travel_mode: str = "walking",
                  with_path: bool = False) -> dict:
    """用 Google Routes API 算實際路線的距離與時間。

    Args:
        origin: 起點，(lat, lon) 座標或地址字串。座標較可靠。
        destination: 目的地，同上。
        travel_mode: "walking"、"bicycling"、"driving" 或 "transit"。
        with_path: 是否連路線的折線座標一起要。要畫在地圖上才需要，
            會把計費拉到較高的層級，所以預設不要。

    Returns:
        dict，含 status、minutes、distance_m 與 points（with_path 時才有值，
        為 (lat, lon) 清單）；status 為 "error" 時含 error_message。
    """
    settings = load_settings()
    result = {"status": "ok", "minutes": None, "distance_m": None,
              "points": [], "provider": "google_routes"}

    if travel_mode not in ROUTES_TRAVEL_MODE:
        return {**result, "status": "error",
                "error_message": f"不支援的交通模式 {travel_mode!r}"}
    if not settings.google_maps_api_key:
        return {**result, "status": "error",
                "error_message": "未設定 GOOGLE_MAPS_API_KEY"}

    body = {
        "origin": waypoint(origin),
        "destination": waypoint(destination),
        "travelMode": ROUTES_TRAVEL_MODE[travel_mode],
        "units": "METRIC",
        "languageCode": "zh-TW",
    }
    # 只有開車能指定 routingPreference，其他模式帶了會被退回 400
    if travel_mode == "driving":
        body["routingPreference"] = "TRAFFIC_AWARE"

    try:
        resp = requests.post(
            ENDPOINT, json=body, timeout=settings.http_timeout_seconds,
            headers={"X-Goog-Api-Key": settings.google_maps_api_key,
                     "X-Goog-FieldMask": (FIELD_MASK_WITH_PATH if with_path
                                          else FIELD_MASK),
                     "User-Agent": USER_AGENT})
    except requests.Timeout:
        return {**result, "status": "error", "error_message": "Google Routes API 逾時"}
    except requests.RequestException as exc:
        return {**result, "status": "error",
                "error_message": f"無法連線 Google Routes API（{type(exc).__name__}）"}

    if resp.status_code != 200:
        # 金鑰錯誤與額度用盡都會走到這裡，訊息保留但不回傳金鑰本身
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        return {**result, "status": "error",
                "error_message": f"Google Routes API 回應 HTTP {resp.status_code}"
                                 + (f"：{detail}" if detail else "")}

    try:
        return {**result, **parse_route(resp.json())}
    except (RoutesError, ValueError) as exc:
        return {**result, "status": "error", "error_message": str(exc)}
