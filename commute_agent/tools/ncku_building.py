"""Tool 層：查詢成大任一棟建築物（成大地理資訊系統 nckumap buildinfo）。

跟 ncku_room.py 的差別：lookup_room 只能查「已知教室代碼／名稱」，
這支讓 Agent 能查全校任何建築物名稱或關鍵字，不侷限於特定教室，
例如使用者只知道系館名稱、還不知道要上課的確切教室時可以先用這支。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import requests

from api import load_settings

BUILDINFO_PATH = "/buildinfo.htm"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "ncku_gis" / "buildinfo"
MAX_QUERY_LENGTH = 50
USER_AGENT = "NCKU-Smart-Commute/0.1 (DevJam TW 2026 hackathon prototype)"


class SchemaError(ValueError):
    """nckumap 回應格式與預期不符（可能是對方改版）。"""


def _normalize(text: str) -> str:
    return (text or "").strip().casefold()


def parse_building_response(payload: dict, query: str) -> list[dict]:
    """把 nckumap buildinfo 的原始回應轉成統一格式的候選清單。

    注意：刻意不回傳 campusId 或猜測出的中文校區名稱。實測 buildinfo 只給
    "campusId":"B" 這類內部代碼，尚未證實它與停車系統 A-G 校區代碼是同一套
    編碼（例如：資訊工程系館的 campusId 是 "B"，但停車系統的 B 是「成功」
    校區，兩者是否對應未經查證），與其硬猜錯誤答案，不如不回傳，避免
    Agent 誤用。
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SchemaError("回應缺少 data 陣列")

    q = _normalize(query)
    candidates = []
    for row in rows:
        name = row.get("name") or ""
        keyword = row.get("keyword") or ""
        # 正式名稱通常帶有建物代碼前綴（例如「B501 資訊工程系館」），使用者
        # 查詢時很少連代碼一起打，因此用「查詢字是否包含在正式名稱中」而非
        # 完全相等來判斷；keyword 欄位本身就是簡短關鍵字，用完全相等判斷即可。
        candidates.append({
            "building_id": row.get("id") or "",
            "building_name": name,
            "keyword": keyword,
            "desc": row.get("desc") or "",
            "exact_match": q == _normalize(keyword) or (bool(q) and q in _normalize(name)),
        })
    return candidates


def _now_iso(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).isoformat(timespec="seconds")


def _result(status: str, query: str, mode: str, source: str, tz: str,
            candidates: list | None = None, error_message: str | None = None) -> dict:
    candidates = candidates or []
    result = {
        "status": status,
        "query": query,
        "candidates": candidates,
        "exact_match_count": sum(1 for c in candidates if c["exact_match"]),
        "mode": mode,
        "source": source,
        "fetched_at": _now_iso(tz),
    }
    if error_message:
        result["error_message"] = error_message
    return result


def lookup_building(query: str) -> dict:
    """查詢成功大學任一棟建築物的正式名稱與代碼，涵蓋全校，不限於特定教室。

    適用時機：使用者只知道大樓、系館或地標名稱，還沒指定確切教室時使用；
    也適用於確認某個名稱在成大是否真的存在某棟建築物。
    找到建築物名稱後，可再搭配 build_walking_link 組出導航連結。

    Args:
        query: 建築物名稱或關鍵字，例如 "資訊工程系館"、"格致堂"、"總圖書館"。

    Returns:
        dict，包含：
        - status: "ok"（找到）、"not_found"（查無結果）或 "error"（查詢失敗）
        - candidates: 候選建築物清單，每筆含 building_id、building_name、
          keyword、desc、exact_match（是否與查詢完全相符）
        - exact_match_count: 完全相符的筆數；為 0 時代表只有模糊結果，需再確認
        - source、fetched_at、mode: 資料來源、查詢時間、live 或 fixture 模式
        - error_message: 僅在 status 為 "error" 時出現
    """
    settings = load_settings()
    mode, tz = settings.provider_mode, settings.timezone
    endpoint = settings.ncku_gis_base_url.rstrip("/") + BUILDINFO_PATH
    q = (query or "").strip()
    params = {"action": "search", "locale": "zh-tw", "q": q,
              "exactlyMatch": "false", "start": "0", "page": "1", "limit": "20"}
    source = f"{endpoint}?{urlencode(params)}"

    if not q or len(q) > MAX_QUERY_LENGTH:
        return _result("error", q, mode, source, tz,
                       error_message=f"查詢字串必須為 1 到 {MAX_QUERY_LENGTH} 個字元")

    if mode == "fixture":
        # 檔名一律用網址編碼（而非原始中文字），避免不同作業系統／解壓縮
        # 工具對非 ASCII 檔名編碼判斷不一致，導致「檔案明明在，卻讀不到」。
        path = FIXTURE_DIR / f"{quote(q, safe='')}.json"
        if not path.is_file():
            return _result("error", q, mode, source, tz,
                           error_message=f"fixture 模式下沒有 {q!r} 的錄製資料")
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        try:
            resp = requests.get(endpoint, params=params, timeout=settings.http_timeout_seconds,
                                headers={"User-Agent": USER_AGENT})
        except requests.Timeout:
            return _result("error", q, mode, source, tz, error_message="成大地理資訊系統查詢逾時")
        except requests.RequestException as exc:
            return _result("error", q, mode, source, tz,
                           error_message=f"無法連線成大地理資訊系統（{type(exc).__name__}）")
        if resp.status_code != 200:
            return _result("error", q, mode, source, tz,
                           error_message=f"成大地理資訊系統回應 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            return _result("error", q, mode, source, tz, error_message="成大地理資訊系統回應不是 JSON")

    try:
        candidates = parse_building_response(payload, q)
    except SchemaError as exc:
        return _result("error", q, mode, source, tz, error_message=f"回應格式異常：{exc}")

    status = "ok" if candidates else "not_found"
    return _result(status, q, mode, source, tz, candidates=candidates)
