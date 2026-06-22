import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime
import pytz
import gspread
from google.oauth2.service_account import Credentials

# =========================
# 1. Google Sheets 連線
# =========================
def init_connection():
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    return client.open("ITZY_Motto_Artroom")

try:
    gc = init_connection()
except Exception as e:
    st.error(f"雲端連線失敗: {e}")
    gc = None

# =========================
# 2. 頁面設定
# =========================
st.set_page_config(page_title="ITZY 1:1 畫室活動監控", layout="wide")
st.title("🎨 ITZY 1:1 畫室活動 in Taipei")

API_URL = "https://www.kmonstar.com.tw/products/%E6%87%89%E5%8B%9F-260726-itzy-motto-11-%E7%95%AB%E5%AE%A4%E6%B4%BB%E5%8B%95-in-taipei.json"

TARGET_MEMBERS = [
    "예지 YEJI",
    "리아 LIA",
    "류진 RYUJIN",
    "채령 CHAERYEONG",
    "유나 YUNA"
]

LOG_COLUMNS = ["時間", "張數", "來源", "總銷售量"]

# =========================
# 3. 初始化 session_state
# =========================
if "member_logs" not in st.session_state:
    st.session_state.member_logs = {}

if "last_totals" not in st.session_state:
    st.session_state.last_totals = {}

if "bootstrapped" not in st.session_state:
    st.session_state.bootstrapped = False

# =========================
# 4. Google Sheet 同步
# =========================
def ensure_worksheet(name):
    if not gc:
        return None

    try:
        return gc.worksheet(name)
    except:
        try:
            wks = gc.add_worksheet(title=name, rows=1000, cols=10)
            wks.append_row(LOG_COLUMNS)
            return wks
        except Exception as e:
            st.sidebar.error(f"建立工作表 {name} 失敗: {e}")
            return None

def sync_from_cloud(names):
    if not gc:
        for name in names:
            if name not in st.session_state.member_logs:
                st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)
        return

    for name in names:
        if name not in st.session_state.member_logs or st.session_state.member_logs[name].empty:
            try:
                wks = ensure_worksheet(name)
                if wks is None:
                    st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)
                    continue

                values = wks.get_all_values()

                if not values:
                    st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)
                    continue

                if values[0] != LOG_COLUMNS:
                    wks.clear()
                    wks.append_row(LOG_COLUMNS)
                    st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)
                    continue

                if len(values) == 1:
                    st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)
                    continue

                df = pd.DataFrame(values[1:], columns=values[0])
                df["張數"] = pd.to_numeric(df["張數"], errors="coerce").fillna(0).astype(int)
                df["總銷售量"] = pd.to_numeric(df["總銷售量"], errors="coerce").fillna(0).astype(int)

                df = df.iloc[::-1].reset_index(drop=True)
                st.session_state.member_logs[name] = df

            except Exception as e:
                st.sidebar.error(f"同步 {name} 失敗: {e}")
                st.session_state.member_logs[name] = pd.DataFrame(columns=LOG_COLUMNS)

# =========================
# 5. API 抓取
# =========================
def get_itzy_data(session):
    result = {}

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.kmonstar.com.tw/"
    }

    try:
        res = session.get(
            f"{API_URL}?t={int(time.time())}",
            headers=headers,
            timeout=10
        )
        res.raise_for_status()
        data = res.json()

        for v in data.get("variants", []):
            name = (v.get("option1") or "").strip()

            if name in TARGET_MEMBERS:
                inventory_qty = v.get("inventory_quantity", 0)

                # KMONSTAR 這次是：
                # 0  = 尚未賣出
                # -1 = 賣出 1 張
                # -20 = 賣出 20 張
                sold = max(0, -int(inventory_qty))

                result[name] = result.get(name, 0) + sold

    except Exception as e:
        st.sidebar.error(f"ITZY API 抓取失敗: {e}")

    for member in TARGET_MEMBERS:
        result.setdefault(member, 0)

    return result

# =========================
# 6. 寫入 Google Sheet
# =========================
def append_sale_log(name, now_str, diff, source, total_now):
    if not gc:
        return False

    try:
        wks = ensure_worksheet(name)
        if wks is None:
            return False

        wks.append_row([now_str, int(diff), source, int(total_now)])
        return True
    except Exception as e:
        st.sidebar.error(f"寫入 {name} 失敗: {e}")
        return False

