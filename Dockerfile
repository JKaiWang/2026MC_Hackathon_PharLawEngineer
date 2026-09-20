FROM python:3.13-slim

WORKDIR /app

# HTTPS 呼叫成大 GIS、氣象署、TDX、Gemini 等外部 API 需要根憑證
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/user_schedule.json 是使用者個資，.dockerignore 已擋掉，
# 沒有這個檔時網站會顯示「先上傳你的課表」畫面，屬正常起始狀態。

ENV PROVIDER_MODE=live
EXPOSE 8080

# Cloud Run 用 $PORT 環境變數指定實際監聽埠，本機 docker run 未設定時預設 8080；
# shell form 讓 ${PORT} 在容器啟動時展開。
CMD exec uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8080}
