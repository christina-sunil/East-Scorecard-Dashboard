import streamlit as st
import requests
import pandas as pd
import numpy as np
import altair as alt
from datetime import date

# ================= CONFIG =================
BASE_URL = "https://progress1.service-now.com"
USER = "github_servicenow_api"
PASSWORD = "wL<c&sLHGso(mH3mIRs=byF5C%97o>P3z[K+QZSD"

REQUEST_TABLE = "sc_req_item"
INCIDENT_TABLE = "incident"
SLA_TARGET_DAYS = 5

st.set_page_config(layout="wide")
st.title("📊 EAST Scorecard Dashboard")

# ================= SIDEBAR =================
start = st.sidebar.date_input("Start", date(2025, 12, 1))
end = st.sidebar.date_input("End", date.today())

START = pd.Timestamp(start)
END = pd.Timestamp(end)

include_incidents = st.sidebar.checkbox("Include incidents", True)

# ================= FETCH =================
@st.cache_data
def fetch(table, query):
    url = f"{BASE_URL}/api/now/table/{table}"
    params = {
        "sysparm_query": query,
        "sysparm_limit": 10000,
        "sysparm_display_value": "true"
    }
    r = requests.get(url, auth=(USER, PASSWORD), params=params)
    if r.status_code != 200:
        return pd.DataFrame()
    return pd.json_normalize(r.json()["result"])

# ================= LOAD =================
start_str = START.strftime("%Y-%m-%d")
end_str = END.strftime("%Y-%m-%d")

req_open = fetch(REQUEST_TABLE, f"opened_at>={start_str}^opened_at<={end_str}")
req_close = fetch(REQUEST_TABLE, f"closed_at>={start_str}^closed_at<={end_str}")

inc_open = fetch(INCIDENT_TABLE, f"opened_at>={start_str}^opened_at<={end_str}")
inc_close = fetch(INCIDENT_TABLE, f"closed_at>={start_str}^closed_at<={end_str}")

df_open = pd.concat([req_open, inc_open])
df_close = pd.concat([req_close, inc_close])

# ================= CLEAN =================
df_open["opened_at"] = pd.to_datetime(df_open["opened_at"], errors="coerce")
df_close["closed_at"] = pd.to_datetime(df_close["closed_at"], errors="coerce")

df_close["Biz Days"] = (df_close["closed_at"] - df_close["opened_at"]).dt.days

# ================= METRICS =================
created = len(df_open)
closed = len(df_close)
backlog = created - closed

sla_pct = (df_close["Biz Days"] <= SLA_TARGET_DAYS).mean()*100 if len(df_close) > 0 else 0
fdr_pct = (df_close["Biz Days"] == 0).mean()*100 if len(df_close) > 0 else 0

# ================= MOM =================
df_open["Month"] = df_open["opened_at"].dt.to_period("M").astype(str)
df_close["Month"] = df_close["closed_at"].dt.to_period("M").astype(str)

mom_open = df_open.groupby("Month").size().reset_index(name="Opened")
mom_close = df_close.groupby("Month").size().reset_index(name="Closed")

mom = pd.merge(mom_open, mom_close, on="Month", how="outer").fillna(0)

# ================= BACKLOG TREND =================
months = pd.date_range(START, END, freq="ME")

rows = []
for m in months:
    cnt = ((df_open["opened_at"] <= m) &
           ((df_open["closed_at"].isna()) | (df_open["closed_at"] > m))).sum()

    rows.append({
        "Month": m.strftime("%b %Y"),
        "Backlog": int(cnt)
    })

backlog_trend = pd.DataFrame(rows)

# ================= TABS =================
tab1, tab2 = st.tabs(["Overview", "Trends"])

# ================= OVERVIEW =================
with tab1:

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric("Created", created)
    c2.metric("Closed", closed)
    c3.metric("Backlog", backlog)
    c4.metric("SLA %", round(sla_pct, 2))
    c5.metric("FDR %", round(fdr_pct, 2))

# ================= TRENDS =================
with tab2:

    st.subheader("Month on Month")

    if not mom.empty:
        st.dataframe(mom, use_container_width=True)
    else:
        st.info("No data")

    st.markdown("### 📈 Trend Chart")

    if not mom.empty:
        chart = alt.Chart(mom).mark_line(point=True).encode(
            x="Month:N",
            y="Closed:Q"
        )
        st.altair_chart(chart, use_container_width=True)

    st.markdown("---")

    st.subheader("Backlog Trend")

    if not backlog_trend.empty:
        st.dataframe(backlog_trend, use_container_width=True)
