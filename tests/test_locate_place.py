"""locate_place 測試：GIS 與 Gemini 全部換成假資料，不打網路也不花額度。"""
import pytest

from commute_agent.skills import locate_place as lp
from commute_agent.skills.locate_place import locate_course_place

CENTROIDS = {
    "B029": {"status": "ok", "name": "B501 資訊工程系館", "lat": 22.9972, "lon": 120.2208},
    "B204": {"status": "ok", "name": "B204 理化實驗大樓", "lat": 22.9978, "lon": 120.2186},
    "E901": {"status": "ok", "name": "E901 社會科學院大樓", "lat": 23.0019, "lon": 120.2166},
}

ROOMS = {
    "4264": [{"room_code": "4264", "room_name": "", "floor": "2F",
              "building_id": "B029", "building_name": "B501 資訊工程系館",
              "exact_match": True}],
    # 「格致廳」模糊比到兩間，但兩間都在同一棟，算得出答案
    "格致廳": [{"room_code": "35X01", "room_name": "格致廳(小講堂)", "floor": "B1",
                "building_id": "B204", "building_name": "B204 理化實驗大樓",
                "exact_match": False},
               {"room_code": "75X01", "room_name": "格致廳(大講堂)", "floor": "B1",
                "building_id": "B204", "building_name": "B204 理化實驗大樓",
                "exact_match": False}],
    # 「階梯教室」十幾棟大樓都有，這種就該放棄
    "階梯教室": [{"room_code": "61401", "room_name": "階梯教室", "floor": "4F",
                  "building_id": "A702", "building_name": "A702 工資管系館",
                  "exact_match": True},
                 {"room_code": "62333", "room_name": "階梯教室", "floor": "3F",
                  "building_id": "A703", "building_name": "A703 管理學院綜合大樓",
                  "exact_match": True}],
}

PLACES = {
    "社會科學院": {"status": "ok", "name": "E901 社會科學院大樓", "build_id": "E901",
                   "lat": 23.0019, "lon": 120.2166},
    # 「資訊大樓格致廳小講堂」剪掉尾巴就查得到，而它跟資訊系館是不同建築
    "資訊大樓": {"status": "ok", "name": "B003 資訊大樓", "build_id": "B006",
                 "lat": 22.9978, "lon": 120.2186},
    "資訊系館": {"status": "ok", "name": "B502 資訊工程系大樓", "build_id": "B102",
                 "lat": 22.9972, "lon": 120.2214},
}


@pytest.fixture
def world(monkeypatch):
    state = {"keywords": {"building_keywords": [], "room_keywords": [], "note": ""},
             "google": {"status": "not_found"}, "asked": [],
             # 本機 Gemma 預設當作沒起來，測試才不會真的打到開發者機器上的 Ollama
             "gemma": {"status": "unavailable", "name": "", "build_id": "",
                       "confidence": 0.0, "reason": "", "model": "gemma3:4b"},
             "gemma_asked": []}

    def fake_lookup(query):
        rows = ROOMS.get(query, [])
        return {"status": "ok" if rows else "not_found", "candidates": rows,
                "exact_match_count": sum(1 for r in rows if r["exact_match"])}

    def fake_resolve(query):
        return {**PLACES.get(query, {"status": "not_found", "name": "",
                                     "build_id": "", "lat": None, "lon": None}),
                "matched_query": query}

    def fake_keywords(text):
        state["asked"].append(text)
        return state["keywords"]

    monkeypatch.setattr(lp, "lookup_room", fake_lookup)
    monkeypatch.setattr(lp, "resolve_place", fake_resolve)
    monkeypatch.setattr(lp, "get_building_centroid",
                        lambda bid: CENTROIDS.get(bid, {"status": "not_found",
                                                        "lat": None}))
    def fake_guess(text):
        state["gemma_asked"].append(text)
        return state["gemma"]

    monkeypatch.setattr(lp, "ask_for_keywords", fake_keywords)
    monkeypatch.setattr(lp, "guess_building", fake_guess)
    monkeypatch.setattr(lp, "geocode_place", lambda t: state["google"])
    lp._CACHE.clear()
    return state


def test_room_code_wins_and_costs_no_model_call(world):
    found = locate_course_place("資訊系館4264", "4264")
    assert found["source"] == "gis_room_code"
    assert (found["lat"], found["lon"]) == (22.9972, 120.2208)
    assert found["is_verified"] is True
    assert world["asked"] == []          # 有代碼就不該問模型


def test_gemini_keyword_is_verified_against_the_gis(world):
    # 課表寫「社科院大樓階梯教室－心理」，GIS 查不到，模型把它讀成社會科學院
    world["keywords"] = {"building_keywords": ["社會科學院"], "room_keywords": [],
                         "note": "社科院即社會科學院"}
    found = locate_course_place("社科院大樓階梯教室－心理")
    assert found["source"] == "gemini_building"
    assert found["name"] == "E901 社會科學院大樓"
    assert found["is_verified"] is True


