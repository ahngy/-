import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import date
import uuid
import calendar
import re
from io import BytesIO
import textwrap

# ✅ Google Sheets
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# 기본 설정
# ============================================================
st.set_page_config(page_title="가계부", layout="centered")

# ============================================================
# 🔐 로그인 (users가 있으면 아이디/비번, 없으면 기존 단일 비번)
# ============================================================
from collections.abc import Mapping

PASSWORD = st.secrets.get("app", {}).get("password", "ab190427")  # fallback 단일 비번
USERS = st.secrets.get("users", {})  # [users] 섹션이 없으면 {}

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "current_user" not in st.session_state:
    st.session_state.current_user = ""

def current_user() -> str:
    u = str(st.session_state.current_user or "").strip()
    return u if u else "me"

def do_logout():
    st.session_state.authenticated = False
    st.session_state.current_user = ""
    st.rerun()

has_users = isinstance(USERS, Mapping) and len(USERS) > 0

if not st.session_state.authenticated:
    st.title("🔒 가계부 로그인")

    if has_users:
        username = st.text_input("아이디", value="", key="login_username")
        pw = st.text_input("비밀번호", type="password", key="login_password")
        login = st.button("로그인", use_container_width=True, key="login_btn")

        if login:
            if username in USERS and pw.strip() == str(USERS[username]).strip():
                st.session_state.authenticated = True
                st.session_state.current_user = username
                st.success("로그인 성공!")
                st.rerun()
            else:
                st.error("아이디/비밀번호가 틀렸어요.")
    else:
        pw = st.text_input("비밀번호를 입력하세요", type="password", key="login_password_only")
        login = st.button("로그인", use_container_width=True, key="login_btn_only")

        if login:
            if pw.strip() == str(PASSWORD).strip():
                st.session_state.authenticated = True
                st.session_state.current_user = "me"
                st.success("로그인 성공!")
                st.rerun()
            else:
                st.error("비밀번호가 틀렸어요.")

    st.stop()

st.title("나의 가계부")

# ============================================================
# Google Sheets 연결
# ============================================================
GSHEET_ID = st.secrets["gsheets"]["spreadsheet_id"]
SA_INFO = dict(st.secrets["gcp_service_account"])

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

def get_or_create_worksheet(title: str, rows: int = 4000, cols: int = 40):
    """Worksheet를 가져오되, '정말 없을 때만' 생성합니다.

    기존 코드는 모든 예외를 잡고 add_worksheet를 시도해서,
    일시적 API 오류/권한 문제/네트워크 오류가 발생해도 '없다'고 오판하여
    중복 생성(또는 '이미 존재' 오류)을 유발할 수 있습니다.
    """
    from gspread.exceptions import WorksheetNotFound, APIError

    sh = get_spreadsheet()

    # 1) 정상적으로 존재하면 바로 반환
    try:
        return sh.worksheet(title)
    except WorksheetNotFound:
        pass  # 없으면 생성 시도
    except APIError as e:
        # API 자체 오류는 '시트 없음'이 아니므로 그대로 올려서 원인을 확인하게 함
        raise
    except Exception:
        # 기타 예외도 '없음'으로 간주하지 않음
        raise

    # 2) 없을 때만 생성
    try:
        return sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))
    except APIError:
        # 동시 실행 등으로 이미 누군가 생성했을 수 있으니 한 번 더 가져오기
        return sh.worksheet(title)

def ws_read_df(ws_title: str, columns: list[str]) -> pd.DataFrame:
    ws = get_or_create_worksheet(ws_title)
    values = ws.get_all_values()

    if not values:
        return pd.DataFrame(columns=columns)

    header = values[0]
    data = values[1:]

    # 헤더가 비었거나 이상하면 강제로 columns 사용
    if len(header) == 0 or all(str(h).strip() == "" for h in header):
        header = columns

    df = pd.DataFrame(data, columns=header[: len(header)])

    # 누락 컬럼 보정
    for c in columns:
        if c not in df.columns:
            df[c] = ""

    return df[columns].copy()

def ws_write_df(ws_title: str, df: pd.DataFrame, columns: list[str]) -> None:
    #⚠️ 전체 덮어쓰기 (편집/삭제/설정 저장에서 사용)
    ws = get_or_create_worksheet(ws_title)
    out = df.copy()

    for c in columns:
        if c not in out.columns:
            out[c] = ""

    out = out[columns].fillna("")

    values = [columns] + out.astype(str).values.tolist()
    ws.clear()
    ws.update(values)

