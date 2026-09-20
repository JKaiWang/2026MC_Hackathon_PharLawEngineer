"""Tool 層：把教授／助教的信讀成結構化的「課程異動」。本機 Gemma 專用，不上雲端。

信件是個資裡最敏感的一類（寄件人、課名、成績、個人請假理由都在裡面），
所以這支**只用本機模型**：Ollama 沒起來就回 unavailable，不會自動改送 Gemini。
要改送雲端得由呼叫端明確傳 allow_cloud=True，而且目前沒有任何呼叫端這麼做。

模型只做「讀信 → 填欄位」。它填的新地點會再交給 locate_place 用 GIS 驗證，
它填的重要度只影響出發緩衝的長短，不會直接改課表——改課表要使用者確認。
"""

from __future__ import annotations

import re
from datetime import date

from api import Settings, load_settings
from commute_agent.tools.local_llm import LocalLLMError, chat_json, is_available

KINDS = ("room_change", "time_change", "cancelled", "online", "exam",
         "presentation", "info", "irrelevant")
IMPORTANCE = ("normal", "high", "critical")
MAX_BODY_CHARS = 4000

SYSTEM = """你在替成功大學的學生讀教授或助教寄來的課程信件，把它整理成固定欄位的 JSON。
只根據信件內容填寫，信裡沒寫的欄位一律填 null，不要推測。

kind 只能是下列之一：
- room_change：上課地點改了（含「改到」「移至」「換教室」）
- time_change：上課時間改了或補課
- cancelled：停課、不上課
- online：改為線上／視訊上課
- exam：期中考、期末考、小考、quiz 相關通知
- presentation：報告、發表、上台、口試相關通知
- info：跟這門課有關但不影響何時去哪裡上課（作業、講義、公告）
- irrelevant：不是課程信（廣告、社團、系辦公告等）

importance（遲到的代價）：exam、presentation 為 critical；room_change、time_change 為 high；其餘 normal。

輸出格式：
{"kind": ..., "course": "課名或null", "new_location": "新地點原文或null", "new_time": "新時間原文或null",
 "effective_date": "生效日期原文或null", "importance": ..., "summary": "一句話說明學生該知道什麼",
 "confidence": 0到1}
"""


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text[:MAX_BODY_CHARS]


def parse_moodle_time_change(subject: str, body: str) -> dict | None:
    """Extract an explicit dated Moodle start-time notice without guessing its end."""
    text = subject + "\n" + body
    course = re.search(r"\d{4}_(.+?)\((\d{4}_[A-Za-z0-9]+)\)", text)
    year = re.search(r"發表於\s*(\d{4})年", body)
    changes = set(re.findall(
        r"(?:下[周週][一二三四五六日天])?[（(](\d{1,2})/(\d{1,2})[）)]"
        r"上課時間改為\s*(早上|上午|下午|晚上)?\s*(\d{1,2})[:：](\d{2})", text))
    if not course or not year or len(changes) != 1:
        return None
    month, day, period, hour, minute = next(iter(changes))
    hour, minute = int(hour), int(minute)
    if period and not 1 <= hour <= 12:
        return None
    if period in ("下午", "晚上") and hour < 12:
        hour += 12
    if period in ("早上", "上午") and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    try:
        effective = date(int(year[1]), int(month), int(day)).isoformat()
    except ValueError:
        return None
    return {"status": "ok", "kind": "time_change", "course": course[1],
            "course_code": course[2], "new_location": None,
            "new_time": f"{hour:02}:{minute:02}", "effective_date": effective,
            "importance": "high", "summary": f"{effective} 開始時間改為 {hour:02}:{minute:02}；結束時間未提供",
            "confidence": 1.0, "source": "moodle_explicit_text", "scope": "single_occurrence"}


def classify_course_mail(subject: str, body: str, settings: Settings | None = None,
                         allow_cloud: bool = False) -> dict:
    """讀一封課程信，判斷它是教室變更、停課、考試通知還是無關信件。

    適用時機：使用者貼一封教授或助教的信，想知道「所以我下週去哪、幾點、
    有多重要」時使用。信件是個資，這支只用本機模型。

    Args:
        subject: 信件主旨。
        body: 信件內文（超過 4000 字會截斷）。
        allow_cloud: 保留參數。目前一律 False，本機模型不可用時直接回 unavailable。

    Returns:
        dict，包含：
        - status: "ok"、"unavailable"（本機模型沒起來）或 "error"
        - kind: 見 KINDS；模型回了清單外的值會被改成 "info"
        - course、new_location、new_time、effective_date: 信裡的原文，沒有就 None
        - importance: "normal" / "high" / "critical"
        - summary、confidence: 模型的一句話摘要與信心
        - source: "gemma_local"
        - error_message: 僅在非 ok 時出現
    """
    settings = settings or load_settings()
    base = {"status": "error", "kind": None, "course": None, "new_location": None,
            "new_time": None, "effective_date": None, "importance": "normal",
            "summary": "", "confidence": 0.0, "source": "gemma_local",
            "model": settings.local_llm_model}
    subject, body = _clip(subject), _clip(body)
    if not subject and not body:
        return {**base, "error_message": "主旨與內文都是空的"}
    explicit = parse_moodle_time_change(subject, body)
    if explicit:
        return explicit
    if not is_available(settings):
        return {**base, "status": "unavailable",
                "error_message": f"本機 Ollama 未啟動或尚未拉取 {settings.local_llm_model}；"
                                 "信件不會送雲端"}

    try:
        answer = chat_json(SYSTEM, f"主旨：{subject or '（無）'}\n\n內文：\n{body or '（無）'}", settings)
    except LocalLLMError as exc:
        return {**base, "error_message": str(exc)}

    kind = answer.get("kind")
    if kind not in KINDS:
        kind = "info"
    importance = answer.get("importance")
    if importance not in IMPORTANCE:
        importance = {"exam": "critical", "presentation": "critical",
                      "room_change": "high", "time_change": "high"}.get(kind, "normal")
    try:
        confidence = float(answer.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    def _text(key: str) -> str | None:
        value = answer.get(key)
        return str(value).strip() if value not in (None, "", "null") else None

    return {**base, "status": "ok", "kind": kind, "importance": importance,
            "course": _text("course"), "new_location": _text("new_location"),
            "new_time": _text("new_time"), "effective_date": _text("effective_date"),
            "summary": str(answer.get("summary") or ""), "confidence": confidence}
