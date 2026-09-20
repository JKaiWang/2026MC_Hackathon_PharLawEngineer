"""Tool 層：把一則通知推到使用者手機（ntfy）。

為什麼是 ntfy：免帳號、免金鑰、iOS／Android 都有 app，手機訂閱一個 topic 名稱就收得到，
一個 HTTP POST 就發出去——hackathon demo 最不會出包的一條。缺點是 topic 名稱
等於收件位址，誰知道就能發，所以當秘密放 .env（NTFY_TOPIC），並建議用長隨機字串。
之後上 Cloud Run 有 HTTPS 再加 Web Push 也不衝突。

fixture 模式不會真的送，回 dry_run 並附上本來要送的內容，方便測試與離線 demo。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from api import Settings, load_settings

USER_AGENT = "NCKU-Smart-Commute/0.1 (DevJam TW 2026 hackathon prototype)"
PRIORITIES = ("min", "low", "default", "high", "urgent")
MAX_TITLE, MAX_MESSAGE = 120, 1000


def _now_iso(tz: str) -> str:
    return datetime.now(ZoneInfo(tz)).isoformat(timespec="seconds")


def send_push(title: str, message: str, priority: str = "default",
              tags: list[str] | None = None, click_url: str | None = None,
              settings: Settings | None = None) -> dict:
    """推一則通知到手機。

    適用時機：該出發了、教室臨時改了、路上出事了——使用者沒開著網頁也該知道的事。
    只在真的需要打斷使用者時用；同一件事不要重複推。

    Args:
        title: 標題，例如「該出發了」。
        message: 內文，一到三句話講清楚要做什麼。
        priority: "min"、"low"、"default"、"high"、"urgent"；來不及了才用 urgent。
        tags: ntfy 的 emoji 標籤，例如 ["warning"]、["bus"]。
        click_url: 點通知要開的網址（例如導航連結）。

    Returns:
        dict，包含：
        - status: "ok"（已送）、"dry_run"（fixture 模式，未送）、"error"
        - title、message、priority: 實際送出的內容
        - mode、sent_at: live／fixture 與時間
        - error_message: 僅在 error 時出現
    """
    settings = settings or load_settings()
    title = (title or "").strip()[:MAX_TITLE]
    message = (message or "").strip()[:MAX_MESSAGE]
    if priority not in PRIORITIES:
        raise ValueError(f"priority 必須是 {PRIORITIES} 之一，目前為 {priority!r}")
    base = {"status": "error", "title": title, "message": message, "priority": priority,
            "tags": list(tags or []), "click_url": click_url,
            "mode": settings.provider_mode, "sent_at": _now_iso(settings.timezone)}
    if not title or not message:
        return {**base, "error_message": "標題與內文都不可為空"}

    if settings.provider_mode == "fixture":
        return {**base, "status": "dry_run"}
    if not settings.ntfy_topic:
        return {**base, "error_message": "沒有設定 NTFY_TOPIC，無法推播"}

    # ntfy 的 HTTP 標頭只吃 latin-1，中文標題會爛掉；改用 JSON body 發到根路徑
    body = {"topic": settings.ntfy_topic, "title": title, "message": message,
            "priority": PRIORITIES.index(priority) + 1}
    if tags:
        body["tags"] = list(tags)
    if click_url:
        body["click"] = click_url
    try:
        resp = requests.post(settings.ntfy_server, json=body, timeout=settings.http_timeout_seconds,
                             headers={"User-Agent": USER_AGENT})
    except requests.Timeout:
        return {**base, "error_message": "推播服務逾時"}
    except requests.RequestException as exc:
        return {**base, "error_message": f"無法連線推播服務（{type(exc).__name__}）"}
    if resp.status_code != 200:
        return {**base, "error_message": f"推播服務回應 HTTP {resp.status_code}"}
    return {**base, "status": "ok"}
