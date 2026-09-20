"""Tool 層：把 Gemini SDK 丟出來的例外翻成看得懂的話，並判斷值不值得重試。

google-genai 把各種狀況都包成 ClientError 或 ServerError，訊息長成
`402 RESOURCE_EXHAUSTED. {'error': {...}}`。只印例外類別名稱的話，畫面上會出現
「Gemini 無法回應（ClientError）」——這句話對使用者與開發者都毫無用處：
額度用完、金鑰打錯、尖峰塞車三件事完全不同，處理方式也不同，卻長得一模一樣。

（2026-09-19 實測：專案的預付額度用盡時，連 Gemma 也是同一個 402，
也就是說換模型救不了，只能去 AI Studio 加值或換一把金鑰。）

重試與否也由狀態碼決定：5xx 是對方暫時忙，過兩秒再試多半就過了；
402、401、403 是帳號或金鑰的問題，重試只是讓使用者多等幾秒看同一個錯。
"""

from __future__ import annotations

import re

# 狀態碼 → 給使用者看的說法。沒列到的就退回原始訊息
STATUS_HINTS = {
    401: "Gemini 金鑰無效",
    403: "Gemini 金鑰沒有這個模型的權限",
    402: "Gemini 預付額度已用盡，請到 AI Studio 加值或改用其他金鑰",
    404: "找不到這個 Gemini 模型（可能已下架，請改設定 GEMINI_MODEL）",
    429: "Gemini 請求速率／免費額度已達上限，請稍後重試或檢查 AI Studio 計費與配額",
    500: "Gemini 伺服器錯誤",
    503: "Gemini 目前流量過大",
    504: "Gemini 回應逾時",
}

# 這些狀態碼過一下再試多半就成功了；其餘的重試只是讓使用者乾等
RETRYABLE_STATUS = (500, 502, 503, 504)

_STATUS = re.compile(r"\b([1-5]\d{2})\b")


def status_code(exc: Exception) -> int | None:
    """從例外裡挖出 HTTP 狀態碼。SDK 沒有統一的欄位，所以兩種都試。"""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    found = _STATUS.search(str(exc))
    return int(found.group(1)) if found else None


def is_retryable(exc: Exception) -> bool:
    """這個錯誤再試一次有沒有機會成功。

    認不出狀態碼時當成可重試：逾時與連線中斷都長這樣，而多試一次的代價
    只是幾秒鐘，比直接放棄好。
    """
    code = status_code(exc)
    return code is None or code in RETRYABLE_STATUS


def describe(exc: Exception) -> str:
    """一句話講清楚 Gemini 為什麼沒回應，適合直接顯示給使用者。"""
    code = status_code(exc)
    hint = STATUS_HINTS.get(code)
    if hint:
        return f"{hint}（HTTP {code}）"
    detail = re.sub(r"\s+", " ", str(exc)).strip()
    return f"{type(exc).__name__}：{detail[:120]}" if detail else type(exc).__name__
