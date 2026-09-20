"""用本機 Gemma 整理真實 TDX 路況地點文字（fixtures/ncku_traffic/Tainan.json 錄的）。

用法：python scripts/smoke_road_text.py [文字...]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROVIDER_MODE"] = "live"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from commute_agent.tools.road_text import normalize_road_text  # noqa: E402

DEFAULT = [
    "null北外環 長和路一段222巷100弄口至北外環路口",
    "永康區中山路(奧迪前-文華路)",
    "臺南市北區公園路和公園南路的交叉路口",
    "null法華街 (五妃街至大同路一段) 發生壅塞",
    "臺南市東區怡東路21號",
]

for t in sys.argv[1:] or DEFAULT:
    r = normalize_road_text(t)
    print(f"[{t}]\n   {r['status']} district={r['district']} roads={r['roads']} "
          f"section={r['section']} xing={r['is_intersection']}\n   cleaned={r['cleaned']}\n")
