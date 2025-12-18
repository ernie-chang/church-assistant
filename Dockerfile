FROM python:3.10-slim

# 安裝系統必備組件與中文字體
RUN apt-get update && apt-get install -y fonts-noto-cjk && fc-cache -fv

WORKDIR /app

# 複製依賴並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製程式碼
COPY . .

# 預設 Port 10000 是 Render 的慣例，但我們會讀取環境變數
EXPOSE 10000

# 使用 gunicorn 啟動 (更穩定)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "bot_server:app"]