"""Tool 層：把課表截圖交給 Gemini 辨識成結構化課表。

分成兩半，理由是測試：
- `parse_schedule_payload` 等純函式負責解析與驗證模型輸出，不碰網路，可完整測試；
- `extract_schedule_from_image` 只負責把圖片送出去、把回應交給前者。

模型有可能回傳缺欄位、日期寫中文、只給節次不給時間的資料，全部在這裡收斂成
`class_schedule.py` 的 `load_courses` 讀得懂的格式，讓下游不必處理髒資料。
"""

from __future__ import annotations

import json
import re
from typing import Any

from api import load_settings
from commute_agent.tools.gemini_error import describe

# 允許上傳的圖片格式。課表截圖就是這幾種，不必開更大。
SUPPORTED_MIME_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp", "image/heic", "image/heif",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# 成大節次對照表。模型讀得到圖上印的時間時一律以圖為準，這張表只在課表只寫
# 節次、沒印時間時用來補；數字節次與專案既有的 class_schedule.json 一致。
# 夜間 A~E 節各校區公告偶有差異，如學校有更新請以教務處節次表為準。
PERIOD_TIMES: dict[str, tuple[str, str]] = {
    "0": ("07:10", "08:00"),
    "1": ("08:10", "09:00"),
    "2": ("09:10", "10:00"),
    "3": ("10:10", "11:00"),
    "4": ("11:10", "12:00"),
    "N": ("12:10", "13:00"),
    "5": ("13:10", "14:00"),
    "6": ("14:10", "15:00"),
    "7": ("15:10", "16:00"),
    "8": ("16:10", "17:00"),
    "9": ("17:10", "18:00"),
    "A": ("18:30", "19:20"),
    "B": ("19:25", "20:15"),
    "C": ("20:20", "21:10"),
    "D": ("21:15", "22:05"),
    "E": ("22:10", "23:00"),
}

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday")
ZH_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_ZH_DIGITS = "一二三四五六日"

# 模型可能回「星期一」「週一」「一」「Mon」「Monday」「1」，全部收斂成英文
_DAY_ALIASES: dict[str, str] = {}
for _i, _en in enumerate(WEEKDAY_NAMES):
    for _alias in (_en, _en[:3], ZH_WEEKDAYS[_i], f"週{_ZH_DIGITS[_i]}",
                   f"禮拜{_ZH_DIGITS[_i]}", _ZH_DIGITS[_i], str(_i + 1)):
        _DAY_ALIASES[_alias.lower()] = _en

TIME_RE = re.compile(r"^([0-2]?\d)[:：]([0-5]\d)$")

PROMPT = """你是課表辨識器。請讀這張大學課表圖片，抽出所有課程，只輸出 JSON。

輸出格式（不要加任何說明文字、不要用 markdown 圍欄）：
{"courses": [
  {"name": "課程名稱",
   "day": "Monday",
   "periods": ["3", "4"],
   "start_time": "10:10",
   "end_time": "12:00",
   "location": "資訊系館4264"}
]}

規則：
- day 一律用英文 Monday~Sunday。
- start_time / end_time 用 24 小時制 HH:MM。圖片上有印時間就以圖為準；
  只有節次沒有時間時，這兩個欄位留空字串，我會用節次自己換算。
- periods 是節次字串陣列，例如 ["3","4"] 或 ["A"]；圖上沒有就給空陣列。
- location 請完整保留圖上寫的地點原文，包含大樓名稱與教室代碼，
  例如「資訊系館4264」「雲平大樓27103」。看不到地點就給空字串。
- 同一門課如果一週上好幾天，每一天各拆成一筆。
- 看不清楚或不確定的欄位就留空，不要猜、不要自己編造課名或教室。
"""


class OCRError(RuntimeError):
    """辨識流程失敗（缺金鑰、圖片不合格、模型回傳無法解析等）。"""


def normalize_day(value: Any) -> str:
    """把各種星期寫法收斂成英文；認不出來回空字串。"""
    text = str(value or "").strip().lower()
    return _DAY_ALIASES.get(text, "")


def normalize_time(value: Any) -> str:
    """把 9:5、09：05 這種寫法補成標準 HH:MM；不合法回空字串。"""
    match = TIME_RE.match(str(value or "").strip())
    if not match:
        return ""
    hour, minute = int(match.group(1)), match.group(2)
    return f"{hour:02d}:{minute}" if hour < 24 else ""


