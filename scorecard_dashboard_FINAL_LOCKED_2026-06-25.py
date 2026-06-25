import streamlit as st
import requests
import pandas as pd
import numpy as np
import altair as alt
from datetime import date

# =========================================================
# CONFIG
# =========================================================

BASE_URL = "https://progress1.service-now.com"
USER = "github_servicenow_api"
PASSWORD = "wL<c&sLHGso(mH3mIRs=byF5C%97o>P3z[K+QZSD"  # <-- ADD PASSWORD HERE

REQUEST_TABLE = "sc_req_item"
INCIDENT_TABLE = "incident"

SLA_TARGET_PCT = 90.0
SLA_TARGET_DAYS = 5

# =========================================================
# GROUP DEFINITIONS
# =========================================================

HISTORICAL_GROUPS = [
    "IT Supp: EAST - Delivery",
    "IT Supp: System Access Requests",
    "IT Supp: EAST - Leads",
    "IT Supp: Salesforce - Sales Cloud",
    "IT Supp: Salesforce - Service Cloud",
    "IT Supp: Salesforce - Sales Cloud - ShareFile",
    "IT Supp: Salesforce - Service Cloud - ShareFile",
    "IT Supp: ShareFile-Intranet",
    "IT Supp: Technical Support",
]

CURRENT_BACKLOG_GROUPS = [
    "IT Supp: EAST - Delivery",
    "IT Supp: System Access Requests",
    "IT Supp: EAST - Leads",
]

L1_GROUPS = [
    "IT Supp: EAST - Delivery",
    "IT Supp: System Access Requests",
]

L2_GROUPS = [
    "IT Supp: EAST - Leads",
]

L3_GROUPS = [
    "IT Supp: Quote to Invoice",
    "IT Supp: Lead to Opp",
    "IT Supp: Product Management",
    "IT Supp: System Admins",
]

EAST_USERS = [
    "Balaji Manikanta Sai Sadhu",
    "Digvijay Pawar",
    "Manognasri Chitrala",
    "Rachana Adiga",
    "Saranmai Mandarapu",
    "Sreeja Janumpally",
    "Raleigh Turner",
    "Christina Sunil",
    "Chris Braga",
]

# =========================================================
# EAST CASE PRIORITIZATION SCORE MODEL
# =========================================================

WEIGHTS = {
    "ticket_age": 0.25,
    "inactivity": 0.20,
    "priority": 0.20,
    "reassignment": 0.15,
    "skill_alignment": 0.10,
    "requester_impact": 0.10,
}

AGE_LOOKUP = [
    (0, 1),
    (3, 2),
    (7, 4),
    (14, 6),
    (30, 8),
    (60, 9),
    (90, 10),
]

INACTIVITY_LOOKUP = [
    (0, 1),
    (2, 3),
    (5, 5),
    (10, 7),
    (20, 9),
    (30, 10),
]

REASSIGNMENT_LOOKUP = [
    (0, 1),
    (1, 3),
    (2, 5),
    (4, 7),
    (6, 9),
    (8, 10),
]

PRIORITY_LOOKUP = {
    "LOW": 3,
    "MEDIUM": 6,
    "HIGH": 9,
    "CRITICAL": 10,
}

SKILL_LOOKUP = {
    "STRONG MATCH": 1,
    "GOOD": 5,
    "MISMATCH": 9,
    "SEVERE MISMATCH": 10,
}

REQUESTER_LOOKUP = {
    "INDIVIDUAL": 3,
    "MANAGER": 5,
    "DIRECTOR": 8,
    "VP+": 10,
    "VP": 10,
}

# =========================================================
# PAGE SETUP
# =========================================================

st.set_page_config(
    page_title="EAST Scorecard Dashboard",
    layout="wide"
)

st.title("📊 EAST Scorecard Dashboard")

# =========================================================
# SIDEBAR
# =========================================================

default_start = date(2025, 12, 1)
default_end = date.today()

start = st.sidebar.date_input("Start", default_start)
end = st.sidebar.date_input("End", default_end)

START = pd.Timestamp(start)
END = pd.Timestamp(end)

include_incidents = st.sidebar.checkbox("Include incidents assigned to my team", value=True)
show_validation = st.sidebar.checkbox("Show validation", value=True)
show_raw = st.sidebar.checkbox("Show raw data", value=False)

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()

# =========================================================
# HELPER FUNCTIONS
# =========================================================

def biz_days(start_dt, end_dt):
    if pd.isna(start_dt) or pd.isna(end_dt):
        return np.nan

    try:
        s = pd.Timestamp(start_dt).normalize().date()
        e = pd.Timestamp(end_dt).normalize().date()
        return np.busday_count(s, e)
    except Exception:
        return np.nan


def age_bucket(days_val):
    if pd.isna(days_val):
        return "Unknown"

    if days_val <= 5:
        return "NEW (0–5)"

    if days_val <= 14:
        return "AGING (6–14)"

    if days_val <= 30:
        return "STALE (15–30)"

    return "OLD (31+)"


def safe_display(df, base_name):
    if df.empty:
        return pd.Series([], dtype=str)

    display_col = f"{base_name}.display_value"

    if display_col in df.columns:
        return df[display_col]

    if base_name in df.columns:
        return df[base_name]

    return pd.Series([""] * len(df), index=df.index)


