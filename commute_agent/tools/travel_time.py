"""Tool 層：用 Google Maps Distance Matrix API 估算真實通勤時間。

核心原則：沒有 GOOGLE_MAPS_API_KEY 時，絕不用假設的速度（例如「機車時速
25 公里」）去推算一個看起來像真的、實際上編造的時間數字——這違反本專案
「資料必須真實、可查證」的原則。沒有金鑰時一律誠實回報 status="unavailable"，
金鑰到位後不需修改任何程式碼即可立即取得真實時間。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from api import load_settings

DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

# Google Distance Matrix 沒有機車／機動兩輪車專用模式（TWO_WHEELER 模式
# 目前僅限印度使用），機車只能借用 driving 模式近似，因此需要加註 caveat。
VEHICLE_TO_GOOGLE_MODE = {"機車": "driving", "汽車": "driving", "步行": "walking"}
MOPED_CAVEAT = "機車沒有專屬的路況模式，此時間是借用汽車路況估算的近似值，實際時間可能因車道差異而不同。"


def _now_iso(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).isoformat(timespec="seconds")


def estimate_travel_time(origin: str, destination: str, vehicle_type: str) -> dict:
    """用 Google Maps 即時路況估算從起點到目的地的真實交通時間。

    適用時機：使用者想知道從某地到成大某棟大樓大約要花多久時間時使用。
    本工具需要 GOOGLE_MAPS_API_KEY 才能運作；沒有設定金鑰時，status 會是
    "unavailable"，此時不可自行假設或估算時間，應如實告知使用者此功能
    目前無法使用，改用其他已知的真實資訊（例如停車位剩餘數）協助決策。

    Args:
        origin: 起點名稱或地址，例如 "敬業一舍"。
        destination: 目的地名稱，建議使用 lookup_room 或 lookup_building
            查到的 building_name，而非課表上寫的原始大樓名稱。
        vehicle_type: "機車"、"汽車" 或 "步行"。機車沒有專屬路況模式，
            回傳結果會附上 caveat 說明是借用汽車路況的近似值。

    Returns:
        dict，包含：
        - status: "ok"（成功）、"not_found"（Google 找不到路線）、
          "unavailable"（缺少金鑰，非本次查詢的錯誤）或 "error"（查詢失敗）
        - duration_text、duration_seconds、distance_text、distance_meters：
          僅在 status 為 "ok" 時出現
        - caveat：僅在 vehicle_type 為 "機車" 且成功時出現
        - reason：僅在 status 為 "unavailable" 時出現，說明原因
        - error_message：僅在 status 為 "error" 時出現
    """
    origin, destination = (origin or "").strip(), (destination or "").strip()
    if not origin:
        raise ValueError("origin 不可為空")
    if not destination:
        raise ValueError("destination 不可為空")
    if vehicle_type not in VEHICLE_TO_GOOGLE_MODE:
        raise ValueError(f"不支援的車種 {vehicle_type!r}，可用選項：{list(VEHICLE_TO_GOOGLE_MODE)}")

    settings = load_settings()
    tz = settings.timezone

    if not settings.google_maps_api_key:
        return {
            "status": "unavailable",
            "reason": "尚未設定 GOOGLE_MAPS_API_KEY，無法提供真實時間估算，"
                      "請勿自行假設速度推算時間",
            "fetched_at": _now_iso(tz),
        }

    google_mode = VEHICLE_TO_GOOGLE_MODE[vehicle_type]
    params = {"origins": origin, "destinations": destination,
              "mode": google_mode, "language": "zh-TW", "key": settings.google_maps_api_key}

    try:
        resp = requests.get(DISTANCE_MATRIX_URL, params=params, timeout=settings.http_timeout_seconds)
    except requests.Timeout:
        return {"status": "error", "error_message": "Google Maps 查詢逾時", "fetched_at": _now_iso(tz)}
    except requests.RequestException as exc:
        return {"status": "error", "error_message": f"無法連線 Google Maps（{type(exc).__name__}）",
                "fetched_at": _now_iso(tz)}

    if resp.status_code != 200:
        return {"status": "error", "error_message": f"Google Maps 回應 HTTP {resp.status_code}",
                "fetched_at": _now_iso(tz)}
    try:
        payload = resp.json()
    except ValueError:
        return {"status": "error", "error_message": "Google Maps 回應不是 JSON", "fetched_at": _now_iso(tz)}

    top_status = payload.get("status")
    if top_status != "OK":
        detail = payload.get("error_message", "")
        return {"status": "error", "error_message": f"Google Maps 回應狀態 {top_status}（{detail}）",
                "fetched_at": _now_iso(tz)}

    try:
        element = payload["rows"][0]["elements"][0]
    except (KeyError, IndexError, TypeError):
        return {"status": "error", "error_message": "Google Maps 回應格式異常", "fetched_at": _now_iso(tz)}

    if element.get("status") != "OK":
        return {"status": "not_found", "origin": origin, "destination": destination,
                "fetched_at": _now_iso(tz)}

    result = {
        "status": "ok",
        "origin": origin,
        "destination": destination,
        "vehicle_type": vehicle_type,
        "google_travel_mode": google_mode,
        "duration_text": element["duration"]["text"],
        "duration_seconds": element["duration"]["value"],
        "distance_text": element["distance"]["text"],
        "distance_meters": element["distance"]["value"],
        "source": DISTANCE_MATRIX_URL,
        "fetched_at": _now_iso(tz),
    }
    if vehicle_type == "機車":
        result["caveat"] = MOPED_CAVEAT
    return result