def normalize_periods(value: Any) -> list[str]:
    """節次統一成大寫字串清單，保留順序，濾掉節次表上沒有的值。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        key = str(item).strip().upper()
        if key in PERIOD_TIMES and key not in out:
            out.append(key)
    return out


def times_from_periods(periods: list[str]) -> tuple[str, str]:
    """用節次推算起訖時間：取最早一節的開始與最晚一節的結束。"""
    if not periods:
        return "", ""
    spans = [PERIOD_TIMES[p] for p in periods]
    return min(s for s, _ in spans), max(e for _, e in spans)


def strip_code_fence(text: str) -> str:
    """模型常把 JSON 包在 markdown 圍欄裡，這裡剝掉。"""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    body = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
    return re.sub(r"\s*```$", "", body).strip()


def parse_schedule_payload(raw: str, source: str = "") -> dict:
    """把模型輸出轉成 class_schedule.py 讀得懂的課表 dict。

    純函式（只讀設定，不碰網路）。單一課程解析失敗不會讓整批陣亡，而是被挑進
    `skipped`，讓使用者看得到哪幾堂沒讀成功，而不是默默少課。
    """
    try:
        payload = json.loads(strip_code_fence(raw))
    except (json.JSONDecodeError, TypeError) as exc:
        raise OCRError(f"模型回傳的不是合法 JSON：{exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("courses"), list):
        raise OCRError("模型回傳的 JSON 缺少 courses 陣列")

    settings = load_settings()
    courses: list[dict] = []
    skipped: list[dict] = []

    for index, raw_course in enumerate(payload["courses"]):
        if not isinstance(raw_course, dict):
            skipped.append({"index": index, "name": "(格式錯誤)", "reason": "不是物件"})
            continue

        name = str(raw_course.get("name") or "").strip()
        day = normalize_day(raw_course.get("day"))
        location = str(raw_course.get("location") or "").strip()
        periods = normalize_periods(raw_course.get("periods"))
        start = normalize_time(raw_course.get("start_time"))
        end = normalize_time(raw_course.get("end_time"))

        # 圖上沒印時間就用節次補；兩者都沒有的話這堂課排不進課表
        if not (start and end):
            fallback_start, fallback_end = times_from_periods(periods)
            start = start or fallback_start
            end = end or fallback_end

        missing = [label for label, value in
                   (("課名", name), ("星期", day), ("地點", location),
                    ("開始時間", start), ("結束時間", end)) if not value]
        if missing:
            skipped.append({"index": index, "name": name or "(無課名)",
                            "reason": "缺少 " + "、".join(missing)})
            continue
        if start >= end:
            skipped.append({"index": index, "name": name,
                            "reason": f"結束時間 {end} 不晚於開始時間 {start}"})
            continue

        courses.append({
            "name": name,
            "day": day,
            "day_zh": ZH_WEEKDAYS[WEEKDAY_NAMES.index(day)],
            "periods": periods,
            "start_time": start,
            "end_time": end,
            "location": location,
        })

    courses.sort(key=lambda c: (WEEKDAY_NAMES.index(c["day"]), c["start_time"]))
    return {
        "timezone": settings.timezone,
        "source": source,
        "courses": courses,
        "skipped": skipped,
    }


def extract_schedule_from_image(image_bytes: bytes, mime_type: str,
                                source: str = "") -> dict:
    """把課表圖片送給 Gemini 辨識，回傳結構化課表。

    Args:
        image_bytes: 圖片原始位元組。
        mime_type: 圖片的 MIME type，需在 SUPPORTED_MIME_TYPES 內。
        source: 記進課表的來源檔名，方便日後追查這份課表哪來的。

    Raises:
        OCRError: 缺金鑰、圖片格式或大小不符、模型回傳無法解析時。
    """
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise OCRError(f"不支援的圖片格式 {mime_type!r}，"
                       f"請改用 {'、'.join(sorted(SUPPORTED_MIME_TYPES))}")
    if not image_bytes:
        raise OCRError("圖片是空的")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise OCRError(f"圖片超過 {MAX_IMAGE_BYTES // 1024 // 1024} MB 上限")

    settings = load_settings()
    if not settings.gemini_api_key:
        raise OCRError("尚未設定 GOOGLE_API_KEY／GEMINI_API_KEY，無法辨識課表圖片")

    # 延後 import：沒用到辨識功能的流程（例如單元測試）不必付出 SDK 載入成本
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                PROMPT,
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:  # SDK 會丟各種自訂例外，一律轉成本模組的錯誤型別
        raise OCRError(f"呼叫 Gemini 失敗：{describe(exc)}") from exc

    if not (response.text or "").strip():
        raise OCRError("Gemini 沒有回傳內容，可能是圖片看不清楚或被安全政策擋下")

    return parse_schedule_payload(response.text, source=source)