def clean(df):
    if df.empty:
        return df

    if "opened_at" in df.columns:
        df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce")
    else:
        df["opened_at"] = pd.NaT

    if "closed_at" in df.columns:
        df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce")
    else:
        df["closed_at"] = pd.NaT

    if "sys_updated_on" in df.columns:
        df["sys_updated_on"] = pd.to_datetime(df["sys_updated_on"], errors="coerce")
    else:
        df["sys_updated_on"] = pd.NaT

    df["assignment_group"] = safe_display(df, "assignment_group").fillna("")
    df["assigned_to"] = safe_display(df, "assigned_to").fillna("")

    if "number" in df.columns:
        df["number"] = df["number"].astype(str).str.strip().str.upper()
    else:
        df["number"] = ""

    for col in [
        "state",
        "priority",
        "short_description",
        "reassignment_count",
        "skill_alignment",
        "requester_impact",
    ]:
        if col not in df.columns:
            df[col] = ""

    return df


def normalize_priority(val):
    txt = str(val).strip().upper()

    if "CRITICAL" in txt or txt.startswith("1"):
        return "CRITICAL"

    if "HIGH" in txt or txt.startswith("2"):
        return "HIGH"

    if "MEDIUM" in txt or "MODERATE" in txt or txt.startswith("3"):
        return "MEDIUM"

    if "LOW" in txt or txt.startswith("4") or txt.startswith("5"):
        return "LOW"

    return "MEDIUM"


def normalize_skill(val):
    txt = str(val).strip().upper()

    if txt in SKILL_LOOKUP:
        return txt

    if txt == "" or txt == "NAN":
        return "GOOD"

    return "GOOD"


def normalize_requester_impact(val):
    txt = str(val).strip().upper()

    if txt in REQUESTER_LOOKUP:
        return txt

    if txt == "" or txt == "NAN":
        return "INDIVIDUAL"

    return "INDIVIDUAL"


def threshold_score(value, lookup_pairs):
    try:
        v = float(value)
    except Exception:
        return 0

    score = 0

    for min_val, mapped_score in lookup_pairs:
        if v >= min_val:
            score = mapped_score
        else:
            break

    return score


def get_support_level(group_name, source_table):
    if source_table == INCIDENT_TABLE:
        return "Incident"

    if group_name in L1_GROUPS:
        return "L1"

    if group_name in L2_GROUPS:
        return "L2"

    if group_name in L3_GROUPS:
        return "L3"

    return "Other"


@st.cache_data(show_spinner=False)
def fetch(table, query, max_rows=30000, page_size=1000):
    all_data = []

    for offset in range(0, max_rows, page_size):
        url = f"{BASE_URL}/api/now/table/{table}"

        params = {
            "sysparm_query": query,
            "sysparm_limit": page_size,
            "sysparm_offset": offset,
            "sysparm_display_value": "true",
            "sysparm_fields": (
                "number,opened_at,closed_at,sys_updated_on,"
                "assignment_group,assigned_to,state,priority,"
                "short_description,reassignment_count,skill_alignment,requester_impact"
            ),
        }

        try:
            r = requests.get(
                url,
                auth=(USER, PASSWORD),
                params=params,
                timeout=30
            )

            if r.status_code != 200:
                st.error(f"ServiceNow API error for {table}: {r.status_code}")
                break

            batch = r.json().get("result", [])

        except Exception as e:
            st.error(f"Request failed for {table}: {e}")
            break

        if not batch:
            break

        all_data.extend(batch)

        if len(batch) < page_size:
            break

    return pd.json_normalize(all_data)


