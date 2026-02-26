import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import date
import uuid
import calendar
import re
from io import BytesIO
import textwrap

import gspread
from google.oauth2.service_account import Credentials


# ============================================================
# 기본 설정
# ============================================================
st.set_page_config(page_title="가계부", layout="centered")

# ============================================================
# 🔐 비밀번호 보호 (Secrets)
# ============================================================
PASSWORD = st.secrets.get("app", {}).get("password", "")

if PASSWORD:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🔒 가계부 로그인")
        pw = st.text_input("비밀번호를 입력하세요", type="password")
        login = st.button("로그인", use_container_width=True)

        if login:
            if pw.strip() == PASSWORD:
                st.session_state.authenticated = True
                st.success("로그인 성공!")
                st.rerun()
            else:
                st.error("비밀번호가 틀렸어요.")

        st.stop()


# ============================================================
# ✅ Google Sheets 연결
# ============================================================
GSHEET_ID = st.secrets["gsheets"]["spreadsheet_id"]
SA_INFO = st.secrets["gcp_service_account"]

SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@st.cache_resource(show_spinner=False)
def gs_client():
    creds = Credentials.from_service_account_info(SA_INFO, scopes=SCOPE)
    return gspread.authorize(creds)


def get_spreadsheet():
    return gs_client().open_by_key(GSHEET_ID)


def get_or_create_worksheet(title: str, rows: int = 2000, cols: int = 30):
    sh = get_spreadsheet()
    try:
        return sh.worksheet(title)
    except Exception:
        return sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))


def ws_read_df(ws_title: str, columns: list[str]) -> pd.DataFrame:
    ws = get_or_create_worksheet(ws_title)
    values = ws.get_all_values()

    if not values:
        return pd.DataFrame(columns=columns)

    header = values[0]
    data = values[1:]

    # 헤더가 없거나 이상하면 강제로 columns로 세팅
    if len(header) == 0 or all(h.strip() == "" for h in header):
        header = columns

    df = pd.DataFrame(data, columns=header[: len(header)])

    # 필요한 컬럼 보장
    for c in columns:
        if c not in df.columns:
            df[c] = ""

    df = df[columns].copy()
    return df


def ws_write_df(ws_title: str, df: pd.DataFrame, columns: list[str]) -> None:
    ws = get_or_create_worksheet(ws_title)
    out = df.copy()
    for c in columns:
        if c not in out.columns:
            out[c] = ""
    out = out[columns].copy()

    out = out.fillna("")
    values = [columns] + out.astype(str).values.tolist()

    ws.clear()
    ws.update(values)


def clear_cache_and_rerun(msg: str | None = None):
    st.cache_data.clear()
    if msg:
        st.success(msg)
    st.rerun()


# ============================================================
# 카테고리
# ============================================================
expense_categories = [
    "1. 식재료", "2. 외식/배달", "3. 생활", "4. 육아용품", "5. 여가",
    "6. 교통비/유류", "7. 의료", "8. 기타", "9. 고정지출", "10. 목돈지출"
]
income_categories = ["월급", "부수입", "이자", "캐시백", "기타"]
FIXED_CATEGORY = "9. 고정지출"
LUMPSUM_CATEGORY = "10. 목돈지출"
budget_categories = [c for c in expense_categories if c not in [FIXED_CATEGORY, LUMPSUM_CATEGORY]]

all_categories = []
for c in expense_categories + income_categories:
    if c not in all_categories:
        all_categories.append(c)


