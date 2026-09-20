"""Skill 層：把課表上的地點寫法，變成地圖上真的找得到的那個點。

課表原文不是地址，也不是 GIS 上的正式名稱。「社科院大樓階梯教室－心理」、
「資訊大樓格致廳小講堂」這種寫法直接丟進成大 GIS 全部查不到（2026-09-19 實測
課表上八個地點沒有一個查得到），丟進 Google Maps 則會對到名字相近的別棟，
導航就這樣偏掉幾百公尺。

解法分三層，愈前面愈可信，能在前面解決就不往後走：

1. 教室代碼 → GIS 的 roominfo 查到 building_id → 用代碼取中心點座標。
   這條最準，而且不花模型額度，課表上有代碼的大多走這裡。
2. 原文直接問 GIS。偶爾會中，順手一試。
3. 請本機 Gemma（Ollama）對著整份大樓清單判斷是哪一棟，再用 build_id 向 GIS 要座標。
   課表地點是個資，能在機器上讀完就不送雲端；Ollama 沒起來時自動略過。
4. 請 Gemini 把原文讀成「可以拿去搜尋的關鍵字」，再把關鍵字丟回 GIS 驗證。

第三、四層是這支的重點，設計上刻意讓模型只做它擅長的事：讀懂「社科院大樓階梯教室
－心理」指的是社會科學院。座標一律來自 GIS，不讓模型自己講經緯度——
它講得出來，但那是編的，而編出來的座標看起來跟真的一模一樣，最危險。
模型給的關鍵字查不到就當作失敗往下走，不會把沒驗證過的答案送出去。

最後才退回 Google Geocoding，並標明來源，讓介面可以說「這個位置可能不準」。
"""

from __future__ import annotations

import json

from api import load_settings
from commute_agent.tools.building_match import guess_building
from commute_agent.tools.gemini_error import describe
from commute_agent.tools.geocode import geocode_place
from commute_agent.tools.ncku_geo import get_building_centroid, resolve_place
from commute_agent.tools.ncku_room import lookup_room

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "building_keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "可以拿去搜尋成大大樓的關鍵字，最多三個，由最有把握的排起"},
        "room_keywords": {
            "type": "array", "items": {"type": "string"},
            "description": "可以拿去搜尋成大教室的關鍵字或代碼，沒有就給空陣列"},
        "note": {"type": "string", "description": "一句話說明你怎麼判讀這串文字"},
    },
    "required": ["building_keywords"],
}

INSTRUCTION = """你在處理成功大學的課表地點欄位。這些欄位是人手寫的簡稱，
夾雜教室代碼、教室性質與系所名稱，不能直接拿去搜尋。

請把它拆成可以查詢成大地理資訊系統的關鍵字：

- building_keywords：這個地點所在的「大樓」名稱關鍵字，由最有把握的排起，最多三個。
  只給大樓的名字，不要包含教室代碼、樓層、「階梯教室」「視聽教室」這類房間性質，
  也不要加「成大」「國立成功大學」前綴。
  簡稱要還原成完整名稱，例如「社科院」是「社會科學院」、「管院」是「管理學院」。
  不確定時就多給一個備選，不要硬猜一個。
- room_keywords：如果文字裡有特定教室的名字或代碼（例如「格致廳」、「4264」），
  放進來，讓系統可以直接查那間教室。沒有就給空陣列。

你只需要輸出關鍵字。不要輸出經緯度或地址——座標由系統自己去查，
你給的座標無法驗證，寫出來只會變成錯誤資訊。
"""

# 同一份課表會反覆問同一個地點（每次切換交通方式都重算一輪），
# 同一個行程裡答案不會變，快取住就不必重複打 GIS 與模型
_CACHE: dict[str, dict] = {}