def ws_append_row(ws_title: str, row_dict: dict, columns: list[str]) -> None:
    # ✅ 빠른 추가(append) 저장 (동시 입력에도 강함)
    ws = get_or_create_worksheet(ws_title)

    # 헤더 없으면 생성
    existing = ws.get_all_values()
    if not existing:
        ws.update([columns])

    row = []
    for c in columns:
        v = row_dict.get(c, "")
        if v is None:
            v = ""
        row.append(str(v))
    ws.append_row(row, value_input_option="USER_ENTERED")

def ensure_columns(df: pd.DataFrame, cols: list[str], defaults: dict[str, object] | None = None) -> pd.DataFrame:
    """Ensure df has all cols in order; fill missing with defaults."""
    defaults = defaults or {}
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = defaults.get(c, "")
    return out[cols]

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
    "6. 교통/유류", "7. 의료", "8. 기타", "9. 고정지출", "10. 목돈지출"
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
# CSS (기존 + 모바일 iOS 최적화 추가)
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

      div[data-testid="stTabs"] button {
        border-radius: 999px !important;
        padding: 8px 14px !important;
      }

      div[data-testid="stDataFrame"] table tbody tr td,
      div[data-testid="stDataEditor"] div[role="row"] > div[role="gridcell"]{
        font-variant-numeric: tabular-nums;
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
        overflow: hidden;
      }
      .ios-label{
        font-size: 0.86rem;
        font-weight: 650;
        color: var(--subtext);
        letter-spacing: -0.2px;
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
        letter-spacing: -0.1px;
      }
      .ios-danger{ color: var(--danger); }

      /* ===== Mobile iOS tuning (추가) ===== */
      @media (max-width: 720px){
        section.main > div { padding-left: 10px; padding-right: 10px; }
        h1 { font-size: 1.35rem; }
        h2 { font-size: 1.15rem; }
        h3 { font-size: 1.05rem; }

        /* iOS 입력 줌 방지 */
        input, textarea { font-size: 16px !important; }

        /* Tabs touch */
        div[data-testid="stTabs"] button{
          padding: 10px 12px !important;
          font-size: 0.95rem !important;
        }

        /* Buttons touch */
        .stButton > button, .stDownloadButton > button{
          padding: 12px 14px !important;
          font-size: 1rem !important;
        }
      }
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

def dynamic_table_height(n_rows: int, base: int = 130, row_h: int = 36, min_h: int = 240, max_h: int = 700) -> int:
    h = base + n_rows * row_h
    return max(min_h, min(max_h, h))

def safe_key_part(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z가-힣_\-]", "", s)
    return s[:60] if len(s) > 60 else s

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
# ✅ Sheets "테이블" 컬럼 정의 (공동사용: user 추가)
# ============================================================
LEDGER_COLS = ["id", "date", "type", "category", "amount", "memo", "fixed_key", "user"]
BUDGET_COLS = ["year", "month", "category", "budget"]
FIXED_COLS = ["fixed_id", "name", "amount", "day", "memo"]
SIMPLE_COLS = ["id", "date", "type", "amount", "memo", "user"]
CARDS_COLS = ["card_name", "benefits"]
CARD_SUBS_COLS = ["card_name", "merchant", "amount", "day", "memo"]

def load_ledger() -> pd.DataFrame:
    df = ws_read_df("ledger", LEDGER_COLS)
    if len(df):
        df = ensure_columns(df, LEDGER_COLS, defaults={"amount": 0})
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["type"] = df["type"].fillna("").astype(str)
        df["category"] = df["category"].fillna("").astype(str)
        df["memo"] = df["memo"].fillna("").astype(str)
        df["id"] = df["id"].fillna("").astype(str)
        df["user"] = df["user"].fillna("").astype(str)
    return df
