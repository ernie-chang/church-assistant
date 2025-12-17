import requests
import pandas as pd
from datetime import datetime, timedelta
import os
import json
import google.generativeai as genai

# --- 1. 配置區 ---
CHURCH_ID = 2523 
ACCOUNT = "h81s2"
PASSWORD = "h81"
ORG_LEVEL = "2-2994,2-2993,2-2995" 
DATA_FOLDER_EXCEL = "reports_excel"      # 格式化報表 (Excel)
DATA_FOLDER_SUMMARY_EXCEL = "reports_summary"

# API 端點
BASE_URL = "https://backend.chlife-stat.org"
LOGIN_URL = f"{BASE_URL}/api/login"
DATA_URL = f"{BASE_URL}/api/church/member"

# Gemini 配置
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 設置 Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') 

# --- 欄位對應與輸出格式定義 (最終確認修正) ---
ATTEND_MAP = {
    # 🚨 關鍵修正: 假設您所需的小區名稱在 API 的 lv3_name 中
    'member_name': '姓名',
    'sex': '性別',
    'lv3_name': '區別',     # 小區名稱 (高中一區/高中二區)，即 Excel 報表所需
    'lv4_name': '小排_API', # 將 lv4_name 視為更小的層級，不進入 RAG 統計，但保留給未來可能使用
    'attend0': '主日',
    'attend1': '禱告',
    'attend2': '家出訪',
    'attend3': '家受訪',
    'attend4': '小排',
    'attend5': '晨興',
    'attend6': '福出訪'
}

# 定義 Excel 輸出時的欄位順序 (嚴格依照您的範本)
EXCEL_COLUMNS_ORDER = [
    '姓名', '性別', '區別', '主日', '禱告', '家出訪', '家受訪', '小排', '晨興', '福出訪'
]

# --- 2. 工具函式 ---

def get_last_week_info():
    """計算並返回前一週的年份、週次和該週的開始日期 (YYYY-MM-DD 格式)。"""
    today = datetime.now().date()
    last_week_date = today - timedelta(weeks=1)
    year, week, _ = last_week_date.isocalendar()
    start_of_week = last_week_date - timedelta(days=last_week_date.isoweekday() - 1)
    return year, week, start_of_week.strftime("%Y-%m-%d")

def get_auth_token():
    """執行登入並獲取 JWT Token。"""
    print("嘗試登入...")
    login_payload = {"church_id": CHURCH_ID, "account": ACCOUNT, "pwd": PASSWORD}
    try:
        response = requests.post(LOGIN_URL, json=login_payload)
        response.raise_for_status()
        data = response.json()
        token = data['data']['token']
        print("登入成功，已取得 Token。")
        return token
    except requests.exceptions.RequestException as e:
        print(f"登入失敗，請檢查帳密或網路：{e}")
        return None

def format_dataframe_for_output(df):
    """
    將原始 DataFrame 格式化。確保 'lv3_name' 成為最終的 '區別' 欄位。
    """
    df_formatted = df.copy()
    
    # 1. 確保所有 attendX 欄位都存在，如果不存在則補 0
    api_attend_cols = [k for k in ATTEND_MAP.keys() if k.startswith('attend')]
    for col in api_attend_cols:
        if col not in df_formatted.columns:
            df_formatted[col] = 0

    # 2. 數據清洗：填補空值並轉為整數
    df_formatted[api_attend_cols] = df_formatted[api_attend_cols].fillna(0).astype(int)
    
    # 3. 重新命名欄位
    df_formatted = df_formatted.rename(columns=ATTEND_MAP)

    # 4. 僅選擇 EXCEL_COLUMNS_ORDER 中定義的欄位，並確保包含 RAG 專用的欄位
    
    # RAG/統計 專用欄位，這次使用 '區別' 作為分組依據，所以不再需要額外的 '大區_API' 欄位
    
    # 組合最終的 DataFrame
    final_cols = [col for col in EXCEL_COLUMNS_ORDER if col in df_formatted.columns]

    # 返回包含所有必要欄位的 DataFrame (僅包含 Excel 報表欄位)
    return df_formatted[final_cols]


