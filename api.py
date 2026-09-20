"""NCKU Smart Commute 集中設定模組。

所有設定一律從環境變數讀取；本檔案不含、也不得寫入任何金鑰。
沿用 CampusPulse repo 的設計：PROVIDER_MODE=fixture 時不需任何外部金鑰即可執行。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_PROVIDER_MODES = ("fixture", "live")

# 每個功能在 live 模式需要哪些環境變數（只列名稱，永不回傳值）
FEATURE_CREDENTIALS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY",),
    "ncku": (),  # 成大 GIS 與停車系統為公開端點，不需金鑰
    "weather": ("CWA_API_KEY",),
    "transit": ("TDX_CLIENT_ID", "TDX_CLIENT_SECRET"),
    # 路況事件也是 TDX 平台的 API，共用同一組 client_id/secret
    "traffic": ("TDX_CLIENT_ID", "TDX_CLIENT_SECRET"),
    "maps": ("GOOGLE_MAPS_API_KEY",),
    "local_llm": (),  # 本機 Ollama，不需金鑰；沒起 server 就自動略過
    "push": ("NTFY_TOPIC",),  # 手機推播；topic 名稱等於收件位址，當秘密保管
}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # 執行環境
    app_env: str
    timezone: str
    provider_mode: str
    http_timeout_seconds: float

    # Gemini（model 名稱不是秘密）
    gemini_model: str

    # 成大公開端點
    ncku_gis_base_url: str
    ncku_parking_base_url: str

    # 課表與預設起點
    class_schedule_path: str
    # 時間來源："auto"（免費的先試，答不出來才用 Google）、"estimate" 或 "google"
    travel_time_provider: str
    # 使用者的通勤偏好，一段自然語言，交給 Gemini 權衡各方案時參考
    commute_preference: str
    # 本機 Ollama（跑 Gemma）。個資類文字（課表地點、教授信）優先在本機處理，
    # 沒起 server 時自動退回雲端 Gemini
    ollama_url: str
    local_llm_model: str
    # 手機推播（ntfy）。server 可自架；topic 知道就能收也能發，所以 repr=False
    ntfy_server: str
    ntfy_topic: str = field(default="", repr=False)
    # 住家地址算個資，預設留空，實際值放在 .gitignore 擋掉的 .env
    default_origin: str = field(default="", repr=False)

    # 金鑰：repr=False 確保 print(settings) 或 log 不會洩漏
    gemini_api_key: str = field(default="", repr=False)
    cwa_api_key: str = field(default="", repr=False)
    tdx_client_id: str = field(default="", repr=False)
    tdx_client_secret: str = field(default="", repr=False)
    google_maps_api_key: str = field(default="", repr=False)


def load_settings(load_env_file: bool = True) -> Settings:
    """讀取環境變數並回傳設定。測試時傳入 load_env_file=False 以免讀到開發者的 .env。"""
    if load_env_file:
        from dotenv import load_dotenv

        # Deployment and test environments must be able to override local .env.
        load_dotenv(override=False)

    mode = _env("PROVIDER_MODE", "fixture").lower()
    if mode not in VALID_PROVIDER_MODES:
        raise ValueError(f"PROVIDER_MODE 必須是 {VALID_PROVIDER_MODES} 之一，目前為 {mode!r}")

    return Settings(
        app_env=_env("APP_ENV", "development"),
        timezone=_env("TIMEZONE", "Asia/Taipei"),
        provider_mode=mode,
        http_timeout_seconds=float(_env("HTTP_TIMEOUT_SECONDS", "8")),
        gemini_model=_env("GEMINI_MODEL", "gemini-3-flash-preview"),
        ncku_gis_base_url=_env("NCKU_GIS_BASE_URL", "https://db.nckumap.ncku.edu.tw/nckugis/public"),
        ncku_parking_base_url=_env("NCKU_PARKING_BASE_URL", "https://apss.oga.ncku.edu.tw/park/index.php/park11215"),
        class_schedule_path=_env("CLASS_SCHEDULE_PATH", "data/class_schedule.json"),
        travel_time_provider=_env("TRAVEL_TIME_PROVIDER", "auto").lower(),
        commute_preference=_env("COMMUTE_PREFERENCE"),
        ollama_url=_env("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        local_llm_model=_env("LOCAL_LLM_MODEL", "gemma3:4b"),
        ntfy_server=_env("NTFY_SERVER", "https://ntfy.sh").rstrip("/"),
        ntfy_topic=_env("NTFY_TOPIC"),
        default_origin=_env("DEFAULT_ORIGIN"),
        # 與 google-genai SDK 一致：GOOGLE_API_KEY 優先，其次 GEMINI_API_KEY
        gemini_api_key=_env("GOOGLE_API_KEY") or _env("GEMINI_API_KEY"),
        cwa_api_key=_env("CWA_API_KEY"),
        tdx_client_id=_env("TDX_CLIENT_ID"),
        tdx_client_secret=_env("TDX_CLIENT_SECRET"),
        google_maps_api_key=_env("GOOGLE_MAPS_API_KEY"),
    )


_VALUE_OF = {
    "GEMINI_API_KEY": lambda s: s.gemini_api_key,
    "CWA_API_KEY": lambda s: s.cwa_api_key,
    "TDX_CLIENT_ID": lambda s: s.tdx_client_id,
    "TDX_CLIENT_SECRET": lambda s: s.tdx_client_secret,
    "GOOGLE_MAPS_API_KEY": lambda s: s.google_maps_api_key,
    "NTFY_TOPIC": lambda s: s.ntfy_topic,
}


def missing_credentials(settings: Settings, features: list[str]) -> list[str]:
    """回傳指定功能缺少的環境變數「名稱」，絕不回傳值。未知功能名稱會丟出 KeyError。"""
    missing: list[str] = []
    for feature in features:
        for name in FEATURE_CREDENTIALS[feature]:
            if not _VALUE_OF[name](settings) and name not in missing:
                missing.append(name)
    return missing