def build_weighted_priority(df, end_dt):
    if df.empty:
        return df.copy()

    work = df.copy()

    work["Ticket Age (Biz Days)"] = work.apply(
        lambda x: biz_days(x["opened_at"], end_dt) if pd.notna(x["opened_at"]) else np.nan,
        axis=1
    )

    work["Inactivity (Biz Days)"] = work.apply(
        lambda x: biz_days(x["sys_updated_on"], end_dt) if pd.notna(x["sys_updated_on"]) else np.nan,
        axis=1
    )

    work["SLA Remaining (Biz Days)"] = SLA_TARGET_DAYS - work["Ticket Age (Biz Days)"]

    work["Priority Normalized"] = work["priority"].apply(normalize_priority)

    work["Reassignment Count"] = pd.to_numeric(
        work["reassignment_count"],
        errors="coerce"
    ).fillna(0)

    work["Skill Alignment"] = work["skill_alignment"].apply(normalize_skill)
    work["Requester Impact"] = work["requester_impact"].apply(normalize_requester_impact)

    work["Score - Ticket Age"] = work["Ticket Age (Biz Days)"].apply(
        lambda x: threshold_score(x, AGE_LOOKUP)
    )

    work["Score - Inactivity"] = work["Inactivity (Biz Days)"].apply(
        lambda x: threshold_score(x, INACTIVITY_LOOKUP)
    )

    work["Score - Priority"] = work["Priority Normalized"].map(PRIORITY_LOOKUP).fillna(6)

    work["Score - Reassignment"] = work["Reassignment Count"].apply(
        lambda x: threshold_score(x, REASSIGNMENT_LOOKUP)
    )

    work["Score - Skill Alignment"] = work["Skill Alignment"].map(SKILL_LOOKUP).fillna(5)

    work["Score - Requester Impact"] = work["Requester Impact"].map(REQUESTER_LOOKUP).fillna(3)

    work["WC - Ticket Age"] = work["Score - Ticket Age"] * WEIGHTS["ticket_age"]
    work["WC - Inactivity"] = work["Score - Inactivity"] * WEIGHTS["inactivity"]
    work["WC - Priority"] = work["Score - Priority"] * WEIGHTS["priority"]
    work["WC - Reassignment"] = work["Score - Reassignment"] * WEIGHTS["reassignment"]
    work["WC - Skill Alignment"] = work["Score - Skill Alignment"] * WEIGHTS["skill_alignment"]
    work["WC - Requester Impact"] = work["Score - Requester Impact"] * WEIGHTS["requester_impact"]

    work["Priority Score"] = (
        work["WC - Ticket Age"]
        + work["WC - Inactivity"]
        + work["WC - Priority"]
        + work["WC - Reassignment"]
        + work["WC - Skill Alignment"]
        + work["WC - Requester Impact"]
    )

    work["Aging Risk"] = work["Ticket Age (Biz Days)"] > 15
    work["Inactive Risk"] = work["Inactivity (Biz Days)"] > 3
    work["SLA Risk"] = work["SLA Remaining (Biz Days)"] <= 1

    return work


# =========================================================
# STYLE HELPERS
# =========================================================

def style_sla_remaining(val):
    try:
        if pd.notna(val) and float(val) <= 1:
            return "background-color: orange"
    except Exception:
        pass

    return ""


def style_sla_pct(val):
    try:
        if pd.notna(val) and float(val) < SLA_TARGET_PCT:
            return "background-color: orange"
    except Exception:
        pass

    return ""


# =========================================================
# CHART HELPERS
# =========================================================

def make_metric_trend_chart(metric_df, title):
    if metric_df.empty:
        return None

    chart_data = metric_df.reset_index().melt(
        id_vars="Month",
        value_vars=["SLA %", "FDR %", "Median TTR (Biz Days)"],
        var_name="Metric",
        value_name="Value"
    )

    chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("Month:N", title="Month", sort=None),
            y=alt.Y("Value:Q", title="Value"),
            color=alt.Color("Metric:N", title="Metric"),
            tooltip=["Month:N", "Metric:N", alt.Tooltip("Value:Q", format=".2f")]
        )
        .properties(
            title=title,
            height=360
        )
        .interactive()
    )

    return chart


def make_backlog_trend_chart(backlog_df):
    if backlog_df.empty:
        return None

    chart = (
        alt.Chart(backlog_df)
        .mark_line(point=True)
        .encode(
            x=alt.X("Month:N", title="Month", sort=None),
            y=alt.Y("Backlog:Q", title="Backlog Count"),
            tooltip=["Month:N", "Backlog:Q"]
        )
        .properties(
            title="Backlog Trend — Month vs Data",
            height=320
        )
        .interactive()
    )

    return chart


def make_individual_trend_chart(individual_monthly_df, selected_person):
    if individual_monthly_df.empty:
        return None

    if selected_person != "All":
        chart_source = individual_monthly_df[individual_monthly_df["Assignee"] == selected_person].copy()
        title = f"Individual Trend — {selected_person}"
    else:
        chart_source = individual_monthly_df.groupby("Month").agg(
            **{
                "SLA %": ("SLA %", "mean"),
                "FDR %": ("FDR %", "mean"),
                "Median TTR (Biz Days)": ("Median TTR (Biz Days)", "median")
            }
        ).reset_index()
        title = "Team Individual Trend — Average SLA/FDR and Median TTR"

    if chart_source.empty:
        return None

    chart_data = chart_source.melt(
        id_vars="Month",
        value_vars=["SLA %", "FDR %", "Median TTR (Biz Days)"],
        var_name="Metric",
        value_name="Value"
    )

    chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("Month:N", title="Month", sort=None),
            y=alt.Y("Value:Q", title="Value"),
            color=alt.Color("Metric:N"),
            tooltip=["Month:N", "Metric:N", alt.Tooltip("Value:Q", format=".2f")]
        )
        .properties(
            title=title,
            height=330
        )
        .interactive()
    )

    return chart


# =========================================================
# LOAD DATA
# =========================================================

