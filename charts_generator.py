import os
import re
import glob
import gc
from typing import List, Optional

import pandas as pd
from datetime import datetime
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm

import google.generativeai as genai

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(CURRENT_DIR, 'fonts', 'NotoSansTC-Regular.ttf')
if os.path.exists(FONT_PATH):
    # 強制加入字體到 Matplotlib 的字體管理器
    fm.fontManager.addfont(FONT_PATH)
    # 獲取該字體的正式名稱
    custom_font_name = fm.FontProperties(fname=FONT_PATH).get_name()
    # 設定為全域預設字體
    plt.rcParams['font.family'] = custom_font_name
    # 修正負號顯示問題
    plt.rcParams['axes.unicode_minus'] = False
    print(f"✅ 已成功載入字體: {custom_font_name}")
else:
    print(f"❌ 找不到字體檔: {FONT_PATH}")
    # Mac 備案：如果本地沒放字體，嘗試用 Mac 內建字體預覽 (但部署到 Render 會失效)
    plt.rcParams['font.family'] = 'Arial Unicode MS'

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
try:
    generation_config = {
    "temperature": 0,  # 設為 0 確保回答一致性
}
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash', generation_config=generation_config)
except Exception as e:
    # 如果 API key 未設定或連線失敗，則 model 為 None
    print(f"Gemini 配置失敗，RAG 功能將無法使用: {e}")
    model = None


REGION_MAPPING = {
    "高中大區": ["高中一區", "高中二區"],
    "青年大區": ["青年一區", "青年二區", "青年三區"], 
    "國中大區": ["國中一區", "國中二區"], 
}
NUMERIC_COLUMNS_CANDIDATES = ["主日", "禱告", "家出訪", "家受訪", "小排", "晨興", "福出訪"]
ATTENDANCE_COLS = ['主日', '禱告', '小排', '晨興']

# -----------------------------------------------------------
# RAG 核心函式
# -----------------------------------------------------------

def _load_recent_summary_data(reports_dir_summary: str, weeks: int = 5) -> Optional[pd.DataFrame]:
    """載入所有總結報表，並僅保留最近 N 週的數據。 (保持不變)"""
    try:
        df_all = aggregate_reports(reports_dir_summary)
        if df_all.empty: return None
        unique_dates = df_all["週末日"].dropna().unique()
        recent_dates = pd.Series(unique_dates).sort_values(ascending=False).head(weeks)
        df_recent = df_all[df_all["週末日"].isin(recent_dates)].copy()
        df_recent.sort_values("週末日", inplace=True)
        return df_recent
    except RuntimeError:
        return None


def _load_filtered_raw_personal_data(reports_dir_excel: str, weeks: int = 5) -> Optional[pd.DataFrame]:
    """
    載入個人原始數據，並過濾五週內完全沒出現的聖徒。
    """
    pattern = os.path.join(reports_dir_excel, "attend_*.xls*")
    file_paths = glob.glob(pattern)
    
    if not file_paths:
        print(f"DEBUG: 在 {reports_dir_excel} 找不到 attend_*.xlsx 檔案")
        return None
        
    # 建立檔案清單並排序
    file_info = []
    for f in file_paths:
        dt = parse_week_end_date_from_filename(f)
        if dt:
            file_info.append((dt, f))
    
    # 按日期由新到舊排序，取前 N 週
    file_info.sort(key=lambda x: x[0], reverse=True)
    recent_files = file_info[:weeks]
    
    if not recent_files:
        print("DEBUG: 找不到日期符合格式的 Excel 檔案")
        return None
        
    all_data = []
    attendance_cols = ['主日', '禱告', '小排', '晨興']
    
    for dt, file_path in recent_files:
        try:
            df = pd.read_excel(file_path)
            df.columns = [str(c).strip() for c in df.columns]
            
            if '姓名' not in df.columns: continue
            
            # 選取必要欄位並補零
            available_cols = ['姓名', '區別'] + [c for c in attendance_cols if c in df.columns]
            temp_df = df[available_cols].copy()
            
            for c in attendance_cols:
                if c in temp_df.columns:
                    temp_df[c] = pd.to_numeric(temp_df[c], errors='coerce').fillna(0).astype(int)
                else:
                    temp_df[c] = 0
            
            temp_df['日期'] = dt.strftime('%Y/%m/%d')
            all_data.append(temp_df)
        except Exception as e:
            print(f"讀取 {file_path} 出錯: {e}")
            continue

    if not all_data: return None

    df_total = pd.concat(all_data, ignore_index=True)

    # 🚨 過濾：只保留五週內至少有一次出席的人
    person_sum = df_total.groupby('姓名')[attendance_cols].transform('sum').sum(axis=1)
    df_filtered = df_total[person_sum > 0].copy()

    # 回傳整理後的流水帳，方便 Gemini 比對
    return df_filtered[['日期', '區別', '姓名', '主日', '禱告', '小排', '晨興']].sort_values(['日期', '區別'], ascending=[False, True])


