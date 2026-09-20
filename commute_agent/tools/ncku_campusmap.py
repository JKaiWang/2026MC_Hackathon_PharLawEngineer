"""Tool 層：校園全圖，把目標大樓在整個主校區的位置框出來。

原理跟 ncku_floorplan 框教室完全一樣，只是範圍從一層樓換成整個主校區：
- data/ncku_campus_buildings.json（由 scripts/build_campus_buildings.py
  離線產生）存了每棟建物的外接矩形，以及主校區（成功校區，A/B/C/D/E/F/G/L
  開頭的大樓）合起來的範圍。
- WMS GetMap 用主校區範圍要一張全校建物外框圖當底圖。
- 用跟教室平面圖同一支 locate_room_on_plan 換算目標大樓在圖上的百分比位置。

只有主校區的大樓能在這張圖上框出來；力行、建國等其他校區的大樓沒有校園全圖
（各校區之間隔太遠，硬塞進同一張圖只會變成一個看不出意義的點）。
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_PATH = PROJECT_ROOT / "data" / "ncku_campus_buildings.json"

GEOSERVER = "https://db.nckumap.ncku.edu.tw/geoNckuGis"
LAYER = "gis:bak_gis_building_v4"
SRS = "EPSG:3826"

# 校園全圖的範圍比建物外框大一圈，不然框到主校區邊界的大樓會被切一半
BBOX_PADDING_RATIO = 0.06

DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 900
MAX_PIXELS = 2048

_index_cache: dict | None = None


def load_index() -> dict:
    """讀全校建物外框索引。檔案不在時回空表，讓呼叫端照常走靜態備援。"""
    global _index_cache
    if _index_cache is None:
        if INDEX_PATH.is_file():
            _index_cache = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        else:
            _index_cache = {"main_campus": None, "buildings": {}}
    return _index_cache


def _padded_bbox(box: list[float]) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = box
    px = (maxx - minx) * BBOX_PADDING_RATIO
    py = (maxy - miny) * BBOX_PADDING_RATIO
    return minx - px, miny - py, maxx + px, maxy + py


def campus_map_url(width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> dict:
    """組出主校區全圖（建物外框）的 WMS 圖片網址。

    Returns:
        dict，含 status（"ok" 或 "not_found"，索引缺主校區範圍時）、url、
        bbox（EPSG:3826，供換算大樓位置用）、width、height。
    """
    main = load_index().get("main_campus")
    if not main or not main.get("bbox"):
        return {"status": "not_found", "url": None, "bbox": None,
                "width": None, "height": None,
                "error_message": "沒有主校區建物外框索引"}

    w = max(1, min(int(width), MAX_PIXELS))
    h = max(1, min(int(height), MAX_PIXELS))
    minx, miny, maxx, maxy = _padded_bbox(main["bbox"])
    query = urlencode({
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": LAYER, "srs": SRS,
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "width": w, "height": h, "format": "image/png", "transparent": "false",
    })
    return {"status": "ok", "url": f"{GEOSERVER}/wms?{query}",
            "bbox": {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
            "width": w, "height": h}


def get_building_footprint(build_id: str) -> dict | None:
    """查某棟大樓在主校區全圖上的外接矩形（EPSG:3826）。查不到回 None。"""
    bid = (build_id or "").strip().upper()
    return load_index().get("buildings", {}).get(bid)


def is_on_main_campus(build_id: str) -> bool:
    """這棟大樓是否在主校區全圖的範圍內（校園全圖只框得出這些大樓）。"""
    main = load_index().get("main_campus") or {}
    prefixes = tuple(main.get("prefixes") or ())
    return bool(prefixes) and (build_id or "").strip().upper().startswith(prefixes)
