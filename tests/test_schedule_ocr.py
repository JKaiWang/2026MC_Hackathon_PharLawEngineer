"""schedule_ocr Tool 測試：只測不碰網路的解析與驗證，不實際呼叫 Gemini。

重點是「模型會回髒資料」這個前提：中文星期、全形冒號、markdown 圍欄、
缺欄位、只給節次不給時間，這些都必須被收斂或明確跳過，不能污染課表。
"""
import json

import pytest

from commute_agent.tools import schedule_ocr as ocr


def payload(courses) -> str:
    return json.dumps({"courses": courses}, ensure_ascii=False)


# --- 欄位正規化 -------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("Monday", "Monday"), ("monday", "Monday"), ("Mon", "Monday"),
    ("星期一", "Monday"), ("週一", "Monday"), ("禮拜一", "Monday"),
    ("一", "Monday"), ("1", "Monday"),
    ("星期日", "Sunday"), ("日", "Sunday"), ("7", "Sunday"),
])
def test_normalize_day_accepts_common_spellings(value, expected):
    assert ocr.normalize_day(value) == expected


@pytest.mark.parametrize("value", ["", None, "火曜日", "Someday", "8", "0"])
def test_normalize_day_rejects_unknown(value):
    assert ocr.normalize_day(value) == ""


@pytest.mark.parametrize("value,expected", [
    ("09:05", "09:05"), ("9:05", "09:05"), ("09：05", "09:05"),
    (" 13:10 ", "13:10"), ("23:59", "23:59"), ("00:00", "00:00"),
])
def test_normalize_time_pads_and_accepts_fullwidth_colon(value, expected):
    assert ocr.normalize_time(value) == expected


@pytest.mark.parametrize("value", ["", None, "24:00", "9", "9:5", "25:10", "abc"])
def test_normalize_time_rejects_invalid(value):
    assert ocr.normalize_time(value) == ""


def test_normalize_periods_uppercases_dedupes_and_filters():
    assert ocr.normalize_periods(["3", 4, "a", "3", "zz"]) == ["3", "4", "A"]


def test_normalize_periods_rejects_non_list():
    assert ocr.normalize_periods("3,4") == []


def test_times_from_periods_spans_first_to_last():
    assert ocr.times_from_periods(["3", "4"]) == ("10:10", "12:00")
    # 順序顛倒也要拿到同一段區間
    assert ocr.times_from_periods(["4", "3"]) == ("10:10", "12:00")


def test_times_from_periods_empty():
    assert ocr.times_from_periods([]) == ("", "")


def test_strip_code_fence_removes_markdown_wrapper():
    assert ocr.strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert ocr.strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert ocr.strip_code_fence('{"a": 1}') == '{"a": 1}'


# --- 整批解析 ---------------------------------------------------------------

def test_parse_happy_path():
    result = ocr.parse_schedule_payload(payload([
        {"name": "數位IC設計", "day": "星期一", "periods": ["2", "3", "4"],
         "start_time": "09:10", "end_time": "12:00", "location": "資訊系館4264"},
    ]), source="shot.png")

    assert result["source"] == "shot.png"
    assert result["skipped"] == []
    course = result["courses"][0]
    assert course["name"] == "數位IC設計"
    assert course["day"] == "Monday"
    assert course["day_zh"] == "星期一"
    assert course["start_time"] == "09:10"
    assert course["location"] == "資訊系館4264"


def test_parse_fills_times_from_periods_when_image_has_no_clock():
    result = ocr.parse_schedule_payload(payload([
        {"name": "基礎學術英文", "day": "Wednesday", "periods": ["3", "4"],
         "start_time": "", "end_time": "", "location": "修齊大樓22201"},
    ]))
    course = result["courses"][0]
    assert (course["start_time"], course["end_time"]) == ("10:10", "12:00")


def test_parse_accepts_code_fenced_json():
    raw = '```json\n' + payload([
        {"name": "日文", "day": "Monday", "periods": ["5", "6"],
         "start_time": "13:10", "end_time": "15:00", "location": "唯農大樓7208"},
    ]) + '\n```'
    assert len(ocr.parse_schedule_payload(raw)["courses"]) == 1