def save_ledger(df: pd.DataFrame) -> None:
    out = ensure_columns(df, LEDGER_COLS, defaults={"amount": 0})
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    ws_write_df("ledger", out, LEDGER_COLS)
def apply_fixed_to_ledger_for_month(ledger_df: pd.DataFrame, fixed_df: pd.DataFrame, year: int, month: int):
    if fixed_df is None or len(fixed_df) == 0:
        return ledger_df, 0

    out = ledger_df.copy()
    for col in LEDGER_COLS:
        if col not in out.columns:
            out[col] = "" if col != "amount" else 0

    existing_keys = set(out["fixed_key"].fillna("").astype(str).tolist())
    _, _, last_day = month_range(year, month)
    yyyymm = f"{year}{month:02d}"

    add_rows = []
    for _, fx in fixed_df.iterrows():
        fixed_id = str(fx.get("fixed_id", "")).strip()
        if fixed_id == "":
            continue

        key = f"FIX_{fixed_id}_{yyyymm}"
        if key in existing_keys:
            continue

        name = str(fx.get("name", "")).strip()
        memo = str(fx.get("memo", "")).strip()
        amount = int(pd.to_numeric(fx.get("amount", 0), errors="coerce") or 0)

        day = int(pd.to_numeric(fx.get("day", 1), errors="coerce") or 1)
        day = max(1, min(day, last_day))
        d = date(year, month, day)

        full_memo = name
        if memo:
            full_memo = f"{name} ({memo})" if name else memo

        add_rows.append({
            "id": str(uuid.uuid4()),
            "date": pd.Timestamp(d),
            "type": "지출",
            "category": FIXED_CATEGORY,
            "amount": amount,
            "memo": f"[고정지출] {full_memo}".strip(),
            "fixed_key": key,
            "user": current_user(),
        })

    if not add_rows:
        return out, 0

    out = pd.concat([out, pd.DataFrame(add_rows)], ignore_index=True)
    return out, len(add_rows)

def apply_subs_to_ledger_for_month(ledger_df: pd.DataFrame, subs_df: pd.DataFrame, year: int, month: int):
    if subs_df is None or len(subs_df) == 0:
        return ledger_df, 0

    out = ledger_df.copy()
    for col in LEDGER_COLS:
        if col not in out.columns:
            out[col] = "" if col != "amount" else 0

    existing_keys = set(out["fixed_key"].fillna("").astype(str).tolist())
    _, _, last_day = month_range(year, month)
    yyyymm = f"{year}{month:02d}"

    add_rows = []
    for _, sb in subs_df.iterrows():
        card = str(sb.get("card_name", "")).strip()
        merchant = str(sb.get("merchant", "")).strip()
        memo = str(sb.get("memo", "")).strip()
        amount = int(pd.to_numeric(sb.get("amount", 0), errors="coerce") or 0)
        day = int(pd.to_numeric(sb.get("day", 1), errors="coerce") or 1)

        if merchant.strip() == "":
            continue
        if amount == 0:
            continue

        day = max(1, min(day, last_day))
        d = date(year, month, day)

        key = f"SUB_{safe_key_part(card)}_{safe_key_part(merchant)}_{yyyymm}"
        if key in existing_keys:
            continue

        base = f"{merchant}"
        if card:
            base = f"{merchant} - {card}"
        full_memo = f"[정기결제] {base}"
        if memo:
            full_memo = f"{full_memo} ({memo})"

        add_rows.append({
            "id": str(uuid.uuid4()),
            "date": pd.Timestamp(d),
            "type": "지출",
            "category": FIXED_CATEGORY,
            "amount": amount,
            "memo": full_memo,
            "fixed_key": key,
            "user": current_user(),
        })

    if not add_rows:
        return out, 0

    out = pd.concat([out, pd.DataFrame(add_rows)], ignore_index=True)
    return out, len(add_rows)

# ============================================================
# 예산/고정/경조사/제로페이/카드
# ============================================================
@st.cache_data(show_spinner=False, ttl=60)
def load_budget_month(expense_cats: list[str], year: int, month: int) -> pd.DataFrame:
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
        bdf = pd.DataFrame({"category": expense_cats, "budget": 0})
    else:
        bdf = bdf[["category", "budget"]].copy()

    bdf["category"] = bdf["category"].fillna("").astype(str).str.strip()
    bdf = bdf[bdf["category"].isin(expense_cats)].copy()

    missing = [c for c in expense_cats if c not in set(bdf["category"])]
    if missing:
        bdf = pd.concat([bdf, pd.DataFrame({"category": missing, "budget": [0] * len(missing)})], ignore_index=True)

    bdf["budget"] = pd.to_numeric(bdf["budget"], errors="coerce").fillna(0).astype(int)
    bdf["__ord"] = bdf["category"].apply(lambda x: expense_cats.index(x) if x in expense_cats else 9999)
    bdf = bdf.sort_values("__ord").drop(columns="__ord").reset_index(drop=True)
    return bdf

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

@st.cache_data(show_spinner=False, ttl=60)
def load_fixed() -> pd.DataFrame:
    df = ws_read_df("fixed_expenses", FIXED_COLS)
    if len(df):
        df = ensure_columns(df, FIXED_COLS, defaults={"amount": 0})
        df["fixed_id"] = df["fixed_id"].fillna("").astype(str)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
        df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)
        df["category"] = df["category"].fillna("").astype(str)
        df["memo"] = df["memo"].fillna("").astype(str)
        df["active"] = df["active"].fillna("Y").astype(str)
        df["user"] = df["user"].fillna("").astype(str)
    return df
