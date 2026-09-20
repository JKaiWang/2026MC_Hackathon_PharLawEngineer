"""從成大 GIS 撈出全校大樓清單，寫成 data/ncku_buildings.json（給本機 Gemma 當判讀依據）。

成大 GIS 的 buildinfo.htm?action=search 只有關鍵字搜尋、沒有「列出全部」，
所以用一大堆常見字（館、樓、系、A–Z、0–9…）各搜一次再聯集去重。
2026-09-20 實測撈到 181 棟（濾掉沒有名稱的雜訊列後 180）。

用法：
    python scripts/build_ncku_buildings.py
"""

from __future__ import annotations

import json
import os
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROVIDER_MODE"] = "live"

import requests  # noqa: E402

from api import load_settings  # noqa: E402
from commute_agent.tools.building_match import BUILDINGS_PATH  # noqa: E402
from commute_agent.tools.ncku_geo import BUILDINFO_PATH, USER_AGENT  # noqa: E402

QUERIES = (
    list("館樓堂廳舍院中心場室校門宿系所大新舊東西南北一二三四五六七八九十"
         "工理管醫文法社電機化材土環資科生農藥")
    + list(string.ascii_uppercase)
    + [str(i) for i in range(10)]
)


def main() -> None:
    settings = load_settings()
    endpoint = settings.ncku_gis_base_url.rstrip("/") + BUILDINFO_PATH
    seen: dict[str, dict] = {}
    failed: list[str] = []
    for q in QUERIES:
        try:
            resp = requests.get(endpoint, params={"action": "search", "q": q, "locale": "zh-tw"},
                                timeout=settings.http_timeout_seconds,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            for row in resp.json().get("data") or []:
                if row.get("id") and (row.get("name") or "").strip():
                    seen[row["id"]] = {
                        "id": row["id"],
                        "campusId": row.get("campusId", ""),
                        "name": row["name"].strip(),
                        "keyword": (row.get("keyword") or "").strip(),
                    }
        except (requests.RequestException, ValueError):
            failed.append(q)

    rows = sorted(seen.values(), key=lambda r: r["name"])
    BUILDINGS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {BUILDINGS_PATH}：{len(rows)} 棟；失敗的關鍵字：{failed or '無'}")


if __name__ == "__main__":
    main()