def _agreed_candidate(candidates: list[dict]) -> dict | None:
    """候選教室全部指向同一棟時才採用，指向好幾棟就當作問不出來。

    「格致廳」模糊比到大、小講堂兩間，但兩間都在 B204，那是同一個答案；
    「階梯教室」卻在十幾棟大樓裡都有，隨便挑第一筆會把人導到別的學院去
    （實測會挑到 A702 工業與資訊管理系館）。寧可回 None 讓上層換一條路查。
    """
    if not candidates:
        return None
    exact = [c for c in candidates if c["exact_match"]]
    pool = exact or candidates
    buildings = {c["building_id"] for c in pool if c.get("building_id")}
    return pool[0] if len(buildings) == 1 else None


def _from_room_code(room_query: str) -> dict | None:
    """教室代碼 → GIS 的大樓與中心點。最準的一條路。"""
    if not (room_query or "").strip():
        return None
    found = lookup_room(room_query)
    if found["status"] != "ok":
        return None
    chosen = _agreed_candidate(found["candidates"])
    if not chosen or not chosen.get("building_id"):
        return None
    centroid = get_building_centroid(chosen["building_id"])
    if centroid["status"] != "ok" or centroid["lat"] is None:
        return None
    return {"status": "ok", "source": "gis_room_code",
            "name": chosen["building_name"], "build_id": chosen["building_id"],
            "floor": chosen["floor"], "lat": centroid["lat"], "lon": centroid["lon"],
            "matched": room_query, "is_verified": True}


def _from_gis_text(text: str) -> dict | None:
    """原文直接問 GIS。中的機率不高，但不花錢也不花時間。"""
    found = resolve_place(text)
    if found["status"] != "ok" or found.get("lat") is None:
        return None
    return {"status": "ok", "source": "gis_text", "name": found["name"],
            "build_id": found["build_id"], "floor": "",
            "lat": found["lat"], "lon": found["lon"],
            "matched": found.get("matched_query", text), "is_verified": True}


# 前綴短於這個長度就不查：兩三個字的片段會比到不相干的大樓
MIN_PREFIX_LENGTH = 4


def _from_gis_prefix(text: str) -> dict | None:
    """把原文從右邊逐字剪短再問 GIS。

    課表的寫法幾乎都是「大樓名」接「教室描述」，所以大樓名就在最前面：
    「資訊大樓格致廳小講堂」剪到「資訊大樓」就查得到 B003 資訊大樓。
    這段不花額度、結果固定，所以排在問模型之前。

    只在教室代碼那一條路走不通時才會走到這裡，這件事很重要：
    成大有兩棟資工大樓，「資訊系館」這個簡稱在 GIS 會比到 B502，
    但 4264 其實在 B501。有代碼就該以代碼為準，名稱只是最後的線索。
    """
    cleaned = (text or "").strip()
    for end in range(len(cleaned), MIN_PREFIX_LENGTH - 1, -1):
        prefix = cleaned[:end].strip()
        if len(prefix) < MIN_PREFIX_LENGTH:
            break
        found = _from_gis_text(prefix)
        if found:
            return {**found, "source": "gis_prefix", "matched": prefix}
    return None


def ask_for_keywords(text: str) -> dict:
    """請 Gemini 把課表寫法讀成可搜尋的關鍵字。失敗時回空清單，不丟例外。"""
    settings = load_settings()
    if not settings.gemini_api_key:
        return {"building_keywords": [], "room_keywords": [],
                "note": "尚未設定 Gemini 金鑰"}

    from google import genai
    from google.genai import types

    # client 要留在變數裡，否則暫時物件會在請求送出前被回收
    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[INSTRUCTION, f"課表上的地點欄位：{text}"],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA),
        )
        answer = json.loads(response.text or "{}")
    except Exception as exc:  # 額度用盡、逾時、SDK 自訂例外都走這裡
        return {"building_keywords": [], "room_keywords": [], "note": describe(exc)}

    return {"building_keywords": list(answer.get("building_keywords") or [])[:3],
            "room_keywords": list(answer.get("room_keywords") or [])[:3],
            "note": answer.get("note", "")}