def test_parse_skips_incomplete_course_instead_of_failing_whole_batch():
    result = ocr.parse_schedule_payload(payload([
        {"name": "好課", "day": "Monday", "periods": ["3"],
         "start_time": "10:10", "end_time": "11:00", "location": "資訊系館4264"},
        {"name": "看不清楚的課", "day": "", "periods": [],
         "start_time": "", "end_time": "", "location": ""},
    ]))
    assert [c["name"] for c in result["courses"]] == ["好課"]
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["name"] == "看不清楚的課"
    assert "星期" in result["skipped"][0]["reason"]


def test_parse_skips_course_with_end_not_after_start():
    result = ocr.parse_schedule_payload(payload([
        {"name": "壞時間", "day": "Monday", "periods": [],
         "start_time": "11:00", "end_time": "10:00", "location": "資訊系館4264"},
    ]))
    assert result["courses"] == []
    assert "不晚於" in result["skipped"][0]["reason"]


def test_parse_sorts_by_weekday_then_time():
    result = ocr.parse_schedule_payload(payload([
        {"name": "週三課", "day": "Wednesday", "periods": ["3"],
         "start_time": "10:10", "end_time": "11:00", "location": "A101"},
        {"name": "週一下午", "day": "Monday", "periods": ["5"],
         "start_time": "13:10", "end_time": "14:00", "location": "A102"},
        {"name": "週一早上", "day": "Monday", "periods": ["1"],
         "start_time": "08:10", "end_time": "09:00", "location": "A103"},
    ]))
    assert [c["name"] for c in result["courses"]] == ["週一早上", "週一下午", "週三課"]


def test_parse_output_is_readable_by_load_courses(tmp_path):
    """辨識結果必須能直接餵給既有的 class_schedule.load_courses。"""
    from commute_agent.tools.class_schedule import load_courses

    result = ocr.parse_schedule_payload(payload([
        {"name": "分散式系統", "day": "Thursday", "periods": ["2", "3", "4"],
         "start_time": "09:10", "end_time": "12:00", "location": "資訊系館4264"},
    ]))
    path = tmp_path / "user_schedule.json"
    path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    assert load_courses(path)[0]["name"] == "分散式系統"


@pytest.mark.parametrize("raw", ["", "不是 JSON", "[1,2,3]", '{"data": []}'])
def test_parse_rejects_unusable_model_output(raw):
    with pytest.raises(ocr.OCRError):
        ocr.parse_schedule_payload(raw)


def test_parse_skips_non_object_entry():
    result = ocr.parse_schedule_payload(json.dumps({"courses": ["我不是物件"]}))
    assert result["courses"] == []
    assert result["skipped"][0]["reason"] == "不是物件"


# --- 上傳前的把關（不呼叫模型就該擋下來）-----------------------------------

def test_extract_rejects_unsupported_mime():
    with pytest.raises(ocr.OCRError, match="不支援的圖片格式"):
        ocr.extract_schedule_from_image(b"x", "application/pdf")


def test_extract_rejects_empty_image():
    with pytest.raises(ocr.OCRError, match="空的"):
        ocr.extract_schedule_from_image(b"", "image/png")


def test_extract_rejects_oversized_image():
    with pytest.raises(ocr.OCRError, match="上限"):
        ocr.extract_schedule_from_image(b"x" * (ocr.MAX_IMAGE_BYTES + 1), "image/png")


def test_extract_reports_missing_key_without_calling_model(monkeypatch):
    monkeypatch.setattr(ocr, "load_settings",
                        lambda *a, **k: type("S", (), {"gemini_api_key": "",
                                                       "gemini_model": "m",
                                                       "timezone": "Asia/Taipei"})())
    with pytest.raises(ocr.OCRError, match="尚未設定"):
        ocr.extract_schedule_from_image(b"fake-png-bytes", "image/png")


def test_extract_reports_gemini_quota_without_raw_sdk_error(monkeypatch):
    class QuotaError(Exception):
        code = 429

    class FakeModels:
        def generate_content(self, **kwargs):
            raise QuotaError("429 RESOURCE_EXHAUSTED internal details")

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(ocr, "load_settings",
                        lambda *a, **k: type("S", (), {
                            "gemini_api_key": "test-key",
                            "gemini_model": "m",
                            "timezone": "Asia/Taipei",
                        })())
    monkeypatch.setattr("google.genai.Client", lambda **kwargs: FakeClient())

    with pytest.raises(ocr.OCRError) as exc_info:
        ocr.extract_schedule_from_image(b"fake-png-bytes", "image/png")

    message = str(exc_info.value)
    assert "HTTP 429" in message
    assert "RESOURCE_EXHAUSTED" not in message