def save_fixed(fdf: pd.DataFrame) -> None:
    out = ensure_columns(fdf, FIXED_COLS, defaults={"amount": 0})
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    ws_write_df("fixed_expenses", out, FIXED_COLS)
def load_simple_money_log(ws_title: str) -> pd.DataFrame:
    df = ws_read_df(ws_title, SIMPLE_COLS)
    if len(df):
        df = ensure_columns(df, SIMPLE_COLS, defaults={"amount": 0})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["type"] = df["type"].fillna("").astype(str)
        df["category"] = df["category"].fillna("").astype(str)
        df["memo"] = df["memo"].fillna("").astype(str)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
        df["id"] = df["id"].fillna("").astype(str)
        df["user"] = df["user"].fillna("").astype(str)
    return df
def save_simple_money_log(ws_title: str, df: pd.DataFrame) -> None:
    out = ensure_columns(df, SIMPLE_COLS, defaults={"amount": 0})
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    ws_write_df(ws_title, out, SIMPLE_COLS)
def load_cards() -> pd.DataFrame:
    df = ws_read_df("cards", CARDS_COLS)
    df["card_name"] = df["card_name"].fillna("").astype(str)
    df["benefits"] = df["benefits"].fillna("").astype(str)
    df = df[df["card_name"].str.strip() != ""].copy()
    return df.reset_index(drop=True)

def save_cards(df: pd.DataFrame) -> None:
    out = df.copy()
    out["card_name"] = out["card_name"].fillna("").astype(str)
    out["benefits"] = out["benefits"].fillna("").astype(str)
    out = out[out["card_name"].str.strip() != ""].copy()
    ws_write_df("cards", out[CARDS_COLS], CARDS_COLS)

@st.cache_data(show_spinner=False, ttl=60)
def load_card_subs() -> pd.DataFrame:
    df = ws_read_df("card_subscriptions", CARD_SUBS_COLS)
    df["card_name"] = df["card_name"].fillna("").astype(str)
    df["merchant"] = df["merchant"].fillna("").astype(str)
    df["memo"] = df["memo"].fillna("").astype(str)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0).astype(int)
    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)
    df = df[df["merchant"].str.strip() != ""].copy()
    return df.reset_index(drop=True)

