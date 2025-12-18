import os
import sys
import gc
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
import urllib.parse

# 導入您的腳本
from charts_generator import (
    aggregate_reports, generate_region_charts, 
    generate_rag_response, REGION_MAPPING
)
import app as church_api  # 導入您的 app.py (自動抓取程式)

app = Flask(__name__)

# --- 配置 ---
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 路徑設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR_SUMMARY = os.path.join(BASE_DIR, "reports_summary")
REPORTS_DIR_EXCEL = os.path.join(BASE_DIR, "reports_excel")
CHARTS_OUTPUT_DIR = os.path.join(BASE_DIR, "charts")

# --- 🚨 0 元圖片方案：開放 /tmp 存取路由 ---
@app.route('/charts/<filename>')
def serve_charts(filename):
    # 這讓 LINE 可以透過 https://您的網址/static/charts/xxx.png 抓到圖
    return send_from_directory(CHARTS_OUTPUT_DIR, filename)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    trigger_keyword = "81人數助理"
    
    if trigger_keyword not in msg:
        return 

    user_query = msg.replace(trigger_keyword, "").strip()
    
    # 建立一個列表來存儲所有要發送的訊息
    reply_msgs = []

    # 取得基礎網址，並加上安全檢查
    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not base_url:
        # 如果變數沒抓到，暫時手動寫入作為備援方案
        base_url = "https://church-assistant-zad7.onrender.com"
    
    # 移除網址末尾可能存在的斜槓，避免出現 // 的情況
    base_url = base_url.rstrip('/')

    # 1. 更新數據
    if user_query == "更新數據":
        try:
            church_api.main()
            reply_msgs.append(TextSendMessage(text="✅ 數據更新完成！"))
        except Exception as e:
            reply_msgs.append(TextSendMessage(text=f"❌ 更新失敗: {e}"))

    # 2. 測試圖片 (修正網址路徑與發送邏輯)
    elif user_query == "測試圖片":
        filename = "高中大區_attendance.png"
        safe_filename = urllib.parse.quote(filename)
        img_url = f"{base_url}/charts/{safe_filename}"
        
        print(f"DEBUG: 發送圖片網址 -> {img_url}")
        reply_msgs.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))

    # 3. 生成報表
    elif user_query in ["生成報表", "報表"]:
        try:
            os.makedirs(CHARTS_OUTPUT_DIR, exist_ok=True)
            df_reports = aggregate_reports(REPORTS_DIR_SUMMARY)
            
            # 先加入提示文字
            reply_msgs.append(TextSendMessage(text="📊 報表產製中，請點擊圖片查看細節："))
            
            for region_name in REGION_MAPPING.keys():
                generate_region_charts(df_reports, region_name, CHARTS_OUTPUT_DIR)
                filename = f"{region_name}_attendance.png"
                
                # 再次確保路徑正確
                img_url = f"{base_url}/charts/{filename}"
                
                if len(reply_msgs) < 5: # LINE 限制一次最多 5 則訊息
                    reply_msgs.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))

            gc.collect()
        except Exception as e:
            reply_msgs.append(TextSendMessage(text=f"❌ 產圖失敗: {e}"))

    # 4. Gemini 查詢
    elif any(word in user_query for word in ["請問", "查詢", "誰", "哪", "人數"]):
        try:
            res = generate_rag_response(REPORTS_DIR_SUMMARY, REPORTS_DIR_EXCEL, user_query)
            reply_msgs.append(TextSendMessage(text=res))
        except Exception as e:
            reply_msgs.append(TextSendMessage(text=f"❌ 分析失敗: {e}"))

    # --- 關鍵修正：最後一次性發送所有訊息，只呼叫一次 reply_message ---
    if reply_msgs:
        try:
            line_bot_api.reply_message(event.reply_token, reply_msgs)
        except Exception as e:
            print(f"❌ LINE API 發送失敗: {e}")

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)