def _generate_rag_context(reports_dir_summary: str, reports_dir_excel: str) -> str:
    """
    生成讓 Gemini 閱讀的知識庫內容。
    """
    df_summary = _load_recent_summary_data(reports_dir_summary, weeks=5)
    df_personal = _load_filtered_raw_personal_data(reports_dir_excel, weeks=5)
    
    context = ""
    
    if df_summary is not None:
        context += "### [1. 總結報表數據]\n"
        context += "這是各區別的彙總數據，適合回答整體趨勢問題。\n"
        context += df_summary.to_markdown(index=False) + "\n\n"
        
    if df_personal is not None:
        context += "### [2. 個人原始點名明細]\n"
        context += "這是每個人在每一週的出席狀況（1=出席, 0=缺席）。可用於跨週比對名單。\n"
        context += df_personal.to_markdown(index=False) + "\n"
    else:
        context += "### [⚠️ 注意]：目前無法讀取個人 Excel 資料，請檢查檔案名稱是否為 attend_YYYY-MM-DD.xlsx。\n"
        
    return context

GLOBAL_RAG_CONTEXT = "數據初始化中，請稍候..."

# ... (保留原有的字體設定、模型設定) ...

def update_global_rag_context(reports_dir_summary: str, reports_dir_excel: str):
    """
    手動觸發：重新讀取 Excel 並更新全局快取文字。
    """
    global GLOBAL_RAG_CONTEXT
    print("🔄 正在重新構建 RAG 知識庫快取...")
    try:
        # 呼叫您原有的 generate 函式取得文字
        new_context = _generate_rag_context(reports_dir_summary, reports_dir_excel)
        GLOBAL_RAG_CONTEXT = new_context
        print(f"✅ 知識庫快取更新完成 (字數: {len(GLOBAL_RAG_CONTEXT)})")
        gc.collect()
    except Exception as e:
        print(f"❌ 快取更新失敗: {e}")

# -----------------------------------------------------------
# 總 RAG 響應生成函式 (統一處理所有查詢)
# -----------------------------------------------------------
def generate_rag_response(reports_dir_summary: str, reports_dir_excel: str, query: str) -> str:
    """
    統一 RAG 函式：生成上下文並傳遞給 Gemini 進行推理。
    """
    if not model:
        return "❌ RAG 功能未啟用，請檢查 Gemini API Key 設定。"

    # 1. 獲取所有檔案濃縮成的核心數據上下文
    rag_context = GLOBAL_RAG_CONTEXT
    
    # 2. 準備系統提示
    system_prompt = f"""
    你是一個智慧的教會數據分析機器人。你的目標是根據用戶的問題和下方提供的『RAG 數據知識庫』來生成精確、簡潔且有條理的答案。
    
    數據欄位說明：
    - A 區塊用於回答總結趨勢和區別比較問題。
    - B 區塊是**原始的、未聚合的個人數據**，可用於回答**任何**個人相關問題，包括跨週比較（例如：上週有來這週沒來的人、某位聖徒在五週內的出席趨勢）。
    
    請利用提供的數據知識庫進行分析和回答。
    """
    
    # 3. 結合上下文和用戶查詢
    full_prompt = f"{system_prompt}\n\n{rag_context}\n\n---\n\n用戶問題：{query}"

    try:
        # 4. 呼叫 Gemini
        response = model.generate_content(full_prompt)
        gc.collect()
        return response.text
    except Exception as e:
        gc.collect()
        return f"❌ RAG 處理失敗 (Gemini API 錯誤): {e}"