with st.spinner("Loading data..."):
    start_str = START.strftime("%Y-%m-%d 00:00:00")
    end_str = END.strftime("%Y-%m-%d 23:59:59")

    req_opened_all = fetch(
        REQUEST_TABLE,
        f"opened_at>={start_str}^opened_at<={end_str}",
        max_rows=30000,
        page_size=1000
    )

    req_closed_all = fetch(
        REQUEST_TABLE,
        f"closed_at>={start_str}^closed_at<={end_str}",
        max_rows=30000,
        page_size=1000
    )

    req_open_current = fetch(
        REQUEST_TABLE,
        "closed_atISEMPTY",
        max_rows=15000,
        page_size=1000
    )

    if include_incidents:
        inc_opened_all = fetch(
            INCIDENT_TABLE,
            f"opened_at>={start_str}^opened_at<={end_str}",
            max_rows=30000,
            page_size=1000
        )

        inc_closed_all = fetch(
            INCIDENT_TABLE,
            f"closed_at>={start_str}^closed_at<={end_str}",
            max_rows=30000,
            page_size=1000
        )

        inc_open_current = fetch(
            INCIDENT_TABLE,
            "closed_atISEMPTY",
            max_rows=15000,
            page_size=1000
        )
    else:
        inc_opened_all = pd.DataFrame()
        inc_closed_all = pd.DataFrame()
        inc_open_current = pd.DataFrame()


# =========================================================
# CLEAN DATA
# =========================================================

req_opened_all = clean(req_opened_all)
req_closed_all = clean(req_closed_all)
req_open_current = clean(req_open_current)

inc_opened_all = clean(inc_opened_all)
inc_closed_all = clean(inc_closed_all)
inc_open_current = clean(inc_open_current)


# =========================================================
# FILTER OPENED / CLOSED DATA
# =========================================================

req_opened_hist = req_opened_all[
    (
        req_opened_all["assignment_group"].str.contains(
            "Technical Support|Salesforce|Sharefile|System Access Requests|EAST",
            case=False,
            na=False
        )
    )
    |
    (
        req_opened_all["assigned_to"].isin(EAST_USERS)
    )
].copy()

req_closed_hist = req_closed_all[
    (
        req_closed_all["assignment_group"].str.contains(
            "Technical Support|Salesforce|Sharefile|System Access Requests|EAST",
            case=False,
            na=False
        )
    )
    |
    (
        req_closed_all["assigned_to"].isin(EAST_USERS)
    )
].copy()

if include_incidents:
    inc_opened_hist = inc_opened_all[
        inc_opened_all["assigned_to"].isin(EAST_USERS)
    ].copy()

    inc_closed_hist = inc_closed_all[
        inc_closed_all["assigned_to"].isin(EAST_USERS)
    ].copy()
else:
    inc_opened_hist = pd.DataFrame()
    inc_closed_hist = pd.DataFrame()

opened_hist = pd.concat(
    [req_opened_hist, inc_opened_hist],
    ignore_index=True
)

closed_hist = pd.concat(
    [req_closed_hist, inc_closed_hist],
    ignore_index=True
)

if not opened_hist.empty:
    opened_hist["source_table"] = np.where(
        opened_hist["number"].str.startswith("INC"),
        INCIDENT_TABLE,
        REQUEST_TABLE
    )
else:
    opened_hist["source_table"] = ""

if not closed_hist.empty:
    closed_hist["source_table"] = np.where(
        closed_hist["number"].str.startswith("INC"),
        INCIDENT_TABLE,
        REQUEST_TABLE
    )
else:
    closed_hist["source_table"] = ""


# =========================================================
# CURRENT BACKLOG DATA
# =========================================================

request_backlog = req_open_current[
    req_open_current["assignment_group"].isin(CURRENT_BACKLOG_GROUPS)
].copy()

request_backlog["source_table"] = REQUEST_TABLE

if include_incidents:
    incident_backlog = inc_open_current[
        inc_open_current["assigned_to"].isin(EAST_USERS)
    ].copy()

    incident_backlog["source_table"] = INCIDENT_TABLE
else:
    incident_backlog = pd.DataFrame()

current_backlog_df = pd.concat(
    [request_backlog, incident_backlog],
    ignore_index=True
)

if not current_backlog_df.empty:
    current_backlog_df["Support Level"] = current_backlog_df.apply(
        lambda x: get_support_level(x["assignment_group"], x["source_table"]),
        axis=1
    )

priority_df = build_weighted_priority(current_backlog_df, END)


# =========================================================
# METRICS
# =========================================================

created = len(opened_hist)
closed = len(closed_hist)

if not closed_hist.empty:
    closed_hist["Biz Days"] = closed_hist.apply(
        lambda x: biz_days(x["opened_at"], x["closed_at"]),
        axis=1
    )
else:
    closed_hist["Biz Days"] = pd.Series(dtype=float)

requested_backlog = len(request_backlog)
incident_backlog_count = len(incident_backlog)
total_backlog = requested_backlog + incident_backlog_count

fdr_pct = (closed_hist["Biz Days"] == 0).mean() * 100 if len(closed_hist) > 0 else 0
sla_pct = (closed_hist["Biz Days"] <= SLA_TARGET_DAYS).mean() * 100 if len(closed_hist) > 0 else 0
avg_ttr = closed_hist["Biz Days"].mean() if len(closed_hist) > 0 else 0
median_ttr = closed_hist["Biz Days"].median() if len(closed_hist) > 0 else 0


# =========================================================
# MOM / WOW
# =========================================================

opened_hist["Opened_Month"] = opened_hist["opened_at"].dt.to_period("M").astype(str)
opened_hist["Opened_Week"] = opened_hist["opened_at"].dt.to_period("W-SUN").astype(str)

