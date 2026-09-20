"""Skill 層：算好該幾點出發，直接推到手機。

plan_departure 負責算，這支負責「把結論變成一則通知」：
- plenty：不吵人（回 skipped），除非 always=True
- leave_now：高優先，附路程與天氣提醒
- too_late：urgent，直說已經來不及準時，附導航連結讓人立刻走

同一堂課同一個結論不重推：呼叫端用回傳的 dedupe_key 判斷（網頁輪詢每分鐘會問一次）。
"""

from __future__ import annotations

from datetime import datetime

from commute_agent.skills.departure_plan import plan_departure
from commute_agent.tools.push_notify import send_push
from commute_agent.tools.route_link import TRAVEL_MODE_LABELS, build_route_link

_VERDICT_STYLE = {
    "leave_now": ("該出發了", "high", ["alarm_clock"]),
    "too_late": ("已經來不及準時", "urgent", ["warning"]),
    "plenty": ("出發提醒", "default", ["bell"]),
}


def _hhmm(iso: str | None) -> str:
    if not iso:
        return "--:--"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return iso


def compose_departure_message(plan: dict) -> tuple[str, str, str, list[str]]:
    """把 plan_departure 的結果寫成 (標題, 內文, 優先度, tags)。純函式。"""
    course = plan.get("course") or {}
    title, priority, tags = _VERDICT_STYLE.get(plan.get("verdict"), _VERDICT_STYLE["plenty"])
    mode = TRAVEL_MODE_LABELS.get(plan.get("travel_mode", ""), plan.get("travel_mode", ""))
    where = course.get("building") or course.get("location") or "教室"
    lines = [f"{course.get('name', '下一堂課')} {course.get('start_time', '')} @ {where}"]
    remaining = plan.get("minutes_until_departure")
    if plan.get("verdict") == "too_late":
        lines.append(f"建議出發時刻 {_hhmm(plan.get('leave_at'))} 已過 {abs(remaining or 0)} 分鐘，現在走仍會晚到。")
    elif remaining is not None:
        lines.append(f"{_hhmm(plan.get('leave_at'))} 出發（{'現在' if remaining <= 0 else f'還有 {remaining} 分鐘'}），"
                     f"{mode}約 {plan.get('travel_minutes', '?')} 分{'（估算）' if plan.get('is_estimate') else ''}。")
    if plan.get("weather_advice"):
        lines.append(plan["weather_advice"])
    return title, "\n".join(lines), priority, tags


def notify_departure(origin: str = "", travel_mode: str = "walking", vehicle_type: str = "機車",
                     schedule_path: str = "", always: bool = False) -> dict:
    """算出該幾點出發，並在需要時推播到手機。

    適用時機：使用者說「快到時間提醒我」、「該走的時候通知我手機」；
    或網頁每分鐘輪詢時呼叫，由回傳的 dedupe_key 避免重複推。

    Args:
        origin、travel_mode、vehicle_type、schedule_path: 同 plan_departure。
        always: True 時就算時間還很充裕也推一則（例如使用者要求「現在就告訴我幾點走」）。

    Returns:
        dict，包含：
        - status: "sent"（已推）、"dry_run"（fixture 模式）、"skipped"（時間充裕不吵人）、
          "no_class"、"error"
        - verdict、leave_at、minutes_until_departure: 來自 plan_departure
        - title、message、priority: 推播內容（skipped 時仍會給，方便介面顯示）
        - dedupe_key: 「課名|上課時間|verdict」，同一把 key 不要重推
        - push: send_push 的原始回傳；沒推時為 None
    """
    plan = plan_departure(origin=origin, travel_mode=travel_mode,
                          vehicle_type=vehicle_type, schedule_path=schedule_path)
    if plan.get("status") != "ok":
        return {"status": plan.get("status", "error"), "verdict": None, "plan": plan, "push": None,
                "error_message": plan.get("error_message", "課表裡沒有接下來的課")}

    plan = {**plan, "travel_mode": travel_mode}
    title, message, priority, tags = compose_departure_message(plan)
    course = plan.get("course") or {}
    key = f"{course.get('name')}|{course.get('start_time')}|{plan['verdict']}"
    result = {"status": "skipped", "verdict": plan["verdict"], "leave_at": plan.get("leave_at"),
              "minutes_until_departure": plan.get("minutes_until_departure"),
              "title": title, "message": message, "priority": priority,
              "dedupe_key": key, "plan": plan, "push": None}
    if plan["verdict"] == "plenty" and not always:
        return result

    destination = course.get("building") or course.get("location") or ""
    link = build_route_link(destination, origin=origin or None, travel_mode=travel_mode) if destination else None
    push = send_push(title, message, priority=priority, tags=tags, click_url=link)
    status = {"ok": "sent", "dry_run": "dry_run"}.get(push["status"], "error")
    return {**result, "status": status, "push": push,
            **({"error_message": push.get("error_message")} if status == "error" else {})}
