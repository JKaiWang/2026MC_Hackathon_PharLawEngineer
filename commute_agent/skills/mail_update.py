"""Skill 層：一封課程信 → 「哪門課、哪裡變了、要不要多留時間」的具體建議。

流程：course_mail（本機 Gemma 讀信）→ 對到課表裡的那門課 → 新地點交給
locate_place 用 GIS 驗證座標 → 依 kind 算出對出發時間的影響。
這支只**提議**變更（回傳 patch），不直接改課表——改課表是使用者按確認才做的事，
跟寄信、改行事曆同一個原則。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api import load_settings
from commute_agent.skills.locate_place import locate_course_place
from commute_agent.tools.class_schedule import extract_room_query, load_courses
from commute_agent.tools.course_mail import classify_course_mail

# 遲到代價高的信，出發緩衝加這麼多分鐘；跟 departure_plan 的 HIGH_STAKES_EXTRA_MINUTES 對齊
EXTRA_BUFFER = {"critical": 10, "high": 5, "normal": 0}


def _norm(text: str) -> str:
    return "".join((text or "").split()).casefold()


def match_course(courses: list[dict], hint: str | None) -> dict | None:
    """用信裡的課名對課表。純函式。完全相同 → 互為子字串 → 都不是就 None。"""
    h = _norm(hint or "")
    if not h:
        return None
    for c in courses:
        if _norm(c.get("name")) == h:
            return c
    partial = [c for c in courses if h in _norm(c.get("name")) or _norm(c.get("name")) in h]
    return partial[0] if len(partial) == 1 else None


def _schedule_path(raw: str) -> Path:
    settings = load_settings()
    path = Path(raw or settings.class_schedule_path)
    return path if path.is_absolute() else Path(__file__).resolve().parent.parent.parent / path


def apply_course_mail(subject: str, body: str, schedule_path: str = "") -> dict:
    """讀一封課程信，對到課表，提出「地點／時間／緩衝」該怎麼改的建議。

    適用時機：使用者貼教授或助教的信，問「所以下週我要去哪」「這封信要注意什麼」。
    信件只在本機讀（Gemma），不上雲端；本機模型沒起來會直說。

    Args:
        subject: 信件主旨。
        body: 信件內文。
        schedule_path: 要對照的課表；留空用設定裡的預設課表。

    Returns:
        dict，包含：
        - status: "ok"、"unavailable"（本機模型沒起來）或 "error"
        - mail: course_mail 的原始判讀（kind、importance、summary…）
        - course: 對到的課表項目；對不到為 None，並在 note 說明
        - proposed_patch: 建議的變更，例如 {"location": "新地點", "extra_buffer_minutes": 10}
          ；沒有需要改的就是空 dict
        - new_place: 新地點經 GIS 驗證後的座標與大樓（locate_place 的回傳），沒有新地點為 None
        - needs_confirmation: 永遠 True——這支只提議，不改課表
        - note: 給使用者看的一句話
    """
    mail = classify_course_mail(subject, body)
    result = {"status": mail["status"], "mail": mail, "course": None,
              "proposed_patch": {}, "new_place": None, "needs_confirmation": True, "note": ""}
    if mail["status"] != "ok":
        result["note"] = mail.get("error_message", "")
        return result

    if mail["kind"] == "irrelevant":
        result["note"] = "這封信跟課程無關，不需要動課表。"
        return result

    try:
        courses = load_courses(_schedule_path(schedule_path))
    except (OSError, ValueError) as exc:
        return {**result, "status": "error", "note": f"課表讀取失敗：{exc}"}

    course = match_course(courses, mail["course"])
    result["course"] = course
    patch: dict = {}

    if mail["kind"] in ("room_change",) and mail["new_location"]:
        place = locate_course_place(mail["new_location"],
                                    extract_room_query(mail["new_location"]))
        result["new_place"] = place
        patch["location"] = mail["new_location"]
        if place["status"] == "ok":
            patch["building_name"] = place["name"]
    if mail["kind"] == "time_change" and mail["new_time"]:
        patch["time_note"] = mail["new_time"]
        if mail.get("source") == "moodle_explicit_text":
            patch.update(start_time=mail["new_time"], effective_date=mail["effective_date"],
                         scope="single_occurrence")
    if mail["kind"] in ("cancelled", "online"):
        patch["skip_commute"] = True
    # 不用出門的課談緩衝沒有意義
    extra = 0 if patch.get("skip_commute") else EXTRA_BUFFER.get(mail["importance"], 0)
    if extra:
        patch["extra_buffer_minutes"] = extra
    result["proposed_patch"] = patch

    when = datetime.now(ZoneInfo(load_settings().timezone)).strftime("%m/%d %H:%M")
    who = course["name"] if course else (mail["course"] or "（信裡沒寫課名）")
    if course is None and mail["kind"] == "info":
        result["note"] = f"{mail['summary']}（不影響上課地點與時間）"
    elif course is None:
        result["note"] = f"{mail['summary']}（課表裡對不到「{who}」，請確認是哪一門課）"
    elif mail["kind"] in ("cancelled", "online"):
        result["note"] = f"{who}：{mail['summary']} 這堂不用出門。"
    elif mail["kind"] == "room_change":
        verified = result["new_place"] and result["new_place"]["status"] == "ok"
        where = result["new_place"]["name"] if verified else mail["new_location"]
        result["note"] = (f"{who}：改到 {where}。" +
                          ("座標已由成大 GIS 驗證。" if verified else "新地點查不到座標，導航可能不準。"))
    else:
        result["note"] = f"{who}：{mail['summary']}" + (f" 建議出發緩衝多留 {extra} 分鐘。" if extra else "")
    result["read_at"] = when
    return result