closed_hist["Closed_Month"] = closed_hist["closed_at"].dt.to_period("M").astype(str)
closed_hist["Closed_Week"] = closed_hist["closed_at"].dt.to_period("W-SUN").astype(str)

opened_mom = opened_hist.groupby("Opened_Month").size().reset_index(name="Opened")
closed_mom = closed_hist.groupby("Closed_Month").size().reset_index(name="Closed")

mom = opened_mom.merge(
    closed_mom,
    left_on="Opened_Month",
    right_on="Closed_Month",
    how="outer"
)

mom["Month"] = mom["Opened_Month"].fillna(mom["Closed_Month"])
mom = mom.drop(columns=[c for c in ["Opened_Month", "Closed_Month"] if c in mom.columns])

if not closed_hist.empty:
    mom_perf = closed_hist.groupby("Closed_Month").agg(
        **{
            "SLA %": ("Biz Days", lambda x: (x <= SLA_TARGET_DAYS).mean() * 100),
            "FDR %": ("Biz Days", lambda x: (x == 0).mean() * 100),
            "Avg TTR (Biz Days)": ("Biz Days", "mean"),
            "Median TTR (Biz Days)": ("Biz Days", "median"),
        }
    ).reset_index().rename(columns={"Closed_Month": "Month"})

    mom = mom.merge(mom_perf, on="Month", how="left")

mom = mom[
    [
        "Month",
        "Opened",
        "Closed",
        "SLA %",
        "FDR %",
        "Avg TTR (Biz Days)",
        "Median TTR (Biz Days)",
    ]
]

opened_wow = opened_hist.groupby("Opened_Week").size().reset_index(name="Opened")
closed_wow = closed_hist.groupby("Closed_Week").size().reset_index(name="Closed")

wow = opened_wow.merge(
    closed_wow,
    left_on="Opened_Week",
    right_on="Closed_Week",
    how="outer"
)

wow["Week"] = wow["Opened_Week"].fillna(wow["Closed_Week"])
wow = wow.drop(columns=[c for c in ["Opened_Week", "Closed_Week"] if c in wow.columns])

if not closed_hist.empty:
    wow_perf = closed_hist.groupby("Closed_Week").agg(
        **{
            "SLA %": ("Biz Days", lambda x: (x <= SLA_TARGET_DAYS).mean() * 100),
            "FDR %": ("Biz Days", lambda x: (x == 0).mean() * 100),
            "Avg TTR (Biz Days)": ("Biz Days", "mean"),
            "Median TTR (Biz Days)": ("Biz Days", "median"),
        }
    ).reset_index().rename(columns={"Closed_Week": "Week"})

    wow = wow.merge(wow_perf, on="Week", how="left")

wow = wow[
    [
        "Week",
        "Opened",
        "Closed",
        "SLA %",
        "FDR %",
        "Avg TTR (Biz Days)",
        "Median TTR (Biz Days)",
    ]
]


# =========================================================
# TREND CHART DATA
# =========================================================

trend_month_chart = pd.DataFrame()

if not mom.empty:
    trend_month_chart = mom.copy()
    trend_month_chart = trend_month_chart.set_index("Month")[
        [
            "SLA %",
            "FDR %",
            "Median TTR (Biz Days)",
        ]
    ]


# =========================================================
# BACKLOG TREND
# =========================================================

all_hist = pd.concat(
    [opened_hist, closed_hist, current_backlog_df],
    ignore_index=True
)


if not all_hist.empty:
    month_ends = list(pd.date_range(START, END, freq="ME"))

    # ✅ ensure latest month (June) is included
    if len(month_ends) == 0 or month_ends[-1].month != END.month:
        month_ends.append(END)

    rows = []

    for month_end in month_ends:
        cnt = (
            (all_hist["opened_at"].notna())
            & (all_hist["opened_at"] <= month_end)
            & (
                all_hist["closed_at"].isna()
                | (all_hist["closed_at"] > month_end)
            )
        ).sum()

        rows.append({
            "Month": pd.Timestamp(month_end).strftime("%b %Y"),
            "Backlog": int(cnt),
        })

    backlog_trend = pd.DataFrame(rows)

month_ends = pd.date_range(START, END, freq="ME")

# ✅ ensure June (current month) is included
if len(month_ends) == 0 or month_ends[-1].month != END.month:
    month_ends = list(month_ends) + [END]

    rows = []

    for month_end in month_ends:
        cnt = (
            (all_hist["opened_at"].notna())
            & (all_hist["opened_at"] <= month_end)
            & (
                all_hist["closed_at"].isna()
                | (all_hist["closed_at"] > month_end)
            )
        ).sum()

        rows.append(
            {
                "Month": month_end.strftime("%b %Y"),
                "Backlog": int(cnt),
            }
        )

    backlog_trend = pd.DataFrame(rows)


# =========================================================
# L1 / L2 QUEUE
# =========================================================

current_l1_df = request_backlog[
    request_backlog["assignment_group"].isin(L1_GROUPS)
].copy()

current_l2_df = request_backlog[
    request_backlog["assignment_group"].isin(L2_GROUPS)
].copy()