def parse_week_end_date_from_filename(filename: str) -> Optional[datetime]:
    """
    從檔名提取日期。
    支援格式: attend_2025-12-08.xlsx 或 summary_2025-12-08.txt
    """
    base_name = os.path.basename(filename)
    # 匹配 YYYY-MM-DD 格式
    date_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", base_name)
    if date_match:
        try:
            return datetime.strptime(date_match.group(0), "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _clean_table_headers(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(col).strip() for col in df.columns]
    return df


def _coerce_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """強制轉換出席欄位為數字"""
    for column_name in NUMERIC_COLUMNS_CANDIDATES:
        if column_name in df.columns:
            df[column_name] = pd.to_numeric(df[column_name], errors="coerce").fillna(0)
    return df


def read_single_report(file_path: str) -> Optional[pd.DataFrame]:
    week_end_date = parse_week_end_date_from_filename(file_path)
    if week_end_date is None:
        print(f"⚠ 無法從檔名解析日期: {os.path.basename(file_path)}，已略過")
        return None

    try:
        dataframe = pd.read_excel(file_path, engine="openpyxl")
    except Exception as e:
        print(f"⚠ 無法讀取總結報表 {file_path}: {e}")
        return None

    if dataframe is None:
        print(f"⚠ 無法讀取報表: {file_path}")
        return None

    dataframe = _clean_table_headers(dataframe)
    
    if "區別" not in dataframe.columns:
        print(f"⚠ 總結報表缺少必要欄位 '區別': {file_path}，已略過")
        return None

    dataframe = _coerce_numeric_columns(dataframe)
    dataframe["週末日"] = week_end_date

    keep_columns = ["區別", "週末日"] + [
        col for col in NUMERIC_COLUMNS_CANDIDATES if col in dataframe.columns
    ]
    return dataframe[keep_columns]


def _is_summary_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return "總計" in value or "合計" in value


def _remove_summary_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "區別" not in df.columns:
        return df
    mask_summary = df["區別"].apply(_is_summary_text)
    return df[~mask_summary].copy()


def aggregate_reports(reports_dir: str) -> pd.DataFrame:
    pattern = os.path.join(reports_dir, "*.xls*") 
    file_paths = sorted(glob.glob(pattern))
    
    if not file_paths:
        raise RuntimeError(f"在資料夾 '{reports_dir}' 中找不到報表檔案。")

    combined: List[pd.DataFrame] = []
    processed_count = 0
    for path in file_paths:
        report_df = read_single_report(path)
        if report_df is not None:
            combined.append(report_df)
            processed_count += 1
            
    if not combined:
        raise RuntimeError("沒有任何可用的報表資料。")

    all_data = pd.concat(combined, ignore_index=True)

    all_data = _remove_summary_rows(all_data)
    all_data.sort_values("週末日", inplace=True)

    unique_weeks = all_data["週末日"].dropna().unique()
    print(f"📦 已讀取 {processed_count}/{len(file_paths)} 份總結報表；週數: {len(unique_weeks)} ({', '.join(pd.Series(unique_weeks).dt.strftime('%Y/%m/%d'))})")

    return all_data


def build_region_timeseries(all_reports: pd.DataFrame, region_name: str) -> pd.DataFrame:
    """
    根據名稱 (總計, 區別/小區, 或大區) 建立時間序列數據。
    """
    
    if region_name == "總計":
        region_df = all_reports.copy()
    
    # 🚨 修正: 處理大區名稱 (即在 REGION_MAPPING 中的 Key)
    elif region_name in REGION_MAPPING:
        subdistricts = REGION_MAPPING[region_name]
        # 過濾出屬於該大區的所有小區數據
        region_df = all_reports[all_reports["區別"].isin(subdistricts)].copy()
        print(f"   -> 匯總 {region_name}: 包含 {', '.join(subdistricts)}")
        
    # 處理小區名稱 (即在 '區別' 欄位中的值)
    else:
        region_df = all_reports[all_reports["區別"] == region_name].copy()
    
    
    if region_df.empty:
        return pd.DataFrame()

    aggregation_columns = [col for col in NUMERIC_COLUMNS_CANDIDATES if col in region_df.columns]
    
    # 執行分組加總 (如果是大區或總計，則會將多個小區的數據加總)
    ts = region_df.groupby("週末日")[aggregation_columns].sum().sort_index()

    # 計算總出訪 (使用 API 欄位名稱)
    gospel = ts["福出訪"] if "福出訪" in ts.columns else 0
    home = ts["家出訪"] if "家出訪" in ts.columns else 0
    ts["總出訪"] = gospel + home
        
    return ts


def _format_date_axis(ax, dates=None):
    if dates is not None:
        ax.set_xticks(pd.Index(dates))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y/%m/%d"))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=11)
    ax.tick_params(axis="y", labelsize=11)
    ax.margins(y=0.15)
    ax.grid(True, alpha=0.3)


def _annotate_series(ax, x_index: pd.Index, y_series: pd.Series, fontsize: int = 12):
    for x, y in zip(x_index, y_series):
        if y > 0:
            ax.annotate(
                f"{int(y)}",
                (x, y),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=fontsize,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8),
                zorder=3,
                clip_on=False,
            )

