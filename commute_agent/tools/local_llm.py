"""Tool 層：本機 Ollama（跑 Gemma）的最小 client。

為什麼要有本地模型：課表地點、教授信、住家附近的描述都是個資，能在機器上
處理完就不必送雲端；沒網路（或 Gemini 額度用完）時也還能運作。這正是題目
要 Gemma 的理由——行動端／離線／隱私——不是為了湊模型數。

本檔只做「打 Ollama、拿 JSON」，不做任何判斷。Ollama 沒起來就回 unavailable，
呼叫端據此退回雲端 Gemini；絕不因為本地模型不在就整條流程炸掉。

啟動：
    ollama serve            # 或裝好後的常駐程式
    ollama pull gemma3:4b   # 3.3GB，CPU 也跑得動，每次判讀約 3–8 秒
"""

from __future__ import annotations

import json
from typing import Any

import requests

from api import Settings, load_settings

USER_AGENT = "NCKU-Smart-Commute/0.1 (DevJam TW 2026 hackathon prototype)"
PROBE_TIMEOUT_S = 1.5
# CPU 推論慢，第一次還要載模型；比一般 API 的 8 秒寬鬆很多
CHAT_TIMEOUT_S = 120.0


class LocalLLMError(RuntimeError):
    """Ollama 沒起來、模型沒拉、或回應不是合法 JSON。"""


def is_available(settings: Settings | None = None) -> bool:
    """Ollama 有在跑而且設定的模型已經拉下來了嗎？失敗一律 False，不丟例外。"""
    settings = settings or load_settings()
    try:
        resp = requests.get(f"{settings.ollama_url}/api/tags", timeout=PROBE_TIMEOUT_S,
                            headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            return False
        names = {m.get("name", "") for m in (resp.json().get("models") or [])}
    except (requests.RequestException, ValueError):
        return False
    want = settings.local_llm_model
    # "gemma3:4b" 與 "gemma3:4b-it-q4_K_M" 這類完整 tag 都算同一個模型
    return any(n == want or n.startswith(want + "-") or n.split(":")[0] == want for n in names)


def chat_json(system: str, user: str, settings: Settings | None = None,
              num_ctx: int = 8192) -> dict[str, Any]:
    """送一組 system + user 訊息，要求模型只回 JSON，回傳解析後的 dict。

    Raises:
        LocalLLMError: 連不上、HTTP 非 200、或回應不是 JSON 物件。
    """
    settings = settings or load_settings()
    body = {
        "model": settings.local_llm_model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": num_ctx},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    try:
        resp = requests.post(f"{settings.ollama_url}/api/chat", json=body,
                             timeout=CHAT_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    except requests.Timeout as exc:
        raise LocalLLMError("本機模型回應逾時") from exc
    except requests.RequestException as exc:
        raise LocalLLMError(f"無法連線 Ollama（{type(exc).__name__}）") from exc
    if resp.status_code != 200:
        raise LocalLLMError(f"Ollama 回應 HTTP {resp.status_code}")
    try:
        content = resp.json()["message"]["content"]
        answer = json.loads(content)
    except (ValueError, KeyError, TypeError) as exc:
        raise LocalLLMError("本機模型回應不是合法 JSON") from exc
    if not isinstance(answer, dict):
        raise LocalLLMError("本機模型回應不是 JSON 物件")
    return answer