for queue_df in [current_l1_df, current_l2_df]:
    if not queue_df.empty:
        queue_df["Aging (Biz Days)"] = queue_df.apply(
            lambda x: biz_days(x["sys_updated_on"], END) if pd.notna(x["sys_updated_on"]) else np.nan,
            axis=1,
        )


# =========================================================
# INDIVIDUAL SCORECARD
# =========================================================

ind = pd.DataFrame()

if not closed_hist.empty:
    ind = closed_hist[
        closed_hist["assigned_to"].isin(EAST_USERS)
    ].groupby("assigned_to").agg(
        Closed=("number", "count"),
        Avg_TTR=("Biz Days", "mean"),
        Median_TTR=("Biz Days", "median"),
        SLA=("Biz Days", lambda x: (x <= SLA_TARGET_DAYS).mean() * 100),
        FDR=("Biz Days", lambda x: (x == 0).mean() * 100),
    ).reset_index()

    opened_counts = opened_hist[
        opened_hist["assigned_to"].isin(EAST_USERS)
    ].groupby("assigned_to").size().reset_index(name="Opened")

    backlog_counts = current_backlog_df[
        current_backlog_df["assigned_to"].isin(EAST_USERS)
    ].groupby("assigned_to").size().reset_index(name="Backlog")

    ind = ind.merge(opened_counts, on="assigned_to", how="left")
    ind = ind.merge(backlog_counts, on="assigned_to", how="left")

    ind[["Opened", "Backlog"]] = ind[["Opened", "Backlog"]].fillna(0)

    ind = ind.rename(columns={"assigned_to": "Assignee"})

    ind = ind.sort_values("Closed", ascending=False)


ind_monthly = pd.DataFrame()

if not closed_hist.empty:
    opened_hist["Month"] = opened_hist["opened_at"].dt.to_period("M").astype(str)
    closed_hist["Month"] = closed_hist["closed_at"].dt.to_period("M").astype(str)

    people_from_opened = opened_hist[
        opened_hist["assigned_to"].isin(EAST_USERS)
    ]["assigned_to"].dropna().tolist()

    people_from_closed = closed_hist[
        closed_hist["assigned_to"].isin(EAST_USERS)
    ]["assigned_to"].dropna().tolist()

    valid_people = sorted(list(set(people_from_opened + people_from_closed)))

    months = sorted(
        list(
            set(
                opened_hist["Month"].dropna().tolist()
                + closed_hist["Month"].dropna().tolist()
            )
        )
    )

    monthly_rows = []

    for month in months:
        month_opened = opened_hist[opened_hist["Month"] == month].copy()
        month_closed = closed_hist[closed_hist["Month"] == month].copy()

        for person in valid_people:
            p_open = month_opened[month_opened["assigned_to"] == person]
            p_close = month_closed[month_closed["assigned_to"] == person]
            p_backlog = current_backlog_df[current_backlog_df["assigned_to"] == person]

            if len(p_open) == 0 and len(p_close) == 0 and len(p_backlog) == 0:
                continue

            monthly_rows.append(
                {
                    "Month": month,
                    "Assignee": person,
                    "Opened": len(p_open),
                    "Closed": len(p_close),
                    "Backlog": len(p_backlog),
                    "Avg TTR (Biz Days)": p_close["Biz Days"].mean() if len(p_close) > 0 else np.nan,
                    "Median TTR (Biz Days)": p_close["Biz Days"].median() if len(p_close) > 0 else np.nan,
                    "SLA %": (p_close["Biz Days"] <= SLA_TARGET_DAYS).mean() * 100 if len(p_close) > 0 else np.nan,
                    "FDR %": (p_close["Biz Days"] == 0).mean() * 100 if len(p_close) > 0 else np.nan,
                }
            )

    ind_monthly = pd.DataFrame(monthly_rows)


# =========================================================
# TABS
# =========================================================

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "Overview",
        "Trends",
        "L1/L2",
        "Backlog",
        "Individual",
        "Validation",
    ]
)


# =========================================================
# OVERVIEW TAB
# =========================================================