def _finalize_plot(plt_obj, output_path):
    """🚨 統一處理圖表輸出與記憶體清理"""
    plt_obj.tight_layout()
    # 🚨 降低 DPI 以減少記憶體佔用與檔案大小 (80-90 適合手機顯示)
    plt_obj.savefig(output_path, dpi=85) 
    plt_obj.clf()
    plt_obj.close('all')
    gc.collect() # 💡 強制垃圾回收

def plot_attendance(region_name: str, ts: pd.DataFrame, output_dir: str) -> None:
    # Only keep the last 5 weeks for plotting
    ts = ts.tail(5)
    if ts.empty or ts.sum().sum() == 0:
        return
        
    plt.figure(figsize=(10, 6))
    ax = plt.gca()

    # 繪圖時使用的欄位名稱，請注意這裡仍使用 API 原始欄位名
    columns_to_plot = [
        ("主日", "當周主日人數", "red", "-"),
        ("小排", "小排人數", "gold", "-"),
        ("晨興", "晨興人數", "green", "-"),
    ]

    plotted_any = False
    for column_key, label_text, color, linestyle in columns_to_plot:
        if column_key in ts.columns and ts[column_key].sum() > 0:
            ax.plot(ts.index, ts[column_key], label=label_text, color=color, linestyle=linestyle, marker="o", markersize=5, linewidth=2)
            _annotate_series(ax, ts.index, ts[column_key], fontsize=12)
            plotted_any = True

    if not plotted_any:
        print(f"⚠ {region_name} 沒有可繪製的出席相關數據")
        plt.close()
        return

    ax.set_title(f"{region_name} - 召會生活人數趨勢 (近五週)")
    ax.set_xlabel("日期")
    ax.set_ylabel("人數")
    ax.legend(loc="upper left")
    _format_date_axis(ax, dates=ts.index)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{region_name}_attendance.png")
    _finalize_plot(plt, output_path)
    print(f"✅ 已輸出 {output_path}")


