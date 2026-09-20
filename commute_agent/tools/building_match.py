"""Tool 層：給本地 Gemma 一份成大大樓清單，讓它判斷一段模糊地點文字是哪一棟。

清單 data/ncku_buildings.json 由 scripts/build_ncku_buildings.py 從成大 GIS 撈出
（181 棟，2026-09-20），每筆有正式名稱（含門牌代碼，如「B501 資訊工程系館」）、
別名與 GIS 內部 id。模型只准從清單裡挑；程式再把它回的名稱對回清單，
對不到就當作編造，回 not_found——模型講得出來的名字不代表存在。

2026-09-20 用 gemma3:4b 對十筆真實輸入實測：課表地點、停車場名、口語簡稱
八筆正確，校外地址兩筆都正確拒絕；錯的兩筆（「資訊大樓格致廳小講堂」對到
修齊大樓、「光復校區機車停車場」硬猜宿舍）在 locate_place 的流程裡會先被
GIS 原文查詢或 build_id 驗證擋掉，不會直達使用者。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from api import Settings, load_settings
from commute_agent.tools.local_llm import LocalLLMError, chat_json, is_available

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BUILDINGS_PATH = PROJECT_ROOT / "data" / "ncku_buildings.json"

SYSTEM = """你是成功大學校園地點判讀器。下面是成大所有大樓的清單，每行格式為「正式名稱｜別名」，正式名稱開頭的英數字（例如 B501）是門牌代碼。
使用者會給一段地點文字，可能來自課表、停車場名稱、路況通報或口語。
你的工作：判斷它指的是清單裡的哪一棟大樓。
規則：
1. 只能從清單裡挑，不可以自己發明大樓。
2. 文字若是校外地址、路名、路段（例如含「區」「路」「巷」「號」且對不到任何大樓），building_name 填 null。
3. 教室代碼（4 到 5 位數字）本身不足以判斷大樓，除非文字裡另有大樓名稱；只有代碼時 confidence 要低並說明。
4. 「XX樓」「XX館」「XX大樓」這類簡稱要對到含該字串的正式名稱或別名。
5. 停車場、校門、操場不是大樓；文字只有校區名（例如「光復校區」）也不足以判斷是哪一棟，這兩種 building_name 填 null。
只輸出 JSON：{"building_name": 清單裡一模一樣的正式名稱或null, "confidence": 0到1, "reason": "一句話"}
"""

_CODE_RE = re.compile(r"^([A-Z]\d{3})\b")
_ROWS: list[dict] | None = None


def load_buildings(path: Path = BUILDINGS_PATH) -> list[dict]:
    """讀大樓清單；檔案不存在回空清單，讓呼叫端如實回報而不是炸掉。"""
    global _ROWS
    if _ROWS is None:
        _ROWS = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    return _ROWS


def build_document(rows: list[dict]) -> str:
    """把清單排成模型讀的文字。只給名稱與別名，不給 GIS id——兩組代碼會把模型搞混。"""
    lines = []
    for r in rows:
        kw = (r.get("keyword") or "").replace("\n", " ").strip()
        lines.append(f"{r['name']}｜{kw}" if kw else r["name"])
    return "\n".join(lines)


def match_row(rows: list[dict], answer: str | None) -> dict | None:
    """把模型回的名稱對回清單。純函式。

    模型常多帶別名（「B501 資訊工程系館｜資訊系館」）或掉了門牌代碼
    （「雲平大樓東棟」），所以依序試：完全相同 → 門牌代碼相同 → 清單名稱
    （去代碼）是回答的子字串。都對不到就回 None，當作編造。
    """
    ans = (answer or "").strip()
    if not ans:
        return None
    head = ans.split("｜", 1)[0].strip()
    for r in rows:
        if r["name"] in (ans, head):
            return r
    m = _CODE_RE.match(head)
    if m:
        prefix = m.group(1) + " "
        for r in rows:
            if r["name"].startswith(prefix):
                return r
    for r in rows:
        bare = r["name"].split(" ", 1)[-1]
        if bare and bare in head:
            return r
    return None


def guess_building(text: str, settings: Settings | None = None) -> dict:
    """用本地 Gemma 判斷一段地點文字是成大的哪一棟大樓。

    適用時機：課表、停車場名稱、口語簡稱這種查 GIS 查不到的寫法。
    回傳的 build_id 是成大 GIS 的內部 id，可直接拿去查中心點座標。

    Returns:
        dict，包含：
        - status: "ok"（對到清單裡的大樓）、"not_found"（模型判斷不是校內大樓，
          或它回的名字清單裡沒有）、"unavailable"（Ollama 沒起來或模型沒拉）、
          "error"（模型回應異常）
        - name、build_id: 清單裡的正式名稱與 GIS id；not_found 時為空
        - confidence、reason: 模型自報的信心與一句話理由
        - model_answer: 模型原始回的名稱，方便對照它有沒有編造
        - source: "gemma_local"
        - error_message: 僅在 status 為 "unavailable" / "error" 時出現
    """
    settings = settings or load_settings()
    base = {"status": "not_found", "query": text, "name": "", "build_id": "",
            "confidence": 0.0, "reason": "", "model_answer": None,
            "source": "gemma_local", "model": settings.local_llm_model}
    rows = load_buildings()
    if not rows:
        return {**base, "status": "error", "error_message": f"找不到大樓清單 {BUILDINGS_PATH.name}"}
    if not (text or "").strip():
        return {**base, "status": "error", "error_message": "地點文字不可為空"}
    if not is_available(settings):
        return {**base, "status": "unavailable",
                "error_message": f"本機 Ollama 未啟動或尚未拉取 {settings.local_llm_model}"}

    try:
        answer = chat_json(SYSTEM + "\n\n大樓清單：\n" + build_document(rows),
                           f"地點文字：{text.strip()}", settings)
    except LocalLLMError as exc:
        return {**base, "status": "error", "error_message": str(exc)}

    name = answer.get("building_name")
    try:
        confidence = float(answer.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    result = {**base, "confidence": confidence, "reason": str(answer.get("reason") or ""),
              "model_answer": name}
    row = match_row(rows, name) if name else None
    if row is None:
        return result
    return {**result, "status": "ok", "name": row["name"], "build_id": row["id"]}