with tab1:
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric("Created", f"{created:,}")
    c2.metric("Closed", f"{closed:,}")
    c3.metric("Backlog", f"{total_backlog:,}")
    c4.metric("FDR %", round(fdr_pct, 2))
    c5.metric("SLA %", round(sla_pct, 2))
    c6.metric("Avg TTR (Biz Days)", round(avg_ttr, 2))

    st.markdown("---")

    st.markdown("### ⚠ SLA Definition & Color Meaning")

    st.info(
        "**SLA Target: 90%**\n\n"
        "🟠 **Orange highlight indicates attention is needed**\n"
        "- In trend / scorecard tables: **SLA % below 90%**\n"
        "- In ticket tables: **SLA Remaining (Biz Days) ≤ 1**\n\n"
        "📌 **How SLA is calculated**\n"
        "- Business Days only (weekends excluded)\n"
        "- SLA is met when a ticket is resolved within **5 Business Days**"
    )

    st.markdown("### 📊 Executive Summary")

    summary_lines = []

    summary_lines.append(f"Current total backlog is **{total_backlog}** open items.")
    summary_lines.append(f"SLA performance is **{round(sla_pct, 1)}%** against the **90% target**.")

    if not priority_df.empty:
        summary_lines.append(
            f"**{int(priority_df['SLA Risk'].sum())}** open items are at immediate SLA risk "
            f"(≤1 business day remaining)."
        )

        if not current_backlog_df.empty:
            top_group = current_backlog_df["assignment_group"].value_counts().idxmax()
            summary_lines.append(f"The largest current backlog concentration is in **{top_group}**.")

    for line in summary_lines:
        st.markdown(f"- {line}")

    st.markdown("---")

    b1, b2, b3 = st.columns(3)

    b1.metric("Requested Backlog", requested_backlog)
    b2.metric("Incident Backlog", incident_backlog_count)
    b3.metric("Total Backlog", total_backlog)

    st.markdown("---")

    st.subheader("Current Backlog Assignment Groups")

    if not current_backlog_df.empty:
        grp = current_backlog_df["assignment_group"].value_counts().reset_index()
        grp.columns = ["Assignment Group", "Count"]
        st.dataframe(grp, use_container_width=True)
    else:
        st.info("No current backlog items found.")

    st.markdown("---")

    st.markdown("## 🚨 Priority Actions (Operational Focus)")

    p1, p2, p3 = st.columns(3)

    p1.metric("Tickets >15 Biz Days", int(priority_df["Aging Risk"].sum()) if not priority_df.empty else 0)
    p2.metric("No Update >3 Biz Days", int(priority_df["Inactive Risk"].sum()) if not priority_df.empty else 0)
    p3.metric("SLA Risk (≤1 Biz Day)", int(priority_df["SLA Risk"].sum()) if not priority_df.empty else 0)

    st.markdown("### 🎯 Top Priority Tickets (Weighted Model)")

    if not priority_df.empty:
        priority_view = priority_df.sort_values(
            by=[
                "Priority Score",
                "Ticket Age (Biz Days)",
                "Inactivity (Biz Days)",
            ],
            ascending=[False, False, False],
        ).copy()

        cols = [
            "number",
            "source_table",
            "assigned_to",
            "assignment_group",
            "Support Level",
            "Ticket Age (Biz Days)",
            "Inactivity (Biz Days)",
            "SLA Remaining (Biz Days)",
            "Priority Normalized",
            "Reassignment Count",
            "Skill Alignment",
            "Requester Impact",
            "Priority Score",
        ]

       
if not top_table.empty:

    top_table.rename(
        columns={
            "number": "Ticket",
            "source_table": "Source",
            "assigned_to": "Assignee",
            "assignment_group": "Assignment Group",
        },
        inplace=True,
    )

    top_table["Priority Score"] = top_table["Priority Score"].round(2)

    st.dataframe(
        top_table,
        use_container_width=True
    )

else:
    st.info("No current backlog items available for priority scoring.")


# =========================================================
# TRENDS TAB
# =========================================================

with tab2:
    st.caption(
        "SLA % and FDR % are percentages. Avg / Median TTR are Business Days. "
        "WoW is grouped Monday→Sunday."
    )

    st.subheader("Month on Month")

    
st.dataframe(
    mom.round(2),
    use_container_width=True
)

if not trend_month_chart.empty:

        st.markdown("### 📈 Trend Chart — SLA %, FDR %, Median TTR")

        trend_chart = make_metric_trend_chart(
            trend_month_chart,
            "SLA %, FDR %, Median TTR by Month"
        )

        if trend_chart is not None:
            st.altair_chart(trend_chart, use_container_width=True)

    st.markdown("---")

    st.subheader("Week on Week")

        wow.round(2),
        use_container_width=True,
    )

    st.markdown("---")

    st.subheader("📉 Backlog Trend — Month vs Data")

    if not backlog_trend.empty:
        backlog_chart = make_backlog_trend_chart(backlog_trend)

        if backlog_chart is not None:
            st.altair_chart(backlog_chart, use_container_width=True)

        st.dataframe(backlog_trend, use_container_width=True)
    else:
        st.info("No backlog trend could be reconstructed from the current filtered data.")


# =========================================================
# L1 / L2 TAB
# =========================================================

with tab3:
    m1, m2 = st.columns(2)

    m1.metric("Current L1 Queue", len(current_l1_df))
    m2.metric("Current L2 Queue", len(current_l2_df))

    st.markdown("---")

    st.subheader("Current L1 Queue")

    if not current_l1_df.empty:
        cols = [
            c for c in [
                "number",
                "assigned_to",
                "assignment_group",
                "opened_at",
                "sys_updated_on",
                "Aging (Biz Days)",
                "state",
            ]
            if c in current_l1_df.columns
        ]

        l1_view = current_l1_df[cols].rename(
            columns={
                "opened_at": "Opened Date",
                "sys_updated_on": "Last Updated",
            }
        )

        st.dataframe(l1_view.head(200), use_container_width=True)
    else:
        st.info("No current L1 queue items.")

    st.markdown("---")

    st.subheader("Current L2 Queue")

    if not current_l2_df.empty:
        cols = [
            c for c in [
                "number",
                "assigned_to",
                "assignment_group",
                "opened_at",
                "sys_updated_on",
                "Aging (Biz Days)",
                "state",
            ]
            if c in current_l2_df.columns
        ]

        l2_view = current_l2_df[cols].rename(
            columns={
                "opened_at": "Opened Date",
                "sys_updated_on": "Last Updated",
            }
        )

        st.dataframe(l2_view.head(200), use_container_width=True)
    else:
        st.info("No current L2 queue items.")