def build_rank_df(log_df):
    if log_df.empty:
        return pd.DataFrame(columns=["張數", "來源"])

    rank_df = log_df.copy()
    rank_df["張數"] = pd.to_numeric(rank_df["張數"], errors="coerce").fillna(0).astype(int)

    positives = rank_df[rank_df["張數"] > 0].copy().reset_index(drop=True)
    negatives = rank_df[rank_df["張數"] < 0].copy().reset_index(drop=True)

    if positives.empty:
        return pd.DataFrame(columns=["張數", "來源"])

    kept_rows = positives.to_dict("records")

    for _, row in negatives.iterrows():
        cancel_qty = abs(int(row["張數"]))

        match_idx = None
        for i, pos in enumerate(kept_rows):
            if int(pos["張數"]) == cancel_qty:
                match_idx = i
                break

        if match_idx is not None:
            kept_rows.pop(match_idx)

    if not kept_rows:
        return pd.DataFrame(columns=["張數", "來源"])

    final_rank_df = pd.DataFrame(kept_rows)
    final_rank_df["張數"] = pd.to_numeric(final_rank_df["張數"], errors="coerce").fillna(0).astype(int)
    final_rank_df = final_rank_df.sort_values("張數", ascending=False).reset_index(drop=True)

    return final_rank_df

# =========================
# 7. 主流程
# =========================
status_placeholder = st.empty()

session = requests.Session()
itzy_res = get_itzy_data(session)

all_names = TARGET_MEMBERS.copy()
sync_from_cloud(all_names)

tz = pytz.timezone("Asia/Taipei")
now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

for name in all_names:
    total_now = int(itzy_res.get(name, 0))

    log_df = st.session_state.member_logs.get(name, pd.DataFrame(columns=LOG_COLUMNS))

    last_total_in_sheet = 0
    if not log_df.empty and "總銷售量" in log_df.columns:
        last_total_in_sheet = int(pd.to_numeric(
            pd.Series([log_df.iloc[0]["總銷售量"]]),
            errors="coerce"
        ).fillna(0).iloc[0])

    diff = total_now - last_total_in_sheet

    # 第一次啟動且 sheet 為空時，不補舊單，只建立基準
    if not st.session_state.bootstrapped and last_total_in_sheet == 0:
        st.session_state.last_totals[name] = total_now
        continue

    if diff != 0:
        if diff > 0:
            source = f"新增 +{diff}"
        else:
            source = f"退單 {abs(diff)}"

        ok = append_sale_log(name, now, diff, source, total_now)

        if ok:
            new_entry = pd.DataFrame([{
                "時間": now,
                "張數": int(diff),
                "來源": source,
                "總銷售量": int(total_now)
            }])

            st.session_state.member_logs[name] = pd.concat(
                [new_entry, log_df],
                ignore_index=True
            )

    st.session_state.last_totals[name] = total_now

st.session_state.bootstrapped = True

# =========================
# 8. 畫面顯示
# =========================
with status_placeholder.container():
    st.write("### 👥 5位成員總銷量統計")

    summary = []
    for n in all_names:
        total = int(itzy_res.get(n, 0))

        summary.append({
            "成員名稱": n,
            "總計": total
        })

    summary_df = pd.DataFrame(summary).sort_values("總計", ascending=False).reset_index(drop=True)
    st.table(summary_df)

    st.divider()

    tabs = st.tabs(all_names)
    for i, tab in enumerate(tabs):
        m_name = all_names[i]

        with tab:
            log_df = st.session_state.member_logs.get(m_name, pd.DataFrame(columns=LOG_COLUMNS))

            cl, cr = st.columns(2)

            with cl:
                st.write("🕒 **銷售時間紀錄**")
                if not log_df.empty:
                    st.dataframe(
                        log_df[["時間", "張數", "來源"]],
                        width="stretch",
                        hide_index=True
                    )
                else:
                    st.info("目前沒有紀錄")

            with cr:
                st.write("🏆 **單筆排行**")
                final_rank_df = build_rank_df(log_df)

                if not final_rank_df.empty:
                    final_rank_df = final_rank_df.reset_index(drop=True)
                    final_rank_df.index = final_rank_df.index + 1

                    rank_display = pd.DataFrame({
                        "排名": [f"第 {idx} 名" for idx in final_rank_df.index],
                        "單筆張數": final_rank_df["張數"].values,
                        "來源": final_rank_df["來源"].values
                    })
                    st.table(rank_display)
                else:
                    st.info("目前沒有有效排行資料")

st.caption(f"最後更新時間：{now}")

time.sleep(15)
st.rerun()
