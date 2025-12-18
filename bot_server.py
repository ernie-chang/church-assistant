import os
import sys
import gc
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage

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
@app.route('/static/charts/<filename>')
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
    print(f"收到訊息: {msg}")
    user_id = event.source.user_id
    trigger_keyword = "81人數助理"
    if trigger_keyword not in msg:
        return # 如果訊息沒提到關鍵字，直接結束，不回覆

    # 關鍵修正 2：確保回應在群組（使用 reply_token）
    # 去除關鍵字後再進行分析，這樣 Gemini 才不會被關鍵字干擾
    user_query = msg.replace(trigger_keyword, "").strip()

    # 指令 1：更新數據 (執行您的 app.py 邏輯)
    if user_query == "更新數據":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⏳ 正在連線至教會系統抓取最新點名表..."))
        try:
            church_api.main() # 執行您上傳的 app.py 中的 main()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 數據更新完成！"))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 更新失敗: {e}"))
    elif user_query == "測試圖片":
        base_url = os.environ.get("RENDER_EXTERNAL_URL")
        filename = "高中大區_attendance.png"
        img_url = f"{base_url}/static/charts/{filename}"
        line_bot_api.reply_message(event.reply_token, ImageSendMessage(img_url, img_url))
    # 指令 2：生成報表
    elif user_query in ["生成報表", "報表"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📊 正在生成視覺化圖表..."))
        try:
            os.makedirs(CHARTS_OUTPUT_DIR, exist_ok=True)
            df_reports = aggregate_reports(REPORTS_DIR_SUMMARY)
            
            # 取得 Render 的公網網址 (需手動設定或自動抓取)
            # Render 會把網址存在環境變數，若無則手動在 Render 設定 RENDER_EXTERNAL_URL
            base_url = os.environ.get("RENDER_EXTERNAL_URL") 
            
            for region_name in REGION_MAPPING.keys():
                print(f"生成 {region_name} 的圖表...")
                generate_region_charts(df_reports, region_name, CHARTS_OUTPUT_DIR)
                filename = f"{region_name}_attendance.png"
                img_path = os.path.join(CHARTS_OUTPUT_DIR, filename)
                
                if os.path.exists(img_path):
                    # 組合出 LINE 抓得到圖片的 URL
                    img_url = f"{base_url}/static/charts/{filename}"
                    line_bot_api.reply_message(event.reply_token, ImageSendMessage(img_url, img_url))

            gc.collect()
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 產圖失敗: {e}"))

    # 指令 3：Gemini 查詢
    elif any(word in user_query for word in ["請問", "查詢", "誰", "哪"]):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🔍 正在分析數據..."))
        res = generate_rag_response(REPORTS_DIR_SUMMARY, REPORTS_DIR_EXCEL, user_query)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=res))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)