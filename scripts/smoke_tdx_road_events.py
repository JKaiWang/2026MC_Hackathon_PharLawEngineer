"""在自己電腦上用 live 模式打真實的 TDX 路況事件 API，順便驗證 parse_road_events 的欄位假設。

需要先在根目錄 .env 填好 TDX_CLIENT_ID、TDX_CLIENT_SECRET（免費，
https://tdx.transportdata.tw 會員中心申請）。

用法：
    python scripts/smoke_tdx_road_events.py                      # 用成大周邊座標查
    python scripts/smoke_tdx_road_events.py --lat 23.0 --lon 120.2 --radius 1000
    python scripts/smoke_tdx_road_events.py --record             # 同時把原始回應存成 fixture

如果印出 SchemaError，代表 road_events.py 裡猜的欄位命名跟真實回應不一樣；
錯誤訊息會列出真實欄位名稱，照著改 _ID_KEYS/_TYPE_KEYS/_ROAD_NAME_KEYS/
_TIME_KEYS/_FLAT_LON_KEYS/_FLAT_LAT_KEYS 就好。
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROVIDER_MODE"] = "live"
# Windows 主控台預設可能不是 UTF-8（例如 cp950），輸出重導向到檔案或非
# UTF-8 終端機時，中文會被轉成亂碼甚至寫出無效位元組；強制輸出用 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests  # noqa: E402

from api import load_settings, missing_credentials  # noqa: E402
from commute_agent.tools import road_events  # noqa: E402

# 成大成功校區附近，跟 fixtures/ncku_traffic/Tainan.json 的示範資料同一區域
NCKU_LAT, NCKU_LON = 22.9997, 120.2220


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="Tainan")
    parser.add_argument("--lat", type=float, default=NCKU_LAT)
    parser.add_argument("--lon", type=float, default=NCKU_LON)
    parser.add_argument("--radius", type=float, default=1000.0)
    parser.add_argument("--record", action="store_true", help="把原始回應存成 fixture")
    args = parser.parse_args()

    settings = load_settings()
    missing = missing_credentials(settings, ["traffic"])
    if missing:
        print(f"缺少環境變數：{missing}，先在根目錄 .env 填好再跑。")
        sys.exit(1)

    if args.record:
        token = road_events.get_access_token(settings.tdx_client_id, settings.tdx_client_secret,
                                             settings.http_timeout_seconds)
        endpoint = road_events.TDX_BASE_URL + road_events.ROAD_EVENT_PATH.format(city=args.city)
        resp = requests.get(endpoint, params={"$format": "JSON"}, timeout=settings.http_timeout_seconds,
                            headers={"User-Agent": road_events.USER_AGENT, "Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        raw = resp.json()
        path = Path(__file__).resolve().parent.parent / "commute_agent" / "fixtures" / "ncku_traffic" / f"{args.city}.json"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已錄製 {path}（記得把 fixtures/ncku_traffic/README.md 的說明改成「真實錄製」並補日期）")
        print(f"原始回應筆數：{len(raw) if isinstance(raw, list) else '（不是陣列，見上面存的檔案）'}")

    result = road_events.get_road_events(args.city, args.lat, args.lon, args.radius)
    print(f"[{result['status']}] {args.city} 座標 ({args.lat}, {args.lon}) 半徑 {args.radius:g}m"
          f"：{result['count']} 筆事件")
    if result["status"] == "error":
        print(f"   錯誤：{result['error_message']}")
        sys.exit(1)
    for e in result["events"]:
        label = e["description"] or (f"代碼 {e['type_code']}（無文字說明，先別轉譯成分類名稱）"
                                      if e["type_is_code"] else "（無說明文字）")
        tag = f"[{e['category']}] " if e["category"] else ""
        print(f"   {e['distance_m']}m｜{e['road_name'] or '（無地點資訊）'}｜{tag}{label}")


if __name__ == "__main__":
    main()
