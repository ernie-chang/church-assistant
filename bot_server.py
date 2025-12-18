import os
import sys
import re
import gc
import csv
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
import urllib.parse
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

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
USER_LOG_FILE = os.path.join(BASE_DIR, "users_log.csv")

GROUP_CHART_CONFIG = {
    "C1234567890abcdef...": ["高中一區", "高中二區"],
    "C0987654321fedcb...": ["高中大區", "全教會總計"],
}


def log_user_info(event):
    """將發送訊息的使用者 ID 與名稱存入 CSV"""
    user_id = event.source.user_id
    display_name = "未知使用者"
    
    try:
        # 嘗試取得使用者名稱 (需機器人為好友或在同一群組)
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception:
        pass

    file_exists = os.path.isfile(USER_LOG_FILE)
    with open(USER_LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Timestamp', 'User_ID', 'Display_Name']) # 建立標頭
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id, display_name])

# def auto_update_and_push():
#     """每週一早上10點自動執行"""
#     print("⏰ 啟動每週自動更新任務...")
#     try:
#         # A. 更新數據 (自動抓取上週)
#         church_api.main() 
        
#         # B. 產製圖表與發送
#         df_reports = aggregate_reports(REPORTS_DIR_SUMMARY)
#         base_url = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com").rstrip('/')
        
#         for group_id, regions in GROUP_CHART_CONFIG.items():
#             push_msgs = [TextSendMessage(text="🔔 每週一自動數據更新完成！")]
            
#             for region in regions:
#                 generate_region_charts(df_reports, region, CHARTS_OUTPUT_DIR)
#                 safe_filename = urllib.parse.quote(f"{region}_attendance.png")
#                 img_url = f"{base_url}/charts/{safe_filename}"
#                 push_msgs.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            
#             # 發送到指定群組 (LINE 限制一條 push 最多 5 個訊息物件)
#             line_bot_api.push_message(group_id, push_msgs[:5])
#             print(f"✅ 已推送至群組: {group_id}")

#     except Exception as e:
#         print(f"❌ 自動更新失敗: {e}")

# # 設定排程：每週一 (mon) 10:00 執行
# scheduler = BackgroundScheduler(timezone="Asia/Taipei")
# scheduler.add_job(func=auto_update_and_push, trigger="cron", day_of_week="mon", hour=10, minute=0)
# scheduler.start()

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
    if "更新數據" in user_query:
        # 使用正則表達式抓取 YYYY-MM-DD
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", user_query)
        target_date = date_match.group(0) if date_match else None
        
        try:
            display_text = f"（日期：{target_date}）" if target_date else None
            # 呼叫 app.py 的 main 並帶入日期
            church_api.main(target_date=target_date)
            reply_msgs.append(TextSendMessage(text=f"✅ 數據更新完成！{display_text}"))
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
                safe_filename = urllib.parse.quote(filename)
                img_url = f"{base_url}/charts/{safe_filename}"
                
                if len(reply_msgs) < 5: # LINE 限制一次最多 5 則訊息
                    reply_msgs.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))

            gc.collect()
        except Exception as e:
            reply_msgs.append(TextSendMessage(text=f"❌ 產圖失敗: {e}"))

    # 4. Gemini 查詢
    else:
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