# =========================================================
# BACKLOG TAB
# =========================================================

with tab4:
    b1, b2, b3 = st.columns(3)

    b1.metric("Requested Backlog", requested_backlog)
    b2.metric("Incident Backlog", incident_backlog_count)
    b3.metric("Total Backlog", total_backlog)

    st.markdown("---")

    st.subheader("Backlog Health Summary")

    if not priority_df.empty:
        backlog_health = priority_df.copy()
        backlog_health["Age Bucket"] = backlog_health["Ticket Age (Biz Days)"].apply(age_bucket)

        health = backlog_health.groupby(
            [
                "source_table",
                "assignment_group",
                "Age Bucket",
            ]
        ).size().reset_index(name="Count")

        health.columns = [
            "Source",
            "Assignment Group",
            "Age Bucket",
            "Count",
        ]

        st.dataframe(health, use_container_width=True)
    else:
        st.info("No backlog health rows found.")

    st.markdown("---")

    st.subheader("All Open Backlog (Requests + Incidents)")

    if not priority_df.empty:
        all_open_cols = [
            "number",
            "source_table",
            "assigned_to",
            "assignment_group",
            "state",
            "priority",
            "opened_at",
            "sys_updated_on",
            "Support Level",
            "Ticket Age (Biz Days)",
            "Inactivity (Biz Days)",
            "SLA Remaining (Biz Days)",
            "Priority Score",
        ]

        all_open_view = priority_df[all_open_cols].copy()

        all_open_view.rename(
            columns={
                "number": "Ticket",
                "source_table": "Source",
                "assigned_to": "Assignee",
                "assignment_group": "Assignment Group",
                "opened_at": "Opened Date",
                "sys_updated_on": "Last Updated",
            },
            inplace=True,
        )

        all_open_view["Priority Score"] = all_open_view["Priority Score"].round(2)

        st.dataframe(
            all_open_view.style.applymap(
                style_sla_remaining,
                subset=["SLA Remaining (Biz Days)"]
            ),
            use_container_width=True,
        )
    else:
        st.info("No open backlog items.")


# =========================================================
# INDIVIDUAL TAB
# =========================================================

with tab5:
    st.subheader("Individual Scorecard")

    if not ind.empty:
        st.dataframe(
            ind.round(2).style.applymap(
                style_sla_pct,
                subset=["SLA"]
            ),
            use_container_width=True,
        )
    else:
        st.info("No individual scorecard rows found.")

    st.markdown("---")

    st.subheader("Individual Report (Monthly Split)")

    if not ind_monthly.empty:
        person_options = ["All"] + sorted(ind_monthly["Assignee"].dropna().unique().tolist())

        selected_person = st.selectbox(
            "Select person for individual trend",
            person_options
        )

        ind_chart = make_individual_trend_chart(
            ind_monthly,
            selected_person
        )

        if ind_chart is not None:
            st.altair_chart(ind_chart, use_container_width=True)

        if selected_person != "All":
            display_ind_monthly = ind_monthly[ind_monthly["Assignee"] == selected_person].copy()
        else:
            display_ind_monthly = ind_monthly.copy()

        st.dataframe(
            display_ind_monthly.round(2).style.applymap(
                style_sla_pct,
                subset=["SLA %"]
            ),
            use_container_width=True,
        )
    else:
        st.info("No monthly individual report available.")


# =========================================================
# VALIDATION TAB
# =========================================================

with tab6:
    if show_validation:
        st.subheader("Validation")

        st.write("✅ Requests opened in range:", len(req_opened_hist))
        st.write("✅ Requests closed in range:", len(req_closed_hist))
        st.write("✅ Incidents opened in range:", len(inc_opened_hist))
        st.write("✅ Incidents closed in range:", len(inc_closed_hist))
        st.write("✅ Current request backlog rows:", len(request_backlog))
        st.write("✅ Current incident backlog rows:", len(incident_backlog))
        st.write("✅ Current combined backlog rows:", len(current_backlog_df))
        st.write("✅ Priority-scored backlog rows:", len(priority_df))

        if show_raw:
            st.markdown("---")
            st.subheader("Raw Opened-in-Range Sample")

            opened_cols = [
                c for c in [
                    "number",
                    "opened_at",
                    "closed_at",
                    "sys_updated_on",
                    "assignment_group",
                    "assigned_to",
                    "state",
                    "priority",
                ]
                if c in opened_hist.columns
            ]

            st.dataframe(
                opened_hist[opened_cols].head(200),
                use_container_width=True
            )

            st.markdown("---")
            st.subheader("Raw Closed-in-Range Sample")

            closed_cols = [
                c for c in [
                    "number",
                    "opened_at",
                    "closed_at",
                    "sys_updated_on",
                    "assignment_group",
                    "assigned_to",
                    "state",
                    "priority",
                    "Biz Days",
                ]
                if c in closed_hist.columns
            ]

            st.dataframe(
                closed_hist[closed_cols].head(200),
                use_container_width=True
            )