def fetch_weekly_data(token, year, week, week_start_date_str):
    """使用 Token 抓取數據，格式化並存檔為 Excel。"""
    
    params = {
        "level": ORG_LEVEL, "meeting": "", "year": year, "week": week,
        "limit": 5000, "page": 1, "memberId": "", "memberName": "",
        "sex": "", "role": "", "filter_mode": "churchStructureTab",
        "lastWeekCopy": 0, "timeChange": True
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    print(f"嘗試抓取 {year} 年 第 {week} 週的數據...")
    try:
        response = requests.get(DATA_URL, headers=headers, params=params)
        response.raise_for_status()
        
        json_data = response.json()
        members_list = json_data.get('data', {}).get('members', [])
        
        if members_list:
            df_raw = pd.DataFrame(members_list)
            
            # 💡 格式化數據
            df_formatted = format_dataframe_for_output(df_raw)
            
            # --- 存檔操作: 存為格式化 Excel (reports_excel) ---
            filename_excel = f"attend_{week_start_date_str}.xlsx"
            os.makedirs(DATA_FOLDER_EXCEL, exist_ok=True)
            filepath_excel = os.path.join(DATA_FOLDER_EXCEL, filename_excel)
            df_formatted.to_excel(filepath_excel, index=False)
            print(f"✅ 格式化報表已存檔 (Excel): {filepath_excel}")

            # 返回格式化後的 DataFrame，供後續分析使用 (這次不需額外的 '大區_API' 欄位)
            return json_data, df_formatted
        
        return json_data, pd.DataFrame() # 數據為空時

    except requests.exceptions.RequestException as e:
        print(f"數據抓取失敗：{e}")
        return None, pd.DataFrame()
    except Exception as e:
        print(f"存檔過程中發生錯誤: {e}")
        # 為了分析，盡量返回數據
        try:
            df_raw = pd.DataFrame(json_data.get('data', {}).get('members', []))
            return json_data, format_dataframe_for_output(df_raw)
        except:
            return json_data, pd.DataFrame()


def analyze_church_data(df_formatted, week_start_date):
    """
    根據 '區別' (小區名稱) 生成統計報表。
    """
    if df_formatted.empty:
        return "⚠️ 本週尚未有數據或抓取失敗。", pd.DataFrame() 
    
    # 🚨 關鍵: 直接使用 '區別' (小區名稱) 進行分組統計和 RAG
    grouping_col = '區別' 
    attend_cols = [v for k, v in ATTEND_MAP.items() if k.startswith('attend')]
    
    summary_df = df_formatted.groupby(grouping_col)[attend_cols].sum()
    total_row = summary_df.sum().to_frame().T
    total_row.index = ['總計']
    summary_df = pd.concat([summary_df, total_row])

    try:
        filename_summary = f"summary_{week_start_date}.xlsx"
        os.makedirs(DATA_FOLDER_SUMMARY_EXCEL, exist_ok=True)
        filepath_summary = os.path.join(DATA_FOLDER_SUMMARY_EXCEL, filename_summary)
        
        # 將 '區別' 變成一個欄位，而不是 Index (方便其他腳本讀取)
        summary_df_output = summary_df.reset_index().rename(columns={'index': grouping_col})

        summary_df_output.to_excel(filepath_summary, index=False)
        print(f"✅ 人數統計報表已存檔 (Excel): {filepath_summary}")
        
    except Exception as e:
        print(f"❌ 儲存統計總結報表失敗: {e}")

    report = []
    report.append(f"📊 **本週教會人數統計報表 (按小區 - {grouping_col} 分組)**")
    report.append("="*30)
    report.append(summary_df.to_markdown())
    report.append("\n")

    return "\n".join(report), df_formatted # 回傳 df 供 RAG 函式使用

def generate_rag_report(df, week_start_date):
    """使用 Gemini 分析數據並生成報告。"""
    
    # RAG 需要的欄位
    required_cols = ['姓名', '區別', '主日', '禱告', '小排'] 
    df_for_rag = df[[col for col in required_cols if col in df.columns]]

    # 找出所有聚會（主日、禱告、小排）皆缺席的聖徒
    absent_mask = (df_for_rag['主日'] == 0) & (df_for_rag['禱告'] == 0) & (df_for_rag['小排'] == 0)
    absent_members = df_for_rag[absent_mask][['姓名', '區別']]
    
    # 格式化缺席名單
    absent_list = absent_members.apply(lambda row: f"  - {row['區別']}：{row['姓名']}", axis=1).tolist()
    absent_section = ""
    if absent_list:
        absent_section = f"以下聖徒本週所有主要聚會皆缺席 ({len(absent_list)}人)，請務必關心：\n" + "\n".join(absent_list)
    
    # 將 DataFrame 轉換為 Markdown 表格
    data_markdown = df_for_rag.to_markdown(index=False)
    
    # RAG Prompt 
    system_prompt = f"""
    你是一個細心、熱情的教會數據機器人。你的任務是分析提供的教會點名數據，並生成一份溫暖、易懂的報告。

    - 數據截止日期為 {week_start_date} 開始的一週。
    - 點名數值 1 代表出席，0 代表缺席。'區別' 是小區名稱 (高中一區/高中二區)，這是主要分組名稱。

    請根據提供的數據，完成以下任務：
    1. 統計「主日」、「禱告」、「小排」的總出席人數。
    2. 分析各項聚會的最高出席率「區別」。
    3. {absent_section}
    4. 根據分析結果，生成一份**摘要報告**，並在最後提出一個溫和的「關心建議」。
    """
    
    user_content = f"本週點名數據如下：\n\n{data_markdown}"
    
    try:
        response = model.generate_content([system_prompt, user_content])
        return response.text
    except Exception as e:
        return f"Gemini 報告生成失敗: {e}"

# --- 3. 主執行邏輯 ---
def main():
    # 獲取 Token
    token = get_auth_token()
    if not token:
        return 

    # 獲取日期
    year, week, week_start_date = get_last_week_info() 
    print(f"本週報告區間：{week_start_date}（{year} 年 第 {week} 週）")

    # 抓取數據並自動存檔 Excel
    json_data, df_formatted = fetch_weekly_data(token, year, week, week_start_date)

    if json_data is None:
        return

    # 進行 RAG 分析並生成報告
    report_text, df_summary = analyze_church_data(df_formatted, week_start_date)
    
    # 輸出最終報告（先輸出統計表格）
    print("\n--- 💻 自動生成報告 (統計表格) ---")
    print(report_text)
    
    # 使用格式化後的 df 進行 RAG 報告生成
    # if not df_for_rag.empty:
    #     rag_report = generate_rag_report(df_for_rag, week_start_date)
    #     print("\n--- 🤖 Gemini RAG 分析報告 ---")
    #     print(rag_report)
    # else:
    #     print("無法生成 RAG 報告，因為數據為空。")
        
    print("--- 報告結束 ---")
    
if __name__ == "__main__":
    main()