# ============================================================
# CSS (iOS Minimal Theme + 카드형 요약 + 예산표 초과 강조)
# ============================================================
st.markdown(
    """
    <style>
      :root{
        --bg: #F2F2F7;
        --card: rgba(255,255,255,0.92);
        --card-border: rgba(60,60,67,0.12);
        --text: #111111;
        --subtext: rgba(60,60,67,0.72);
        --accent: #0A84FF;
        --danger: #FF3B30;
        --danger-soft: rgba(255,59,48,0.12);
        --shadow: 0 10px 24px rgba(0,0,0,0.06);
        --radius: 18px;
      }
      @media (prefers-color-scheme: dark) {
        :root{
          --bg: #000000;
          --card: rgba(28,28,30,0.92);
          --card-border: rgba(255,255,255,0.12);
          --text: #FFFFFF;
          --subtext: rgba(235,235,245,0.65);
          --accent: #0A84FF;
          --danger: #FF453A;
          --danger-soft: rgba(255,69,58,0.18);
          --shadow: 0 10px 24px rgba(0,0,0,0.45);
        }
      }

      .stApp { background: var(--bg); color: var(--text); }
      html, body, [class*="css"]  {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
                     "Apple SD Gothic Neo", "Noto Sans KR", Segoe UI, Roboto, Arial, sans-serif;
        letter-spacing: -0.2px;
      }
      section.main > div { max-width: 920px; padding-top: 0.25rem; }
      h1, h2, h3 { color: var(--text); letter-spacing: -0.4px; }
      .stCaption, .stMarkdown p, .stMarkdown span { color: var(--subtext); }

      div[data-testid="stVerticalBlockBorderWrapper"]{
        background: var(--card);
        border: 1px solid var(--card-border);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        padding: 14px 16px;
      }

      input, textarea {
        text-align: right;
        font-variant-numeric: tabular-nums;
        border-radius: 14px !important;
      }
      div[data-baseweb="select"] > div { border-radius: 14px !important; }

      .stButton > button, .stDownloadButton > button {
        border-radius: 14px !important;
        border: 1px solid var(--card-border) !important;
        background: var(--card) !important;
        color: var(--text) !important;
        box-shadow: 0 6px 16px rgba(0,0,0,0.06);
        transition: transform 0.06s ease, opacity 0.12s ease;
      }
      .stButton > button:hover, .stDownloadButton > button:hover {
        transform: translateY(-1px);
        opacity: 0.96;
        border-color: rgba(10,132,255,0.35) !important;
      }

      hr { border-color: rgba(60,60,67,0.16) !important; }

      .total-diff {
        font-size: 2.1rem;
        font-weight: 750;
        font-variant-numeric: tabular-nums;
        letter-spacing: -0.6px;
        color: var(--text);
      }
      .total-diff.neg { color: var(--danger); }

      /* iOS Summary Cards */
      .ios-metric-grid{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
      }
      @media (max-width: 720px) {
        .ios-metric-grid{ grid-template-columns: 1fr; }
      }
      .ios-card{
        background: var(--card);
        border: 1px solid var(--card-border);
        border-radius: 18px;
        box-shadow: var(--shadow);
        padding: 14px 14px;
      }
      .ios-label{
        font-size: 0.86rem;
        font-weight: 650;
        color: var(--subtext);
        margin-bottom: 6px;
      }
      .ios-value{
        font-size: 1.55rem;
        font-weight: 800;
        letter-spacing: -0.8px;
        font-variant-numeric: tabular-nums;
        color: var(--text);
        line-height: 1.1;
        margin-bottom: 6px;
      }
      .ios-help{
        font-size: 0.82rem;
        color: var(--subtext);
      }
      .ios-danger{ color: var(--danger); }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# 유틸
# ============================================================
def month_range(year: int, month: int):
    last_day = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day)
    return start, end, last_day


def to_int_money(x, default=0) -> int:
    if x is None:
        return default
    s = str(x).strip()
    if s == "":
        return default
    s = s.replace(",", "")
    s = re.sub(r"[^\d\-]", "", s)
    if s in ["", "-", "--"]:
        return default
    try:
        return int(s)
    except Exception:
        return default


def money_str(x) -> str:
    try:
        return f"{int(x):,}"
    except Exception:
        return "0"


def month_selector(prefix_key: str):
    today = date.today()
    years = list(range(today.year - 3, today.year + 2))
    col_y, col_m = st.columns(2)
    with col_y:
        y = st.selectbox("연도", years, index=years.index(today.year), key=f"{prefix_key}_year")
    with col_m:
        ml = [f"{m}월" for m in range(1, 13)]
        m_label = st.selectbox("월", ml, index=today.month - 1, key=f"{prefix_key}_month")
        m = int(m_label.replace("월", ""))
    start_d, end_d, _ = month_range(y, m)
    st.caption(f"선택 기간: {start_d} ~ {end_d}")
    return y, m


def html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def render_ios_summary_cards(items: list[dict]) -> str:
    cards = []
    for it in items:
        label = html_escape(it.get("label", ""))
        value = html_escape(it.get("value", ""))
        help_ = html_escape(it.get("help", ""))
        tone = it.get("tone", "normal")
        value_cls = "ios-value ios-danger" if tone == "danger" else "ios-value"
        cards.append(
            f"""<div class="ios-card">
  <div class="ios-label">{label}</div>
  <div class="{value_cls}">{value}</div>
  <div class="ios-help">{help_}</div>
</div>"""
        )
    html = f"""<div class="ios-metric-grid">
{''.join(cards)}
</div>"""
    return textwrap.dedent(html).strip()


def dynamic_table_height(n_rows: int, base: int = 130, row_h: int = 36, min_h: int = 240, max_h: int = 700) -> int:
    h = base + n_rows * row_h
    return max(min_h, min(max_h, h))


# ============================================================
# Sheets 스키마(컬럼)
# ============================================================
LEDGER_COLS = ["id", "date", "type", "category", "amount", "memo", "fixed_key"]
BUDGET_COLS = ["year", "month", "category", "budget"]
FIXED_COLS = ["fixed_id", "name", "amount", "day", "memo"]
SIMPLE_COLS = ["id", "date", "type", "amount", "memo"]


# ============================================================
# 로드/저장 (Google Sheets)
# ============================================================
@st.cache_data(show_spinner=False)
def load_ledger() -> pd.DataFrame:
    df = ws_read_df("ledger", LEDGER_COLS)

    if len(df):
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["memo"] = df["memo"].fillna("")
        df["type"] = df["type"].fillna("")
        df["category"] = df["category"].fillna("")
        df["fixed_key"] = df["fixed_key"].fillna("").astype(str)

    if "id" not in df.columns:
        df.insert(0, "id", [str(uuid.uuid4()) for _ in range(len(df))])

    save_ledger(df)
    return df[LEDGER_COLS].copy()


def save_ledger(df: pd.DataFrame) -> None:
    out = df.copy()
    if "id" not in out.columns:
        out.insert(0, "id", [str(uuid.uuid4()) for _ in range(len(out))])
    if "fixed_key" not in out.columns:
        out["fixed_key"] = ""

    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    out["memo"] = out["memo"].fillna("")
    out["type"] = out["type"].fillna("")
    out["category"] = out["category"].fillna("")
    out["fixed_key"] = out["fixed_key"].fillna("").astype(str)

    ws_write_df("ledger", out, LEDGER_COLS)


@st.cache_data(show_spinner=False)
def load_budget_month(year: int, month: int) -> pd.DataFrame:
    bdf_all = ws_read_df("budgets_monthly", BUDGET_COLS)
    if len(bdf_all):
        bdf_all["year"] = pd.to_numeric(bdf_all["year"], errors="coerce").fillna(0).astype(int)
        bdf_all["month"] = pd.to_numeric(bdf_all["month"], errors="coerce").fillna(0).astype(int)
        bdf_all["budget"] = pd.to_numeric(bdf_all["budget"], errors="coerce").fillna(0).astype(int)
        bdf_all["category"] = bdf_all["category"].fillna("").astype(str).str.strip()
        bdf = bdf_all[(bdf_all["year"] == year) & (bdf_all["month"] == month)].copy()
    else:
        bdf = pd.DataFrame(columns=["category", "budget"])

    if len(bdf) == 0:
        bdf = pd.DataFrame({"category": budget_categories, "budget": 0})
    else:
        bdf = bdf[["category", "budget"]].copy()

    bdf = bdf[bdf["category"].isin(budget_categories)].copy()

    missing = [c for c in budget_categories if c not in set(bdf["category"])]
    if missing:
        bdf = pd.concat([bdf, pd.DataFrame({"category": missing, "budget": [0] * len(missing)})], ignore_index=True)

    bdf["budget"] = pd.to_numeric(bdf["budget"], errors="coerce").fillna(0).astype(int)
    return bdf.reset_index(drop=True)


def save_budget_month(bdf_month: pd.DataFrame, year: int, month: int) -> None:
    out_month = bdf_month.copy()
    out_month["category"] = out_month["category"].fillna("").astype(str).str.strip()
    out_month = out_month[out_month["category"].isin(expense_categories)].copy()
    out_month["budget"] = pd.to_numeric(out_month["budget"], errors="coerce").fillna(0).astype(int)
    out_month["year"] = year
    out_month["month"] = month
    out_month = out_month[BUDGET_COLS].copy()

    bdf_all = ws_read_df("budgets_monthly", BUDGET_COLS)
    if len(bdf_all):
        bdf_all["year"] = pd.to_numeric(bdf_all["year"], errors="coerce").fillna(0).astype(int)
        bdf_all["month"] = pd.to_numeric(bdf_all["month"], errors="coerce").fillna(0).astype(int)
        bdf_all = bdf_all[~((bdf_all["year"] == year) & (bdf_all["month"] == month))].copy()
    merged = pd.concat([bdf_all, out_month], ignore_index=True)
    ws_write_df("budgets_monthly", merged, BUDGET_COLS)


@st.cache_data(show_spinner=False)
def load_fixed() -> pd.DataFrame:
    fdf = ws_read_df("fixed_expenses", FIXED_COLS)
    if len(fdf):
        fdf["fixed_id"] = fdf["fixed_id"].fillna("").astype(str)
        mask = (fdf["fixed_id"].str.strip() == "")
        if mask.any():
            fdf.loc[mask, "fixed_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
        fdf["amount"] = pd.to_numeric(fdf["amount"], errors="coerce").fillna(0).astype(int)
        fdf["day"] = pd.to_numeric(fdf["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)
        fdf["memo"] = fdf["memo"].fillna("")
        fdf["name"] = fdf["name"].fillna("")
        save_fixed(fdf)
        return fdf.reset_index(drop=True)
    return pd.DataFrame(columns=FIXED_COLS)


def save_fixed(fdf: pd.DataFrame) -> None:
    out = fdf.copy()
    for col in FIXED_COLS:
        if col not in out.columns:
            out[col] = "" if col in ["fixed_id", "name", "memo"] else 0
    out["fixed_id"] = out["fixed_id"].fillna("").astype(str)
    mask = (out["fixed_id"].str.strip() == "")
    if mask.any():
        out.loc[mask, "fixed_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
    out["name"] = out["name"].fillna("").astype(str)
    out["memo"] = out["memo"].fillna("").astype(str)
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    out["day"] = pd.to_numeric(out["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)
    out = out[out["name"].str.strip() != ""].copy()
    ws_write_df("fixed_expenses", out, FIXED_COLS)


@st.cache_data(show_spinner=False)
def load_simple_money_log(ws_title: str) -> pd.DataFrame:
    df = ws_read_df(ws_title, SIMPLE_COLS)
    if len(df):
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["type"] = df["type"].fillna("")
        df["memo"] = df["memo"].fillna("")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
    return df


def save_simple_money_log(ws_title: str, df: pd.DataFrame) -> None:
    out = df.copy()
    if "id" not in out.columns:
        out.insert(0, "id", [str(uuid.uuid4()) for _ in range(len(out))])
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["type"] = out["type"].fillna("")
    out["memo"] = out["memo"].fillna("")
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    ws_write_df(ws_title, out, SIMPLE_COLS)


# ============================================================
# 예산 표 HTML (초과 행 배경 강조)
# ============================================================
def render_budget_table_html(df: pd.DataFrame) -> str:
    style = """
    <style>
      :root{
        --tbl-bg: rgba(255,255,255,0.92);
        --tbl-border: rgba(60,60,67,0.12);
        --tbl-head-bg: rgba(60,60,67,0.06);
        --tbl-text: #111;
        --tbl-sub: rgba(60,60,67,0.72);
        --danger: #FF3B30;
        --danger-soft: rgba(255,59,48,0.12);
        --radius: 16px;
        --shadow: 0 10px 22px rgba(0,0,0,0.05);
      }
      @media (prefers-color-scheme: dark) {
        :root{
          --tbl-bg: rgba(28,28,30,0.92);
          --tbl-border: rgba(255,255,255,0.12);
          --tbl-head-bg: rgba(255,255,255,0.08);
          --tbl-text: #fff;
          --tbl-sub: rgba(235,235,245,0.65);
          --danger: #FF453A;
          --danger-soft: rgba(255,69,58,0.18);
          --shadow: 0 14px 26px rgba(0,0,0,0.45);
        }
      }
      .budget-table-wrap { width: 100%; overflow-x: auto; }
      table.budget-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-variant-numeric: tabular-nums;
        color: var(--tbl-text);
        background: var(--tbl-bg);
        border: 1px solid var(--tbl-border);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        overflow: hidden;
      }
      table.budget-table th, table.budget-table td {
        border-bottom: 1px solid var(--tbl-border);
        padding: 10px 12px;
        font-size: 0.95rem;
        white-space: nowrap;
        background: transparent;
      }
      table.budget-table tr:last-child td { border-bottom: none; }
      table.budget-table th {
        background: var(--tbl-head-bg);
        text-align: left;
        font-weight: 700;
        color: var(--tbl-sub);
      }
      td.num { text-align: right; }
      td.diff-neg { color: var(--danger); font-weight: 800; }
      td.diff-pos { font-weight: 650; }
      tr.overspent td{ background: var(--danger-soft); }
    </style>
    """

    rows_html = []
    for _, r in df.iterrows():
        cat = html_escape(r["카테고리"])
        budget = int(pd.to_numeric(r["목표(원)"], errors="coerce") or 0)
        spent = int(pd.to_numeric(r["실제지출(원)"], errors="coerce") or 0)
        diff = int(pd.to_numeric(r["차액(원)"], errors="coerce") or 0)
        status = html_escape(r["상태"])
        diff_class = "diff-neg" if diff < 0 else "diff-pos"
        tr_class = "overspent" if diff < 0 else ""
        rows_html.append(
            f"""
            <tr class="{tr_class}">
              <td>{cat}</td>
              <td class="num">{budget:,}</td>
              <td class="num">{spent:,}</td>
              <td class="num {diff_class}">{diff:,}</td>
              <td>{status}</td>
            </tr>
            """
        )

    return f"""
    {style}
    <div class="budget-table-wrap">
      <table class="budget-table">
        <thead>
          <tr>
            <th>카테고리</th>
            <th>목표(원)</th>
            <th>실제지출(원)</th>
            <th>차액(원)</th>
            <th>상태</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """


# ============================================================
# 엑셀 다운로드
# ============================================================
def make_excel_bytes(
    selected_year: int,
    selected_month: int,
    ledger_df: pd.DataFrame,
    budget_df: pd.DataFrame,
    fixed_df: pd.DataFrame,
    event_df: pd.DataFrame,
    zeropay_df: pd.DataFrame,
) -> bytes:
    start_d, end_d, _ = month_range(selected_year, selected_month)

    def month_filter(df: pd.DataFrame, date_col: str = "date"):
        out = df.copy()
        if len(out) == 0:
            return out
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
        return out[(out[date_col] >= pd.Timestamp(start_d)) & (out[date_col] <= pd.Timestamp(end_d))].copy()

    month_ledger = month_filter(ledger_df, "date")
    month_event = month_filter(event_df, "date")
    month_zeropay = month_filter(zeropay_df, "date")

    inc = int(month_ledger.loc[month_ledger["type"] == "수입", "amount"].sum()) if len(month_ledger) else 0
    exp = int(month_ledger.loc[month_ledger["type"] == "지출", "amount"].sum()) if len(month_ledger) else 0
    bal = inc - exp
    summary = pd.DataFrame([{"연도": selected_year, "월": selected_month, "수입합계": inc, "지출합계": exp, "차액": bal}])

    exp_month = month_ledger[month_ledger["type"] == "지출"].copy() if len(month_ledger) else pd.DataFrame(columns=month_ledger.columns)
    spent_by_cat = exp_month.groupby("category")["amount"].sum().to_dict() if len(exp_month) else {}

    b = budget_df.copy()
    b["spent"] = b["category"].map(spent_by_cat).fillna(0).astype(int)
    b["diff"] = (b["budget"] - b["spent"]).astype(int)
    b["status"] = b["diff"].apply(lambda x: "남음" if x >= 0 else "초과")
    budget_status = b.rename(columns={"category": "카테고리", "budget": "목표(원)", "spent": "실제지출(원)", "diff": "차액(원)", "status": "상태"})

    if len(month_ledger):
        out_ledger = month_ledger.copy()
        out_ledger["date"] = pd.to_datetime(out_ledger["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("0000-00-00")
        out_ledger = out_ledger.drop(columns=["id", "fixed_key"], errors="ignore")
        out_ledger = out_ledger.sort_values("date")
    else:
        out_ledger = month_ledger.drop(columns=["id", "fixed_key"], errors="ignore")

    fixed_clean = fixed_df.drop(columns=["fixed_id"], errors="ignore")

    if len(month_event):
        month_event = month_event.copy()
        month_event["date"] = pd.to_datetime(month_event["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("0000-00-00")
        month_event = month_event.drop(columns=["id"], errors="ignore")

    if len(month_zeropay):
        month_zeropay = month_zeropay.copy()
        month_zeropay["date"] = pd.to_datetime(month_zeropay["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("0000-00-00")
        month_zeropay = month_zeropay.drop(columns=["id"], errors="ignore")

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="요약")
        out_ledger.to_excel(writer, index=False, sheet_name="가계부(선택월)")
        budget_status.to_excel(writer, index=False, sheet_name="예산현황(선택월)")
        fixed_clean.to_excel(writer, index=False, sheet_name="고정지출(설정)")
        month_event.to_excel(writer, index=False, sheet_name="경조사비(선택월)")
        month_zeropay.to_excel(writer, index=False, sheet_name="제로페이(선택월)")
    return bio.getvalue()


# ============================================================
# ✅ 모바일 최적화: 탭 대신 사이드바 메뉴
# ============================================================
st.title("나의 가계부")

page = st.sidebar.radio(
    "메뉴",
    ["가계부", "예산 설정", "고정지출", "경조사비", "제로페이"],
)

# ============================================================
# 1) 가계부
# ============================================================
if page == "가계부":
    st.subheader("내역 입력")

    # 한 줄(가로) + 메모는 아래 한 줄
    with st.form("ledger_entry_form_horizontal"):
        c_type, c_date, c_cat, c_amt, c_btn = st.columns([1.0, 1.25, 1.6, 1.0, 0.9])

        with c_type:
            entry_type = st.selectbox("구분", ["지출", "수입"], key="ledger_entry_type")

        category_options = expense_categories if entry_type == "지출" else income_categories

        with c_date:
            entry_date = st.date_input("날짜", value=date.today(), key="ledger_date")

        with c_cat:
            category = st.selectbox("카테고리", category_options, key="ledger_category")

        with c_amt:
            amt_str = st.text_input("금액(원)", value="0", key="ledger_amount_str", help="예: 12,000")

        with c_btn:
            submitted = st.form_submit_button("추가", use_container_width=True)

        memo = st.text_input("메모(선택)", key="ledger_memo")

    if submitted:
        ledger_df = load_ledger()
        amt = to_int_money(amt_str, 0)
        new_row = {
            "id": str(uuid.uuid4()),
            "date": pd.Timestamp(entry_date),
            "type": entry_type,
            "category": category,
            "amount": int(amt),
            "memo": memo,
            "fixed_key": ""
        }
        ledger_df = pd.concat([ledger_df, pd.DataFrame([new_row])], ignore_index=True)
        save_ledger(ledger_df)
        clear_cache_and_rerun("추가되었습니다!")

    st.divider()

    st.subheader("선택 월")
    selected_year, selected_month = month_selector("main")

    ledger_df = load_ledger()
    event_df = load_simple_money_log("events")
    zeropay_df = load_simple_money_log("zeropay")

    start_date, end_date, _ = month_range(selected_year, selected_month)
    month_ledger = ledger_df.copy()
    if len(month_ledger):
        month_ledger = month_ledger[
            (month_ledger["date"] >= pd.Timestamp(start_date)) & (month_ledger["date"] <= pd.Timestamp(end_date))
        ].copy()

    st.subheader("요약 (선택 월 기준)")
    income_sum = int(month_ledger.loc[month_ledger["type"] == "수입", "amount"].sum()) if len(month_ledger) else 0
    expense_sum = int(month_ledger.loc[month_ledger["type"] == "지출", "amount"].sum()) if len(month_ledger) else 0
    balance = income_sum - expense_sum

    items = [
        {"label": "수입 합계", "value": f"{income_sum:,}원", "help": "선택월 수입 합계"},
        {"label": "지출 합계", "value": f"{expense_sum:,}원", "help": "선택월 지출 합계"},
        {"label": "잔액", "value": f"{balance:,}원", "help": "선택월 기준 (수입-지출)", "tone": "danger" if balance < 0 else "normal"},
    ]
    st.markdown(render_ios_summary_cards(items), unsafe_allow_html=True)

    st.divider()

    st.subheader("예산 현황 (실제 지출 대비 차액)")
    expense_month = month_ledger[(month_ledger["type"] == "지출") & (month_ledger["category"].isin(budget_categories))].copy()
    spent_by_cat = expense_month.groupby("category")["amount"].sum().to_dict() if len(expense_month) else {}

    budget_current = load_budget_month(selected_year, selected_month)
    budget_current["spent"] = budget_current["category"].map(spent_by_cat).fillna(0).astype(int)
    budget_current["diff"] = (budget_current["budget"] - budget_current["spent"]).astype(int)
    budget_current["status"] = budget_current["diff"].apply(lambda x: "남음" if x >= 0 else "초과")

    show_df = budget_current[["category", "budget", "spent", "diff", "status"]].copy()
    show_df.columns = ["카테고리", "목표(원)", "실제지출(원)", "차액(원)", "상태"]
    components.html(render_budget_table_html(show_df), height=dynamic_table_height(len(show_df)), scrolling=True)

    st.divider()

    st.subheader("엑셀 다운로드")
    excel_bytes = make_excel_bytes(
        selected_year=selected_year,
        selected_month=selected_month,
        ledger_df=ledger_df,
        budget_df=budget_current[["category", "budget"]],
        fixed_df=load_fixed(),
        event_df=event_df,
        zeropay_df=zeropay_df,
    )
    st.download_button(
        label="선택 월 데이터 엑셀 다운로드",
        data=excel_bytes,
        file_name=f"가계부_{selected_year}-{selected_month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


# ============================================================
# 2) 예산 설정
# ============================================================
elif page == "예산 설정":
    st.subheader("지출 예산 설정 (월별)")
    budget_year, budget_month = month_selector("budget")

    bdf = load_budget_month(budget_year, budget_month)
    bview = bdf.copy()
    bview["budget_str"] = bview["budget"].apply(money_str)
    bview = bview[["category", "budget_str"]].copy()

    edited_bdf = st.data_editor(
        bview,
        hide_index=True,
        use_container_width=True,
        column_config={
            "category": st.column_config.TextColumn("카테고리", disabled=True),
            "budget_str": st.column_config.TextColumn("목표 금액(원)"),
        },
        key="budget_editor",
    )

    if st.button("예산 저장", key="save_budget_btn"):
        out = edited_bdf.copy()
        out["budget"] = out["budget_str"].apply(lambda x: to_int_money(x, 0))
        out = out[["category", "budget"]]
        save_budget_month(out, budget_year, budget_month)
        clear_cache_and_rerun(f"{budget_year}년 {budget_month}월 예산이 저장되었습니다!")


# ============================================================
# 3) 고정지출
# ============================================================
elif page == "고정지출":
    st.subheader("고정지출 설정")
    fdf = load_fixed()
    if len(fdf) == 0:
        fdf = pd.DataFrame([{
            "fixed_id": str(uuid.uuid4()),
            "name": "예: 월세",
            "amount": 0,
            "day": 1,
            "memo": ""
        }])

    original_ids = fdf["fixed_id"].astype(str).tolist()

    fview = fdf.copy()
    fview["amount_str"] = fview["amount"].apply(money_str)
    fview = fview[["name", "amount_str", "day", "memo"]].copy()

    edited_fixed = st.data_editor(
        fview,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "name": st.column_config.TextColumn("이름"),
            "amount_str": st.column_config.TextColumn("금액(원)"),
            "day": st.column_config.NumberColumn("지출일(1~31)", min_value=1, max_value=31, step=1),
            "memo": st.column_config.TextColumn("메모"),
        },
        key="fixed_editor",
    )

    if st.button("고정지출 저장", key="fixed_save"):
        saved = edited_fixed.copy()
        saved["name"] = saved["name"].fillna("").astype(str)
        saved["memo"] = saved["memo"].fillna("").astype(str)
        saved["amount"] = saved["amount_str"].apply(lambda x: to_int_money(x, 0))
        saved["day"] = pd.to_numeric(saved["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)

        fixed_ids = []
        for i in range(len(saved)):
            fixed_ids.append(original_ids[i] if i < len(original_ids) else str(uuid.uuid4()))

        saved.insert(0, "fixed_id", fixed_ids)
        saved = saved[FIXED_COLS].copy()

        save_fixed(saved)
        clear_cache_and_rerun("고정지출이 저장되었습니다!")


# ============================================================
# 4) 경조사비 / 5) 제로페이 (가로 폼 + 메모 아래)
# ============================================================
def simple_log_page(title: str, ws_title: str, state_key: str):
    st.subheader(title)
    df = load_simple_money_log(ws_title)

    with st.form(f"{state_key}_form"):
        c_type, c_date, c_amt, c_btn = st.columns([1.0, 1.25, 1.2, 0.9])

        with c_type:
            t = st.selectbox("구분", ["지출", "수입"], key=f"{state_key}_type")
        with c_date:
            d = st.date_input("날짜", value=date.today(), key=f"{state_key}_date")
        with c_amt:
            amt_str = st.text_input("금액(원)", value="0", key=f"{state_key}_amount_str", help="예: 50,000")
        with c_btn:
            ok = st.form_submit_button("추가", use_container_width=True)

        memo = st.text_input("메모(선택)", value="", key=f"{state_key}_memo")

    if ok:
        df = load_simple_money_log(ws_title)
        new_row = {
            "id": str(uuid.uuid4()),
            "date": pd.Timestamp(d),
            "type": t,
            "amount": to_int_money(amt_str, 0),
            "memo": memo
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        save_simple_money_log(ws_title, df)
        clear_cache_and_rerun("추가되었습니다!")

    st.divider()
    st.subheader("내역 (전체)")

    if len(df) == 0:
        st.info("기록이 없어요.")
        return

    view = df.copy()
    view["date"] = pd.to_datetime(view["date"], errors="coerce").dt.date
    view["amount_str"] = view["amount"].apply(money_str)
    view = view[["id", "date", "type", "amount_str", "memo"]].copy()
    view.insert(0, "삭제", False)
    view = view.set_index("id")

    edited = st.data_editor(
        view,
        hide_index=True,
        use_container_width=True,
        column_config={
            "삭제": st.column_config.CheckboxColumn("삭제"),
            "date": st.column_config.DateColumn("날짜"),
            "type": st.column_config.SelectboxColumn("구분", options=["지출", "수입"]),
            "amount_str": st.column_config.TextColumn("금액(원)"),
            "memo": st.column_config.TextColumn("메모"),
        },
        key=f"{state_key}_editor",
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("변경 저장", key=f"{state_key}_save"):
            df0 = load_simple_money_log(ws_title)
            ed = edited.reset_index()
            delete_ids = ed.loc[ed["삭제"] == True, "id"].astype(str).tolist()

            keep = ed[ed["삭제"] != True].copy()
            keep["amount"] = keep["amount_str"].apply(lambda x: to_int_money(x, 0))
            keep["date"] = pd.to_datetime(keep["date"], errors="coerce")
            keep["memo"] = keep["memo"].fillna("")
            keep["type"] = keep["type"].fillna("")

            updated = df0.set_index("id").copy()
            keep = keep.set_index("id")
            common = updated.index.intersection(keep.index)
            updated.loc[common, ["date", "type", "amount", "memo"]] = keep.loc[
                common, ["date", "type", "amount", "memo"]
            ].values

            if delete_ids:
                updated = updated.drop(index=delete_ids, errors="ignore")

            df2 = updated.reset_index()
            save_simple_money_log(ws_title, df2)
            clear_cache_and_rerun("저장되었습니다!")

    with col_b:
        if st.button("선택 삭제", key=f"{state_key}_delete"):
            df0 = load_simple_money_log(ws_title)
            ed = edited.reset_index()
            delete_ids = ed.loc[ed["삭제"] == True, "id"].astype(str).tolist()
            if not delete_ids:
                st.warning("삭제할 항목을 체크해 주세요.")
            else:
                df2 = df0[~df0["id"].astype(str).isin(delete_ids)].copy()
                save_simple_money_log(ws_title, df2)
                clear_cache_and_rerun(f"{len(delete_ids)}개 항목을 삭제했습니다.")


if page == "경조사비":
    simple_log_page("경조사비", "events", "event")

elif page == "제로페이":
    simple_log_page("제로페이", "zeropay", "zeropay")

st.caption("Made by Gayoung")
