"""把成大 GeoServer 的建物輪廓（全校）抓下來，存成本地索引。

成大地圖除了每間教室的 gis_room 圖層，還有一個全校建物外框圖層
gis:bak_gis_building_v4（WFS 實測：248 筆，BuildID 與 buildinfo.htm 的
大樓代碼同一套，178/180 對得上 data/ncku_buildings.json）。這支把它的
每棟外接矩形（EPSG:3826，跟 ncku_floorplan 用的樓層圖層同一套座標）
存下來，用來在校園全圖上把目標大樓框出來，作法跟樓層平面圖框教室完全一樣。

主校區（成功校區）是 A、B、C、D、E、F、G、L 開頭的大樓；力行、建國等其他
校區的代碼不在這幾個字母內，各自散得很開，硬塞進同一張圖只會變成一個點，
所以校園全圖目前只涵蓋主校區，其餘校區的建物只存座標、不歸進 "main"。

重建：
    ./.venv/bin/python scripts/build_campus_buildings.py
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "ncku_campus_buildings.json"

GEOSERVER = "https://db.nckumap.ncku.edu.tw/geoNckuGis"
LAYER = "gis:bak_gis_building_v4"
USER_AGENT = "NCKU-Smart-Commute/0.1 (DevJam TW 2026 hackathon prototype)"

MAIN_CAMPUS_PREFIXES = ("A", "B", "C", "D", "E", "F", "G", "L")


def fetch_features() -> list[dict]:
    resp = requests.get(f"{GEOSERVER}/wfs",
                        params={"service": "WFS", "version": "1.0.0",
                                "request": "GetFeature", "typeName": LAYER,
                                "outputFormat": "application/json"},
                        timeout=60, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json().get("features") or []


def _bounds_of(geometry: dict) -> dict | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node) -> None:
        if (isinstance(node, (list, tuple)) and len(node) >= 2
                and all(isinstance(v, (int, float)) for v in node[:2])):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates"))
    if not xs:
        return None
    return {"minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys)}


def main() -> None:
    buildings: dict[str, dict] = {}
    for feature in fetch_features():
        build_id = (feature.get("properties", {}).get("BuildID") or "").strip().upper()
        bounds = _bounds_of(feature.get("geometry") or {})
        if not build_id or not bounds:
            continue
        buildings[build_id] = bounds

    main_ids = [bid for bid in buildings if bid.startswith(MAIN_CAMPUS_PREFIXES)]
    minx = min(buildings[b]["minx"] for b in main_ids)
    miny = min(buildings[b]["miny"] for b in main_ids)
    maxx = max(buildings[b]["maxx"] for b in main_ids)
    maxy = max(buildings[b]["maxy"] for b in main_ids)

    payload = {
        "note": ("成大 GeoServer 全校建物外框索引，由 scripts/build_campus_buildings.py "
                 "產生。bbox 為 EPSG:3826，與 ncku_floorplan 的樓層圖層同一套座標。"),
        "geoserver": GEOSERVER,
        "layer": LAYER,
        "main_campus": {"prefixes": list(MAIN_CAMPUS_PREFIXES),
                        "bbox": [minx, miny, maxx, maxy]},
        "buildings": buildings,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print(f"寫入 {OUTPUT_PATH}：{len(buildings)} 棟建物，"
          f"主校區 {len(main_ids)} 棟")


if __name__ == "__main__":
    main()
