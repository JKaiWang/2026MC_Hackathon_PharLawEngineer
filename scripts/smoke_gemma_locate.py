"""在自己電腦上用本機 Gemma（Ollama）判讀真實地點文字，並走完整條 locate_course_place。

需要：ollama serve 在跑、已 ollama pull gemma3:4b（或 .env 的 LOCAL_LLM_MODEL）。
GIS 座標驗證會打真實成大 GIS，所以 PROVIDER_MODE 強制 live。

用法：
    python scripts/smoke_gemma_locate.py                       # 預設十筆真實輸入
    python scripts/smoke_gemma_locate.py "社科院大樓階梯教室－心理"
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROVIDER_MODE"] = "live"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from api import load_settings  # noqa: E402
from commute_agent.skills.locate_place import locate_course_place  # noqa: E402
from commute_agent.tools.building_match import guess_building  # noqa: E402
from commute_agent.tools.local_llm import is_available  # noqa: E402

DEFAULT_CASES = [
    "資訊系館4264", "唯農大樓7208", "資訊大樓格致廳小講堂", "成大圖書館", "三系館",
    "奇美樓地下機車停車場", "雲平東側", "社科院大樓階梯教室－心理",
    "null北外環 長和路一段222巷100弄口至北外環路口", "臺南市東區怡東路21號",
]


def main() -> None:
    settings = load_settings()
    if not is_available(settings):
        print(f"Ollama 沒起來或還沒拉 {settings.local_llm_model}：先 `ollama serve` 與 `ollama pull {settings.local_llm_model}`")
        sys.exit(1)
    cases = sys.argv[1:] or DEFAULT_CASES
    for text in cases:
        t0 = time.perf_counter()
        g = guess_building(text, settings)
        dt = time.perf_counter() - t0
        print(f"[{text}]  {dt:.1f}s")
        print(f"   Gemma → {g['status']}: {g['name'] or g['model_answer'] or '（不是校內大樓）'}"
              f"  conf={g['confidence']}  {g['reason']}")
        full = locate_course_place(text, use_gemini=False, use_local_llm=True)  # 本機 + GIS，不動雲端額度
        print(f"   locate_course_place → source={full.get('source')} "
              f"name={full.get('name') or '-'} verified={full.get('is_verified')}")
        print()


if __name__ == "__main__":
    main()