def plot_burden(region_name: str, ts: pd.DataFrame, output_dir: str) -> None:
    # Only keep the last 5 weeks for plotting
    ts = ts.tail(5)
    if ts.empty or ts.sum().sum() == 0:
        return
        
    plt.figure(figsize=(10, 6))
    ax = plt.gca()

    plotted_any = False
    if "禱告" in ts.columns and ts["禱告"].sum() > 0:
        ax.plot(ts.index, ts["禱告"], label="禱告人數", color="#00aaff", marker="o", markersize=5, linewidth=2)
        _annotate_series(ax, ts.index, ts["禱告"], fontsize=12)
        plotted_any = True
        
    if "總出訪" in ts.columns and ts["總出訪"].sum() > 0: 
        ax.plot(ts.index, ts["總出訪"], label="總出訪人數", color="#0044aa", marker="o", markersize=5, linewidth=2)
        _annotate_series(ax, ts.index, ts["總出訪"], fontsize=12)
        plotted_any = True
        
    if "家受訪" in ts.columns and ts["家受訪"].sum() > 0: # 總結報表中的 '家受訪'
        ax.plot(ts.index, ts["家受訪"], label="家受訪人數", color="#66ccff", marker="o", markersize=5, linewidth=2)
        _annotate_series(ax, ts.index, ts["家受訪"], fontsize=12)
        plotted_any = True

    if not plotted_any:
        print(f"⚠ {region_name} 沒有可繪製的負擔相關數據")
        plt.close()
        return

    ax.set_title(f"{region_name} - 負擔領受程度趨勢 (近五週)")
    ax.set_xlabel("日期")
    ax.set_ylabel("人數")
    ax.legend(loc="upper left")
    _format_date_axis(ax, dates=ts.index)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{region_name}_burden.png")
    _finalize_plot(plt, output_path)
    print(f"✅ 已輸出 {output_path}")


def generate_region_charts(all_reports: pd.DataFrame, region_name: str, output_dir: str) -> None:
    """生成指定名稱 (總計, 大區, 或小區) 的圖表"""
    ts = build_region_timeseries(all_reports, region_name)
    if ts.empty:
        print(f"⚠ 找不到 {region_name} 的資料，無法繪圖")
        return
    
    plot_attendance(region_name, ts, output_dir)
    plot_burden(region_name, ts, output_dir)


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    reports_dir = os.path.join(base_dir, "reports_summary") 
    charts_output_dir = os.path.join(base_dir, "charts")

    try:
        df_reports = aggregate_reports(reports_dir)
        
        if "區別" not in df_reports.columns:
            raise RuntimeError("匯總後的資料缺少 '區別' 欄位，無法分區生成圖表。")

        # --- 1. 生成 '總計' 圖表 ---
        generate_region_charts(df_reports, "總計", charts_output_dir)
        
        # --- 2. 生成所有自定義的 '大區' 圖表 (如: 高中大區, 青年大區) ---
        print("\n--- 🌐 開始生成大區圖表 ---")
        for region_name in REGION_MAPPING.keys():
            generate_region_charts(df_reports, region_name, charts_output_dir)

        # --- 3. 生成所有 '小區' 圖表 (即 '區別' 欄位中的獨立名稱) ---
        print("\n--- 💠 開始生成小區圖表 ---")
        # 僅迭代那些沒有被包含在 REGION_MAPPING 中的獨立小區，或所有小區
        all_unique_districts = df_reports["區別"].dropna().unique()
        
        # 為了避免重複，我們可以選擇只生成未被歸類到大區的小區，或者生成所有小區
        # 這裡選擇生成所有的小區圖表 (即使它被歸類到大區)，以提供最細節的視圖
        for subdistrict in sorted(all_unique_districts):
             # 排除總計列 (如果之前沒被移除的話)
            if not _is_summary_text(subdistrict):
                generate_region_charts(df_reports, str(subdistrict), charts_output_dir)
            
    except RuntimeError as e:
        print(f"❌ 執行圖表生成失敗: {e}")