def test_ambiguous_room_keyword_is_rejected(world):
    # 「階梯教室」在十幾棟都有，隨便挑一棟會把人導到別的學院
    world["keywords"] = {"building_keywords": ["社會科學院"],
                         "room_keywords": ["階梯教室"], "note": ""}
    found = locate_course_place("社科院大樓階梯教室－心理")
    assert found["source"] == "gemini_building"
    assert found["build_id"] == "E901"


def test_fuzzy_rooms_agreeing_on_one_building_are_accepted(world):
    # 格致廳大、小講堂都在 B204，兩筆指向同一個答案
    world["keywords"] = {"building_keywords": [], "room_keywords": ["格致廳"],
                         "note": ""}
    # 不帶大樓名，前綴那一層沒辦法，才會走到模型這條路
    found = locate_course_place("格致廳小講堂")
    assert found["source"] == "gemini_room"
    assert found["build_id"] == "B204"


def test_a_trimmed_prefix_finds_the_building_without_the_model(world):
    # 「資訊大樓格致廳小講堂」整串查不到，剪到「資訊大樓」就中了
    found = locate_course_place("資訊大樓格致廳小講堂")
    assert found["source"] == "gis_prefix"
    assert found["build_id"] == "B006"
    assert world["asked"] == []          # 前綴有中就不該花模型額度


def test_the_prefix_step_never_overrides_a_room_code(world):
    # 「資訊系館」當關鍵字會比到 B502，但 4264 其實在 B501，
    # 有代碼時就該以代碼為準 —— 兩棟資工大樓差了幾百公尺
    found = locate_course_place("資訊系館4264", "4264")
    assert found["source"] == "gis_room_code"
    assert found["build_id"] == "B029"


def test_a_prefix_that_is_too_short_is_not_tried(world):
    # 兩三個字的片段會比到不相干的大樓
    assert locate_course_place("某地")["status"] == "not_found"


def test_google_is_the_last_resort_and_is_flagged(world):
    world["google"] = {"status": "ok", "lat": 22.99, "lon": 120.21, "name": "某處"}
    found = locate_course_place("某個查不到的地方")
    assert found["source"] == "google"
    assert found["is_verified"] is False


def test_nothing_found_is_said_plainly(world):
    found = locate_course_place("某個查不到的地方")
    assert found["status"] == "not_found"
    assert found["lat"] is None
    assert "查不到" in found["error_message"]


def test_the_model_is_not_asked_twice_for_the_same_place(world):
    world["keywords"] = {"building_keywords": ["社會科學院"], "room_keywords": [],
                         "note": ""}
    locate_course_place("社科院大樓階梯教室－心理")
    locate_course_place("社科院大樓階梯教室－心理")
    assert len(world["asked"]) == 1


def test_gemini_can_be_switched_off(world):
    locate_course_place("社科院大樓階梯教室－心理", use_gemini=False)
    assert world["asked"] == []


def test_model_failure_does_not_raise(world):
    # ask_for_keywords 失敗時回空清單，整支要安靜地退到下一層
    world["keywords"] = {"building_keywords": [], "room_keywords": [],
                         "note": "Gemini 無法回應（TimeoutError）"}
    assert locate_course_place("社科院大樓階梯教室－心理")["status"] == "not_found"


# ---------- 本機 Gemma ----------

def test_local_gemma_is_tried_before_gemini_and_verified_by_gis(world):
    world["gemma"] = {"status": "ok", "name": "E901 社會科學院大樓", "build_id": "E901",
                      "confidence": 0.9, "reason": "社科院即社會科學院", "model": "gemma3:4b"}
    found = locate_course_place("社科院大樓階梯教室－心理")
    assert found["source"] == "gemma_building"
    assert found["is_verified"] is True
    assert found["lat"] == CENTROIDS["E901"]["lat"]
    assert world["asked"] == []  # 本機答得出來就不打雲端 Gemini


def test_local_gemma_unavailable_falls_through_to_gemini(world):
    world["keywords"] = {"building_keywords": ["社會科學院"], "room_keywords": [], "note": ""}
    found = locate_course_place("社科院大樓階梯教室－心理")
    assert found["source"] == "gemini_building"
    assert world["gemma_asked"] == ["社科院大樓階梯教室－心理"]


def test_local_gemma_building_without_gis_centroid_is_rejected(world):
    # 模型挑了一棟 GIS 給不出座標的樓：不採用，往下走
    world["gemma"] = {"status": "ok", "name": "Z999 幽靈大樓", "build_id": "Z999",
                      "confidence": 0.95, "reason": "", "model": "gemma3:4b"}
    found = locate_course_place("社科院大樓階梯教室－心理")
    assert found["source"] != "gemma_building"


def test_local_gemma_has_its_own_switch(world):
    locate_course_place("社科院大樓階梯教室－心理", use_local_llm=False)
    assert world["gemma_asked"] == []                        # 關本機就不問 Gemma
    assert world["asked"] == ["社科院大樓階梯教室－心理"]   # 但雲端照問
    locate_course_place("社科院大樓階梯教室－心理", use_gemini=False)
    assert world["gemma_asked"] == ["社科院大樓階梯教室－心理"]  # 關雲端不影響本機
    assert len(world["asked"]) == 1                          # 雲端沒再被問
