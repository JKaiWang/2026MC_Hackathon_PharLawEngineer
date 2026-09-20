"""Tool 層：把 TDX 路況通報的髒地點文字整理成「行政區／路名／路段」。本機 Gemma。

TDX 台南路況的 Location.Other 與 Description 長這樣（2026-09-19 真實錄製）：
「null北外環 長和路一段222巷100弄口至北外環路口」、「永康區中山路(奧迪前-文華路)」、
「臺南市北區公園路和公園南路的交叉路口」。要拿去跟路線比對、或講給使用者聽，
得先抽出路名。規則式抽不乾淨（開頭的 "null" 是來源資料的 bug、路段寫法五花八門），
交給本地小模型剛好。這是公開資料，用本機模型的理由是離線與省額度，不是隱私。

模型只做文字整理，不判斷嚴重程度、不猜座標。
"""

from __future__ import annotations

from api import Settings, load_settings
from commute_agent.tools.local_llm import LocalLLMError, chat_json, is_available

SYSTEM = """你在整理台南市的路況通報地點文字。輸入可能含來源資料的雜訊（例如開頭多了 "null"）、
行政區、路名、路段、交叉路口。請只做整理，不要推測沒寫的資訊。

輸出 JSON：
{"district": "行政區（含「區」字）或null", "roads": ["提到的路名，含路／街／大道等後綴，去掉段號與巷弄，最多三條"],
 "section": "路段描述原文（例如「五妃街至大同路一段」）或null", "is_intersection": true或false,
 "cleaned": "去掉雜訊後、可以直接唸給人聽的一句話"}
"""


def normalize_road_text(text: str, settings: Settings | None = None) -> dict:
    """把一段路況地點文字整理成行政區、路名清單與路段。

    適用時機：拿 TDX 路況事件的 road_name／description 去跟使用者路線上的路名比對、
    或要用一句乾淨的話告訴使用者「哪條路出事」時使用。

    Returns:
        dict，包含：
        - status: "ok"、"unavailable"（本機模型沒起來）或 "error"
        - district: 行政區或 None
        - roads: 路名清單（可能是空清單）
        - section: 路段原文或 None
        - is_intersection: 是否為交叉路口
        - cleaned: 整理後可直接朗讀的一句話；失敗時等於原文
        - source: "gemma_local"
        - error_message: 僅在非 ok 時出現
    """
    settings = settings or load_settings()
    raw = (text or "").strip()
    base = {"status": "error", "query": raw, "district": None, "roads": [], "section": None,
            "is_intersection": False, "cleaned": raw, "source": "gemma_local",
            "model": settings.local_llm_model}
    if not raw:
        return {**base, "error_message": "地點文字不可為空"}
    if not is_available(settings):
        return {**base, "status": "unavailable",
                "error_message": f"本機 Ollama 未啟動或尚未拉取 {settings.local_llm_model}"}
    try:
        answer = chat_json(SYSTEM, f"地點文字：{raw}", settings, num_ctx=2048)
    except LocalLLMError as exc:
        return {**base, "error_message": str(exc)}

    roads = answer.get("roads")
    roads = [str(r).strip() for r in roads if str(r).strip()] if isinstance(roads, list) else []
    district = answer.get("district")
    section = answer.get("section")
    return {**base, "status": "ok",
            "district": str(district).strip() if district not in (None, "", "null") else None,
            "roads": roads[:3],
            "section": str(section).strip() if section not in (None, "", "null") else None,
            "is_intersection": bool(answer.get("is_intersection")),
            "cleaned": str(answer.get("cleaned") or raw).strip() or raw}