def save_card_subs(df: pd.DataFrame) -> None:
    out = df.copy()
    for col in CARD_SUBS_COLS:
        if col not in out.columns:
            out[col] = "" if col in ["card_name", "merchant", "memo"] else 0

    out["card_name"] = out["card_name"].fillna("").astype(str)
    out["merchant"] = out["merchant"].fillna("").astype(str)
    out["memo"] = out["memo"].fillna("").astype(str)
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce").fillna(0).astype(int)
    out["day"] = pd.to_numeric(out["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)

    out = out[out["merchant"].str.strip() != ""].copy()
    ws_write_df("card_subscriptions", out[CARD_SUBS_COLS], CARD_SUBS_COLS)

# ============================================================
# 엑셀 다운로드 (user는 제외해도 됨 — 원래 느낌 유지)
# ============================================================
def make_excel_bytes(
    selected_year: int,
    selected_month: int,
    ledger_df: pd.DataFrame,
    budget_df: pd.DataFrame,
    fixed_df: pd.DataFrame,
    subs_df: pd.DataFrame,
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
        out_ledger = out_ledger.drop(columns=["id", "fixed_key", "user"], errors="ignore")
        out_ledger = out_ledger.sort_values("date")
    else:
        out_ledger = month_ledger.drop(columns=["id", "fixed_key", "user"], errors="ignore")

    fixed_clean = fixed_df.drop(columns=["fixed_id"], errors="ignore")

    if len(month_event):
        month_event = month_event.copy()
        month_event["date"] = pd.to_datetime(month_event["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("0000-00-00")
        month_event = month_event.drop(columns=["id", "user"], errors="ignore")

    if len(month_zeropay):
        month_zeropay = month_zeropay.copy()
        month_zeropay["date"] = pd.to_datetime(month_zeropay["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("0000-00-00")
        month_zeropay = month_zeropay.drop(columns=["id", "user"], errors="ignore")

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="요약")
        out_ledger.to_excel(writer, index=False, sheet_name="가계부(선택월)")
        budget_status.to_excel(writer, index=False, sheet_name="예산현황(선택월)")
        fixed_clean.to_excel(writer, index=False, sheet_name="고정지출(설정)")
        subs_df.to_excel(writer, index=False, sheet_name="정기결제(설정)")
        month_event.to_excel(writer, index=False, sheet_name="경조사비(선택월)")
        month_zeropay.to_excel(writer, index=False, sheet_name="제로페이(선택월)")
    return bio.getvalue()

# ============================================================
# 사이드바: 사용자/새로고침/로그아웃
# ============================================================
with st.sidebar:
    st.markdown("### 👤 현재 로그인")
    st.write(f"**{current_user()}**")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if st.button("새로고침", use_container_width=True):
            clear_cache_and_rerun()
    with col_s2:
        if st.button("로그아웃", use_container_width=True):
            do_logout()

# ============================================================
# 탭 구성
# ============================================================
tab_main, tab_budget, tab_fixed, tab_event, tab_zeropay, tab_card = st.tabs(
    ["가계부", "예산 설정", "고정지출", "경조사비", "제로페이", "신용카드"]
)

# ============================================================
# 1) 가계부 탭
# ============================================================
with tab_main:
    st.subheader("내역 입력")

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

    # ✅ 여기서부터 속도 개선 핵심: append로 바로 저장
    if submitted:
        amt = to_int_money(amt_str, 0)
        new_row = {
            "id": str(uuid.uuid4()),
            "date": str(entry_date),   # "YYYY-MM-DD"
            "type": entry_type,
            "category": category,
            "amount": int(amt),
            "memo": memo,
            "fixed_key": "",
            "user": current_user(),
        }
        ws_append_row("ledger", new_row, LEDGER_COLS)
        clear_cache_and_rerun("추가되었습니다!")

    st.divider()

    st.subheader("선택 월")
    selected_year, selected_month = month_selector("main")

    ledger_df = load_ledger()
    fixed_df = load_fixed()
    subs_df = load_card_subs()
    event_df = load_simple_money_log("events")
    zeropay_df = load_simple_money_log("zeropay")

    start_date, end_date, _ = month_range(selected_year, selected_month)
    month_ledger = ledger_df.copy()
    if len(month_ledger):
        month_ledger = month_ledger[
            (month_ledger["date"] >= pd.Timestamp(start_date)) & (month_ledger["date"] <= pd.Timestamp(end_date))
        ].copy()

    with st.expander("표시 옵션", expanded=False):
        show_cumulative = st.checkbox("월 누적 잔액(이전월 포함)으로 계산", value=False, key="opt_cumulative")
        show_only_mine = st.checkbox("내 기록만 보기", value=False, key="opt_only_mine")

    if show_only_mine and len(month_ledger):
        month_ledger = month_ledger[month_ledger["user"].fillna("").astype(str) == current_user()].copy()

    st.subheader("요약 (선택 월 기준)")
    income_sum = int(month_ledger.loc[month_ledger["type"] == "수입", "amount"].sum()) if len(month_ledger) else 0
    expense_sum = int(month_ledger.loc[month_ledger["type"] == "지출", "amount"].sum()) if len(month_ledger) else 0
    balance_month = income_sum - expense_sum

    if show_cumulative:
        upto = ledger_df.copy()
        if len(upto):
            upto = upto[upto["date"] <= pd.Timestamp(end_date)].copy()
        if show_only_mine and len(upto):
            upto = upto[upto["user"].fillna("").astype(str) == current_user()].copy()
        income_upto = int(upto.loc[upto["type"] == "수입", "amount"].sum()) if len(upto) else 0
        expense_upto = int(upto.loc[upto["type"] == "지출", "amount"].sum()) if len(upto) else 0
        balance = income_upto - expense_upto
        balance_label = "누적 잔액"
        balance_help = "이전월 포함, 선택월 말 기준"
    else:
        balance = balance_month
        balance_label = "잔액"
        balance_help = "선택월 기준 (수입-지출)"

    items = [
        {"label": "수입 합계", "value": f"{income_sum:,}원", "help": "선택월 수입 합계"},
        {"label": "지출 합계", "value": f"{expense_sum:,}원", "help": "선택월 지출 합계"},
        {"label": balance_label, "value": f"{balance:,}원", "help": balance_help, "tone": "danger" if balance < 0 else "normal"},
    ]
    st.markdown(render_ios_summary_cards(items), unsafe_allow_html=True)

    st.divider()

    col_fx_a, col_fx_b, col_fx_c = st.columns([2, 1, 1])
    with col_fx_a:
        st.caption("고정지출/정기결제는 버튼을 눌러서 해당 월에만 반영할 수 있어요. (중복 반영 방지됨)")
    with col_fx_b:
        if st.button("선택 월에 고정지출 반영", key="apply_fixed_btn_main"):
            ledger_df = load_ledger()
            fixed_df = load_fixed()
            ledger_df, added = apply_fixed_to_ledger_for_month(ledger_df, fixed_df, selected_year, selected_month)
            if added > 0:
                save_ledger(ledger_df)
                clear_cache_and_rerun(f"{selected_year}년 {selected_month}월 고정지출 {added}건 반영 완료!")
            else:
                st.info("추가로 반영할 고정지출이 없어요. (이미 반영되었을 수 있어요)")
                st.rerun()

    with col_fx_c:
        if st.button("선택 월에 정기결제 반영", key="apply_subs_btn_main"):
            ledger_df = load_ledger()
            subs_df = load_card_subs()
            ledger_df, added = apply_subs_to_ledger_for_month(ledger_df, subs_df, selected_year, selected_month)
            if added > 0:
                save_ledger(ledger_df)
                clear_cache_and_rerun(f"{selected_year}년 {selected_month}월 정기결제 {added}건 반영 완료!")
            else:
                st.info("추가로 반영할 정기결제가 없어요. (이미 반영되었을 수 있어요)")
                st.rerun()

    ledger_df = load_ledger()
    month_ledger = ledger_df.copy()
    if len(month_ledger):
        month_ledger = month_ledger[
            (month_ledger["date"] >= pd.Timestamp(start_date)) & (month_ledger["date"] <= pd.Timestamp(end_date))
        ].copy()
    if show_only_mine and len(month_ledger):
        month_ledger = month_ledger[month_ledger["user"].fillna("").astype(str) == current_user()].copy()

    st.subheader("예산 현황 (실제 지출 대비 차액)")
    expense_month = month_ledger[(month_ledger["type"] == "지출") & (month_ledger["category"].isin(budget_categories))].copy()
    spent_by_cat = expense_month.groupby("category")["amount"].sum().to_dict() if len(expense_month) else {}

    budget_current = load_budget_month(budget_categories, selected_year, selected_month)
    budget_current["spent"] = budget_current["category"].map(spent_by_cat).fillna(0).astype(int)
    budget_current["diff"] = (budget_current["budget"] - budget_current["spent"]).astype(int)
    budget_current["status"] = budget_current["diff"].apply(lambda x: "남음" if x >= 0 else "초과")

    total_budget = int(budget_current["budget"].sum())
    total_spent = int(budget_current["spent"].sum())
    total_diff = total_budget - total_spent

    cc1, cc2, cc3 = st.columns(3)
    cc1.metric("총 목표(예산)", f"{total_budget:,}원")
    cc2.metric("총 지출(선택월)", f"{total_spent:,}원")
    if total_diff < 0:
        cc3.markdown(f"**총 차액**  \n<span class='total-diff neg'>{total_diff:,}원</span>", unsafe_allow_html=True)
    else:
        cc3.markdown(f"**총 차액**  \n<span class='total-diff'>{total_diff:,}원</span>", unsafe_allow_html=True)

    show_df = budget_current[["category", "budget", "spent", "diff", "status"]].copy()
    show_df.columns = ["카테고리", "목표(원)", "실제지출(원)", "차액(원)", "상태"]
    components.html(render_budget_table_html(show_df), height=dynamic_table_height(len(show_df)), scrolling=True)

    st.divider()

    st.subheader("내역 (선택 월 기준)")
    if len(month_ledger) == 0:
        st.info("선택한 월에는 기록이 없어요.")
    else:
        view = month_ledger.copy()
        view["date"] = pd.to_datetime(view["date"], errors="coerce").dt.date
        view["amount_str"] = view["amount"].apply(money_str)

        # user는 표시 안 해도 되지만, 공동사용이면 있으면 편해요(옵션)
        show_user_col = st.checkbox("작성자 표시", value=False, key="show_user_col")
        cols = ["id", "date", "type", "category", "amount_str", "memo"]
        if show_user_col:
            cols.append("user")

        view = view[cols].copy()
        view.insert(0, "삭제", False)
        view = view.set_index("id")

        column_config = {
            "삭제": st.column_config.CheckboxColumn("삭제"),
            "date": st.column_config.DateColumn("날짜"),
            "type": st.column_config.SelectboxColumn("구분", options=["지출", "수입"]),
            "category": st.column_config.SelectboxColumn("카테고리", options=all_categories),
            "amount_str": st.column_config.TextColumn("금액(원)"),
            "memo": st.column_config.TextColumn("메모"),
        }
        if show_user_col:
            column_config["user"] = st.column_config.TextColumn("작성자", disabled=True)

        edited = st.data_editor(
            view,
            hide_index=True,
            use_container_width=True,
            column_config=column_config,
            key="ledger_editor",
        )

        col_a, col_b = st.columns([1, 1])

        with col_a:
            if st.button("변경 저장", key="ledger_save"):
                ledger_df = load_ledger()

                ed = edited.reset_index()
                delete_ids = ed.loc[ed["삭제"] == True, "id"].astype(str).tolist()

                keep = ed[ed["삭제"] != True].copy()
                keep["amount"] = keep["amount_str"].apply(lambda x: to_int_money(x, 0))
                keep["date"] = pd.to_datetime(keep["date"], errors="coerce")
                keep["memo"] = keep.get("memo", "").fillna("")
                keep["type"] = keep["type"].fillna("")
                keep["category"] = keep["category"].fillna("")

                updated = ledger_df.set_index("id").copy()
                keep = keep.set_index("id")
                common = updated.index.intersection(keep.index)

                updated.loc[common, ["date", "type", "category", "amount", "memo"]] = keep.loc[
                    common, ["date", "type", "category", "amount", "memo"]
                ].values

                if delete_ids:
                    updated = updated.drop(index=delete_ids, errors="ignore")

                ledger_df2 = updated.reset_index()
                save_ledger(ledger_df2)
                clear_cache_and_rerun("저장되었습니다!")

        with col_b:
            if st.button("선택 삭제", key="ledger_delete"):
                ledger_df = load_ledger()
                ed = edited.reset_index()
                delete_ids = ed.loc[ed["삭제"] == True, "id"].astype(str).tolist()
                if not delete_ids:
                    st.warning("삭제할 항목을 체크해 주세요.")
                else:
                    ledger_df2 = ledger_df[~ledger_df["id"].astype(str).isin(delete_ids)].copy()
                    save_ledger(ledger_df2)
                    clear_cache_and_rerun(f"{len(delete_ids)}개 항목을 삭제했습니다.")

    st.divider()

    st.subheader("엑셀 다운로드")
    excel_bytes = make_excel_bytes(
        selected_year=selected_year,
        selected_month=selected_month,
        ledger_df=ledger_df,
        budget_df=budget_current[["category", "budget"]],
        fixed_df=fixed_df,
        subs_df=subs_df,
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
# 2) 예산 설정 탭
# ============================================================
with tab_budget:
    st.subheader("지출 예산 설정 (월별)")
    budget_year, budget_month = month_selector("budget")

    bdf = load_budget_month(budget_categories, budget_year, budget_month)
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
        out = out[out["category"].isin(budget_categories)].copy()
        save_budget_month(out, budget_year, budget_month)
        clear_cache_and_rerun(f"{budget_year}년 {budget_month}월 예산이 저장되었습니다!")

# ============================================================
# 3) 고정지출 탭
# ============================================================
with tab_fixed:
    st.subheader("고정지출 설정")
    st.caption(f"고정지출은 반영 시 모두 '{FIXED_CATEGORY}' 카테고리로 들어가며, 같은 월에 중복 추가되지 않아요.")

    fdf = load_fixed()
    if len(fdf) == 0:
        fdf = pd.DataFrame([{
            "fixed_id": str(uuid.uuid4()),
            "name": "예: 월세",
            "amount": 0,
            "day": 1,
            "memo": ""
        }])

    total_fixed_amount = int(pd.to_numeric(fdf.get("amount", 0), errors="coerce").fillna(0).sum()) if len(fdf) else 0
    st.metric("고정지출 총 금액(설정)", f"{total_fixed_amount:,}원")

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

        new_len = len(saved)
        fixed_ids = []
        for i in range(new_len):
            if i < len(original_ids):
                fixed_ids.append(original_ids[i])
            else:
                fixed_ids.append(str(uuid.uuid4()))

        saved.insert(0, "fixed_id", fixed_ids)
        saved = saved[FIXED_COLS].copy()

        save_fixed(saved)
        clear_cache_and_rerun("고정지출이 저장되었습니다!")

# ============================================================
# 4) 경조사비/제로페이 탭 (추가 저장은 append로 속도 개선)
# ============================================================
def simple_log_tab(title: str, ws_title: str, state_key: str):
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
        new_row = {
            "id": str(uuid.uuid4()),
            "date": str(d),
            "type": t,
            "amount": to_int_money(amt_str, 0),
            "memo": memo,
            "user": current_user(),
        }
        ws_append_row(ws_title, new_row, SIMPLE_COLS)
        clear_cache_and_rerun("추가되었습니다!")

    # 다시 로드
    df = load_simple_money_log(ws_title)

    st.divider()

    income_sum = int(df.loc[df["type"] == "수입", "amount"].sum()) if len(df) else 0
    expense_sum = int(df.loc[df["type"] == "지출", "amount"].sum()) if len(df) else 0
    bal = income_sum - expense_sum

    c1, c2, c3 = st.columns(3)
    c1.metric("수입 합계(전체)", f"{income_sum:,}원")
    c2.metric("지출 합계(전체)", f"{expense_sum:,}원")
    c3.metric("차액(전체)", f"{bal:,}원")

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

with tab_event:
    simple_log_tab("경조사비", "events", "event")

with tab_zeropay:
    simple_log_tab("제로페이", "zeropay", "zeropay")

# ============================================================
# 5) 신용카드 탭
# ============================================================
with tab_card:
    st.subheader("신용카드")

    st.markdown("### 카드 혜택 정리")
    cards_df = load_cards()
    if len(cards_df) == 0:
        cards_df = pd.DataFrame([{"card_name": "예: OO카드", "benefits": "예: 주유 5% / 커피 10%"}])

    edited_cards = st.data_editor(
        cards_df,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "card_name": st.column_config.TextColumn("카드명"),
            "benefits": st.column_config.TextColumn("혜택 정리"),
        },
        key="cards_editor",
    )

    if st.button("카드 혜택 저장", key="save_cards_btn"):
        save_cards(edited_cards)
        clear_cache_and_rerun("카드 혜택이 저장되었습니다!")

    st.divider()

    st.markdown("### 정기결제 관리 (카드별)")
    st.caption("정기결제는 '가계부 탭'에서 선택 월에만 반영할 수 있어요. (월별로 리스트 변동 가능)")

    cards_df = load_cards()
    card_names = [c for c in cards_df["card_name"].fillna("").astype(str).tolist() if c.strip() != ""]
    if not card_names:
        st.warning("먼저 위에서 카드명을 1개 이상 등록해 주세요.")
        st.stop()

    subs_df = load_card_subs()

    totals = (
        subs_df.groupby("card_name")["amount"].sum().sort_values(ascending=False).reset_index()
        if len(subs_df) else pd.DataFrame(columns=["card_name", "amount"])
    )

    total_all = int(subs_df["amount"].sum()) if len(subs_df) else 0
    st.metric("정기결제 총액", f"{total_all:,}원")

    if len(totals):
        t = totals.copy()
        t.columns = ["카드", "합계(원)"]
        t["합계(원)"] = t["합계(원)"].apply(money_str)
        st.dataframe(t, use_container_width=True, hide_index=True)

    st.divider()

    selected_card = st.selectbox("카드 선택", card_names, key="subs_card_select")

    card_only = subs_df[subs_df["card_name"] == selected_card].copy()
    card_total = int(card_only["amount"].sum()) if len(card_only) else 0
    st.metric(f"{selected_card} 정기결제 합계", f"{card_total:,}원")

    if len(card_only) == 0:
        card_only = pd.DataFrame([{
            "card_name": selected_card,
            "merchant": "예: 넷플릭스",
            "amount": 0,
            "day": 1,
            "memo": ""
        }])

    view = card_only.copy()
    view["amount_str"] = view["amount"].apply(money_str)
    view = view[["merchant", "amount_str", "day", "memo"]].copy()

    edited = st.data_editor(
        view,
        hide_index=True,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "merchant": st.column_config.TextColumn("정기결제명"),
            "amount_str": st.column_config.TextColumn("금액(원)"),
            "day": st.column_config.NumberColumn("결제일", min_value=1, max_value=31, step=1),
            "memo": st.column_config.TextColumn("메모"),
        },
        key="subs_editor_no_active",
    )

    if st.button("이 카드 정기결제 저장", key="save_subs_no_active"):
        subs_df = load_card_subs()

        out = edited.copy()
        out["card_name"] = selected_card
        out["merchant"] = out["merchant"].fillna("").astype(str)
        out["memo"] = out["memo"].fillna("").astype(str)
        out["amount"] = out["amount_str"].apply(lambda x: to_int_money(x, 0))
        out["day"] = pd.to_numeric(out["day"], errors="coerce").fillna(1).astype(int).clip(1, 31)

        out = out[out["merchant"].str.strip() != ""].copy()

        rest = subs_df[subs_df["card_name"] != selected_card].copy()
        merged = pd.concat([rest, out[["card_name", "merchant", "amount", "day", "memo"]]], ignore_index=True)

        save_card_subs(merged)
        clear_cache_and_rerun("저장되었습니다!")

st.caption("Made by Gayoung")