def _from_local_llm(text: str) -> dict | None:
    """本機 Gemma 挑大樓，GIS 給座標。Ollama 不在、模型說不是校內、或名字對不上清單都算失敗。"""
    guess = guess_building(text)
    if guess["status"] != "ok" or not guess.get("build_id"):
        return None
    centroid = get_building_centroid(guess["build_id"])
    if centroid["status"] != "ok" or centroid["lat"] is None:
        return None
    return {"status": "ok", "source": "gemma_building", "name": guess["name"],
            "build_id": guess["build_id"], "floor": "",
            "lat": centroid["lat"], "lon": centroid["lon"],
            "matched": guess["name"], "is_verified": True,
            "hint": {"model": guess["model"], "confidence": guess["confidence"],
                     "reason": guess["reason"], "note": "本機 Gemma 判讀，座標由 GIS 提供"}}


def _from_gemini(text: str) -> dict | None:
    """模型給關鍵字，GIS 給座標。關鍵字查不到就算失敗，不硬送。"""
    hint = ask_for_keywords(text)
    for keyword in hint["room_keywords"]:
        found = _from_room_code(keyword)
        if found:
            return {**found, "source": "gemini_room", "hint": hint,
                    "gemini_keyword": keyword}
    for keyword in hint["building_keywords"]:
        found = _from_gis_text(keyword)
        if found:
            return {**found, "source": "gemini_building", "hint": hint,
                    "gemini_keyword": keyword}
    return None


def _from_google(text: str) -> dict | None:
    """最後的退路。Google 不知道成大的大樓編號，位置可能偏，要標明來源。"""
    found = geocode_place(text)
    if found["status"] != "ok":
        return None
    return {"status": "ok", "source": "google", "name": found.get("name") or text,
            "build_id": "", "floor": "", "lat": found["lat"], "lon": found["lon"],
            "matched": text, "is_verified": False}


def locate_course_place(location_text: str, room_query: str = "",
                        use_gemini: bool = True, use_local_llm: bool = True) -> dict:
    """把課表上的地點寫法，解析成地圖上確切的一個點。

    適用時機：課表寫的地點（例如「社科院大樓階梯教室－心理」）查不到、
    或導航連結對到錯的大樓時使用。

    Args:
        location_text: 課表上的地點原文。
        room_query: 從原文抓出的教室代碼，有的話會優先用它查，最準也最省。
        use_gemini: 前面幾層都查不到時，要不要請雲端 Gemini 讀這串文字。
        use_local_llm: 要不要先請本機 Gemma（Ollama）判斷；沒起 server 會自動略過。

    Returns:
        dict，包含：
        - status: "ok" 或 "not_found"
        - source: 答案是怎麼來的 —— "gis_room_code"（教室代碼查 GIS，最準）、
          "gis_text"（原文查 GIS）、"gis_prefix"（剪掉教室描述只留大樓名）、
          "gemma_building"（本機 Gemma 對清單挑大樓，座標由 GIS 提供）、
          "gemini_room" / "gemini_building"
          （Gemini 讀出關鍵字再由 GIS 驗證）或 "google"（Google Geocoding，可能偏）
        - is_verified: 座標是否來自成大 GIS。False 代表只有 Google 的結果，
          介面應提醒使用者位置可能不準
        - name、build_id、floor、lat、lon: 解析出的大樓與座標
        - hint: 走模型那條時的判讀說明（Gemma：信心與理由；Gemini：關鍵字）
    """
    text = (location_text or "").strip()
    if not text and not (room_query or "").strip():
        return {"status": "not_found", "source": None, "is_verified": False,
                "error_message": "沒有地點文字，也沒有教室代碼"}

    key = f"{text}|{room_query}|{use_gemini}|{use_local_llm}"
    if key in _CACHE:
        return _CACHE[key]

    found = (_from_room_code(room_query)
             or _from_gis_text(text)
             or (_from_local_llm(text) if use_local_llm else None)
             or _from_gis_prefix(text)
             or (_from_gemini(text) if use_gemini else None)
             or _from_google(text))

    result = found or {
        "status": "not_found", "source": None, "is_verified": False,
        "name": "", "build_id": "", "floor": "", "lat": None, "lon": None,
        "error_message": f"查不到「{text}」在成大的哪個位置",
    }
    result = {**result, "query": text, "room_query": room_query}
    _CACHE[key] = result
    return result
