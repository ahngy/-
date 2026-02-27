import streamlit as st
import pandas as pd
from datetime import date
import uuid, re, time, random
from io import BytesIO

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound

st.set_page_config(page_title="가계부", layout="wide")

# ----------------- Schemas -----------------
LEDGER_COLS = ["id","date","type","category","amount","memo","fixed_key","user"]
BUDGET_COLS = ["year","month","category","budget"]
FIXED_COLS  = ["fixed_id","name","amount","day","memo"]
SIMPLE_COLS = ["id","date","type","amount","memo","user"]
CARDS_COLS = ["card_name","benefits"]
CARD_SUBS_COLS = ["card_name","merchant","amount","day","memo"]

DEFAULT_EXPENSE_CATEGORIES = ["식비","카페/간식","교통","쇼핑","생활","의료","교육","여가","경조","기타"]
DEFAULT_INCOME_CATEGORIES  = ["월급","용돈","기타수입"]
FIXED_CATEGORY = "고정지출"

# ----------------- Style (kept) -----------------
st.markdown("""
<style>
.block-container{padding-top:1.2rem;padding-bottom:2rem;}
[data-testid="stTabs"] button{font-size:0.95rem;padding:0.35rem 0.8rem;}
.small-note{font-size:0.85rem;opacity:0.8;}
hr{margin:0.6rem 0 1.0rem 0;}
</style>
""", unsafe_allow_html=True)

# ----------------- Utils -----------------
def money_str(v) -> str:
    try: return f"{int(float(v)):,}"
    except: return "0"

def to_int_money(x, default=0) -> int:
    if x is None: return default
    if isinstance(x,(int,float)): return int(x)
    s = re.sub(r"[^\d\-]", "", str(x))
    if s in ("","-"): return default
    try: return int(s)
    except: return default

def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")

def parse_date_col(df: pd.DataFrame, col="date") -> pd.Series:
    return pd.to_datetime(df[col], errors="coerce")

def month_last_day(y: int, m: int) -> int:
    import calendar
    return calendar.monthrange(y, m)[1]

def current_user() -> str:
    u = str(st.session_state.get("user_name","")).strip()
    return u if u else "default"

def _cache_bust() -> int:
    return int(st.session_state.get("_cache_bust", 0))

def clear_cache_and_rerun(msg: str | None = None):
    st.session_state["_cache_bust"] = _cache_bust() + 1
    if msg: st.toast(msg)
    st.rerun()

def _is_quota_429(err: Exception) -> bool:
    s = str(err)
    return ("[429]" in s) or ("Quota exceeded" in s) or ("Read requests" in s)

def _with_retry(fn, tries=6):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if isinstance(e, APIError) and (_is_quota_429(e) or "[503]" in str(e) or "[500]" in str(e)):
                time.sleep(min(8.0, (2**i)*0.5 + random.random()))
                continue
            raise
    raise last

def _extract_sheet_id(v: str) -> str:
    v = (v or "").strip()
    if "/spreadsheets/d/" in v:
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", v)
        if m: return m.group(1)
    return v

def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if df is None or len(df)==0:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns: df[c] = ""
    return df[cols].copy()

# ----------------- Google Sheets -----------------
def _get_secrets():
    if "gsheets" not in st.secrets:
        st.error("secrets.toml에 [gsheets] 설정이 필요합니다.")
        st.stop()
    return st.secrets["gsheets"]

@st.cache_resource
def get_gspread_client():
    sa = st.secrets.get("gcp_service_account")
    if not sa:
        st.error("secrets.toml에 [gcp_service_account] 서비스 계정 정보가 필요합니다.")
        st.stop()
    scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(sa, scopes=scopes)
    return gspread.authorize(creds)

def get_spreadsheet():
    sec = _get_secrets()
    sid = _extract_sheet_id(sec.get("spreadsheet_id",""))
    if not sid:
        st.error("gsheets.spreadsheet_id가 비어있습니다. (시트 ID 또는 URL)")
        st.stop()
    gc = get_gspread_client()
    try:
        return _with_retry(lambda: gc.open_by_key(sid))
    except APIError as e:
        st.error("Google Sheets 연결 오류 (권한/쿼터/ID 확인 필요)")
        st.code(str(e))
        st.stop()

def get_or_create_worksheet(title: str, rows=2000, cols=30):
    sh = get_spreadsheet()
    try:
        return _with_retry(lambda: sh.worksheet(title))
    except WorksheetNotFound:
        try:
            _with_retry(lambda: sh.add_worksheet(title=title, rows=str(rows), cols=str(cols)))
        except Exception:
            pass
        return _with_retry(lambda: sh.worksheet(title))

@st.cache_data(ttl=60, show_spinner=False)
def _ws_read_df_cached(ws_title: str, cols: tuple[str,...], bust: int) -> pd.DataFrame:
    ws = get_or_create_worksheet(ws_title)
    values = _with_retry(lambda: ws.get_all_values())
    if not values:
        _with_retry(lambda: ws.update("A1", [list(cols)]))
        return pd.DataFrame(columns=list(cols))
    header = values[0]
    if header != list(cols):
        df = pd.DataFrame(values[1:], columns=header)
        return ensure_columns(df, list(cols))
    return pd.DataFrame(values[1:], columns=list(cols))

def ws_read_df(ws_title: str, cols: list[str]) -> pd.DataFrame:
    return _ws_read_df_cached(ws_title, tuple(cols), _cache_bust())

def ws_overwrite(ws_title: str, df: pd.DataFrame, cols: list[str]):
    ws = get_or_create_worksheet(ws_title)
    out = ensure_columns(df, cols).astype(str).fillna("")
    values = [cols] + out.values.tolist()
    _with_retry(lambda: ws.clear())
    _with_retry(lambda: ws.update("A1", values))

def ws_append_row(ws_title: str, row_dict: dict, cols: list[str]):
    ws = get_or_create_worksheet(ws_title)
    row = [str(row_dict.get(c,"")) for c in cols]
    _with_retry(lambda: ws.append_row(row, value_input_option="USER_ENTERED", table_range="A1"))

# ----------------- Data access -----------------
def load_ledger() -> pd.DataFrame:
    df = ws_read_df("ledger", LEDGER_COLS)
    df = ensure_columns(df, LEDGER_COLS)
    df["amount"] = df["amount"].apply(lambda x: to_int_money(x, 0))
    df["date"] = df["date"].fillna(today_str())
    df["memo"] = df["memo"].fillna("")
    df["type"] = df["type"].fillna("")
    df["category"] = df["category"].fillna("")
    df["fixed_key"] = df["fixed_key"].fillna("").astype(str)
    df["user"] = df["user"].replace("", current_user()).fillna(current_user())
    df["id"] = df["id"].replace("", pd.NA)
    df["id"] = df["id"].fillna(pd.Series([str(uuid.uuid4()) for _ in range(len(df))]))
    return df

def save_ledger(df: pd.DataFrame):
    out = ensure_columns(df, LEDGER_COLS).copy()
    out["amount"] = out["amount"].apply(lambda x: to_int_money(x, 0))
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date.astype(str)
    out["fixed_key"] = out["fixed_key"].fillna("").astype(str)
    out["user"] = out["user"].replace("", current_user()).fillna(current_user())
    ws_overwrite("ledger", out, LEDGER_COLS)

def append_ledger(d: date, typ: str, category: str, amount: int, memo: str, fixed_key: str=""):
    ws_append_row("ledger", {
        "id": str(uuid.uuid4()),
        "date": d.strftime("%Y-%m-%d"),
        "type": typ,
        "category": category,
        "amount": int(amount),
        "memo": memo or "",
        "fixed_key": fixed_key or "",
        "user": current_user(),
    }, LEDGER_COLS)

def load_fixed() -> pd.DataFrame:
    df = ws_read_df("fixed_expenses", FIXED_COLS)
    df = ensure_columns(df, FIXED_COLS)
    df["fixed_id"] = df["fixed_id"].fillna("").astype(str)
    df["amount"] = df["amount"].apply(lambda x: to_int_money(x, 0))
    df["day"] = df["day"].apply(lambda x: max(1, min(31, to_int_money(x, 1))))
    df["memo"] = df["memo"].fillna("").astype(str)
    df["name"] = df["name"].fillna("").astype(str)
    mask = df["fixed_id"].eq("")
    if mask.any():
        df.loc[mask, "fixed_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
    return df

def save_fixed(df: pd.DataFrame):
    out = ensure_columns(df, FIXED_COLS).copy()
    out["amount"] = out["amount"].apply(lambda x: to_int_money(x, 0))
    out["day"] = out["day"].apply(lambda x: max(1, min(31, to_int_money(x, 1))))
    out["fixed_id"] = out["fixed_id"].replace("", pd.NA)
    out["fixed_id"] = out["fixed_id"].fillna(pd.Series([str(uuid.uuid4()) for _ in range(len(out))]))
    ws_overwrite("fixed_expenses", out, FIXED_COLS)

def load_cards() -> pd.DataFrame:
    return ensure_columns(ws_read_df("cards", CARDS_COLS), CARDS_COLS)

def save_cards(df: pd.DataFrame):
    ws_overwrite("cards", ensure_columns(df, CARDS_COLS), CARDS_COLS)

def load_card_subs() -> pd.DataFrame:
    df = ensure_columns(ws_read_df("card_subscriptions", CARD_SUBS_COLS), CARD_SUBS_COLS)
    df["amount"] = df["amount"].apply(lambda x: to_int_money(x, 0))
    df["day"] = df["day"].apply(lambda x: max(1, min(31, to_int_money(x, 1))))
    return df

def save_card_subs(df: pd.DataFrame):
    out = ensure_columns(df, CARD_SUBS_COLS).copy()
    out["amount"] = out["amount"].apply(lambda x: to_int_money(x, 0))
    out["day"] = out["day"].apply(lambda x: max(1, min(31, to_int_money(x, 1))))
    ws_overwrite("card_subscriptions", out, CARD_SUBS_COLS)

def load_simple(ws_title: str) -> pd.DataFrame:
    df = ensure_columns(ws_read_df(ws_title, SIMPLE_COLS), SIMPLE_COLS)
    df["amount"] = df["amount"].apply(lambda x: to_int_money(x, 0))
    df["date"] = df["date"].fillna(today_str())
    df["user"] = df["user"].replace("", current_user()).fillna(current_user())
    df["id"] = df["id"].replace("", pd.NA)
    df["id"] = df["id"].fillna(pd.Series([str(uuid.uuid4()) for _ in range(len(df))]))
    return df

def append_simple(ws_title: str, d: date, typ: str, amount: int, memo: str):
    ws_append_row(ws_title, {
        "id": str(uuid.uuid4()),
        "date": d.strftime("%Y-%m-%d"),
        "type": typ,
        "amount": int(amount),
        "memo": memo or "",
        "user": current_user(),
    }, SIMPLE_COLS)

# ----------------- Budget -----------------
def load_budget_month(categories: list[str], y: int, m: int) -> pd.DataFrame:
    df = ensure_columns(ws_read_df("budgets_monthly", BUDGET_COLS), BUDGET_COLS)
    df["year"] = df["year"].apply(lambda x: to_int_money(x, 0))
    df["month"] = df["month"].apply(lambda x: to_int_money(x, 0))
    df["budget"] = df["budget"].apply(lambda x: to_int_money(x, 0))
    cur = df[(df["year"]==y) & (df["month"]==m)].copy()
    have = set(cur["category"].astype(str).tolist())
    for c in categories:
        if c not in have:
            cur = pd.concat([cur, pd.DataFrame([{"year":y,"month":m,"category":c,"budget":0}])], ignore_index=True)
    cur = cur[cur["category"].isin(categories)].copy()
    cur["category"] = pd.Categorical(cur["category"], categories=categories, ordered=True)
    return cur.sort_values("category").reset_index(drop=True)

def save_budget_month(df_cat_budget: pd.DataFrame, y: int, m: int):
    df_all = ensure_columns(ws_read_df("budgets_monthly", BUDGET_COLS), BUDGET_COLS)
    df_all["year"] = df_all["year"].apply(lambda x: to_int_money(x, 0))
    df_all["month"] = df_all["month"].apply(lambda x: to_int_money(x, 0))
    keep = df_all[~((df_all["year"]==y) & (df_all["month"]==m))].copy()
    add = df_cat_budget.copy()
    add["year"], add["month"] = y, m
    add["budget"] = add["budget"].apply(lambda x: to_int_money(x, 0))
    out = pd.concat([keep, add[BUDGET_COLS]], ignore_index=True)
    ws_overwrite("budgets_monthly", out, BUDGET_COLS)

# ----------------- Apply recurring -----------------
def _apply_recurring(out: pd.DataFrame, rows: list[dict], key_prefix: str, y: int, m: int) -> tuple[pd.DataFrame,int]:
    if not rows: return out, 0
    existing = set(out["fixed_key"].fillna("").astype(str).tolist())
    yyyymm = f"{y}{m:02d}"
    add = []
    for r in rows:
        rid = r["rid"]
        key = f"{key_prefix}{rid}_{yyyymm}"
        if key in existing: 
            continue
        day = max(1, min(int(r["day"]), month_last_day(y,m)))
        add.append({
            "id": str(uuid.uuid4()),
            "date": pd.Timestamp(date(y,m,day)),
            "type": "지출",
            "category": r.get("category", FIXED_CATEGORY),
            "amount": int(r.get("amount",0)),
            "memo": r.get("memo",""),
            "fixed_key": key,
            "user": current_user(),
        })
    if not add: return out, 0
    out2 = pd.concat([out, pd.DataFrame(add)], ignore_index=True)
    return out2, len(add)

def apply_fixed_for_month(ledger_df: pd.DataFrame, fixed_df: pd.DataFrame, y: int, m: int):
    rows=[]
    for _,fx in fixed_df.iterrows():
        fid = str(fx.get("fixed_id","")).strip()
        if not fid: continue
        name = str(fx.get("name","")).strip()
        memo = str(fx.get("memo","")).strip()
        full = name if not memo else (f"{name} ({memo})" if name else memo)
        rows.append({
            "rid": fid,
            "day": to_int_money(fx.get("day",1),1),
            "amount": to_int_money(fx.get("amount",0),0),
            "category": FIXED_CATEGORY,
            "memo": f"[고정지출] {full}".strip(),
        })
    return _apply_recurring(ledger_df, rows, "FIX_", y, m)

def apply_subs_for_month(ledger_df: pd.DataFrame, subs_df: pd.DataFrame, y: int, m: int):
    rows=[]
    for _,sb in subs_df.iterrows():
        card = str(sb.get("card_name","")).strip()
        merchant = str(sb.get("merchant","")).strip()
        rid = f"{card}_{merchant}".strip("_")
        if not rid: continue
        memo = str(sb.get("memo","")).strip()
        full = f"{card} - {merchant}".strip(" -")
        if memo: full = f"{full} ({memo})"
        rows.append({
            "rid": rid,
            "day": to_int_money(sb.get("day",1),1),
            "amount": to_int_money(sb.get("amount",0),0),
            "category": "생활",
            "memo": f"[정기결제] {full}".strip(),
        })
    return _apply_recurring(ledger_df, rows, "SUB_", y, m)

# ----------------- Widgets -----------------
def month_selector(prefix: str):
    yk, mk = f"{prefix}_year", f"{prefix}_month"
    t = date.today()
    st.session_state.setdefault(yk, t.year)
    st.session_state.setdefault(mk, t.month)
    c1,c2 = st.columns([1,1])
    with c1:
        y = st.number_input("연도", 2000, 2100, int(st.session_state[yk]), key=yk)
    with c2:
        m = st.selectbox("월", list(range(1,13)), index=int(st.session_state[mk])-1, key=mk)
    return int(y), int(m)

def download_df_excel(df: pd.DataFrame, file_name: str, label: str):
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="data")
    st.download_button(label=label, data=bio.getvalue(), file_name=file_name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch")

# ----------------- Sidebar -----------------
with st.sidebar:
    st.header("설정")
    st.text_input("사용자", key="user_name", value=st.session_state.get("user_name","default"))
    if st.button("수동 새로고침", width="stretch"):
        clear_cache_and_rerun("새로고침 완료")

# ----------------- Main -----------------
st.title("가계부")
expense_categories = st.secrets.get("categories", {}).get("expense", DEFAULT_EXPENSE_CATEGORIES)
income_categories  = st.secrets.get("categories", {}).get("income",  DEFAULT_INCOME_CATEGORIES)
budget_categories  = [c for c in expense_categories if c != FIXED_CATEGORY] + [FIXED_CATEGORY]

tab_main, tab_budget, tab_fixed, tab_events, tab_zeropay, tab_cards = st.tabs(
    ["가계부","예산 설정","고정지출","경조사비","제로페이","신용카드"]
)

def _ledger_type_changed():
    typ = st.session_state.get("ledger_type","지출")
    opts = income_categories if typ=="수입" else expense_categories
    if st.session_state.get("ledger_category") not in opts:
        st.session_state["ledger_category"] = opts[0] if opts else ""

# --- Ledger tab ---
with tab_main:
    st.subheader("내역 입력")
    a,b,c,d = st.columns([1.2,1,1,1])
    with a:
        dt = st.date_input("날짜", value=date.today(), key="ledger_date")
    with b:
        st.selectbox("구분", ["지출","수입"], key="ledger_type", on_change=_ledger_type_changed)
    with c:
        typ = st.session_state.get("ledger_type","지출")
        opts = income_categories if typ=="수입" else expense_categories
        st.session_state.setdefault("ledger_category", opts[0] if opts else "")
        if st.session_state["ledger_category"] not in opts and opts:
            st.session_state["ledger_category"] = opts[0]
        st.selectbox("카테고리", opts, key="ledger_category")
    with d:
        amt = st.text_input("금액(원)", value="", key="ledger_amount")
    memo = st.text_input("메모", value="", key="ledger_memo")

    if st.button("추가", key="add_ledger", width="stretch"):
        try:
            append_ledger(dt, st.session_state["ledger_type"], st.session_state["ledger_category"],
                          to_int_money(amt,0), memo)
            clear_cache_and_rerun("내역이 저장되었습니다.")
        except Exception as e:
            st.error("저장 중 오류")
            st.code(str(e))

    st.markdown("---")
    st.subheader("내역 보기")
    y,m = month_selector("main")
    ledger_df = load_ledger()
    ledger_df["dt"] = parse_date_col(ledger_df)
    cur = ledger_df[(ledger_df["dt"].dt.year==y) & (ledger_df["dt"].dt.month==m) & (ledger_df["user"]==current_user())].copy()
    cur = cur.sort_values("dt", ascending=False)

    income = int(cur.loc[cur["type"]=="수입","amount"].sum())
    expense = int(cur.loc[cur["type"]=="지출","amount"].sum())
    x1,x2,x3 = st.columns(3)
    x1.metric("수입", f"{money_str(income)}원")
    x2.metric("지출", f"{money_str(expense)}원")
    x3.metric("차액", f"{money_str(income-expense)}원")

    # Apply buttons (manual)
    st.caption("고정지출/정기결제는 버튼을 눌러서 선택 월에만 반영합니다. (중복 반영 방지)")
    c1,c2 = st.columns(2)
    with c1:
        if st.button("선택 월에 고정지출 반영", width="stretch"):
            fixed_df = load_fixed()
            out, added = apply_fixed_for_month(ledger_df, fixed_df, y, m)
            if added:
                save_ledger(out)
                clear_cache_and_rerun(f"{y}년 {m}월 고정지출 {added}건 반영 완료!")
            else:
                st.info("추가로 반영할 고정지출이 없어요.")
    with c2:
        if st.button("선택 월에 정기결제 반영", width="stretch"):
            subs_df = load_card_subs()
            out, added = apply_subs_for_month(ledger_df, subs_df, y, m)
            if added:
                save_ledger(out)
                clear_cache_and_rerun(f"{y}년 {m}월 정기결제 {added}건 반영 완료!")
            else:
                st.info("추가로 반영할 정기결제가 없어요.")

    view = cur[["date","type","category","amount","memo"]].copy()
    view["amount"] = view["amount"].apply(money_str)
    st.dataframe(view.reset_index(drop=True), width="stretch", hide_index=True)
    download_df_excel(view, f"가계부_{y}-{m:02d}.xlsx", "선택 월 데이터 엑셀 다운로드")

# --- Budget tab ---
with tab_budget:
    st.subheader("지출 예산 설정 (월별)")
    y,m = month_selector("budget")
    bdf = load_budget_month(budget_categories, y, m)
    bview = bdf[["category","budget"]].copy()
    bview["budget_str"] = bview["budget"].apply(money_str)
    edited = st.data_editor(
        bview[["category","budget_str"]].reset_index(drop=True),
        hide_index=True, width="stretch",
        column_config={
            "category": st.column_config.TextColumn("카테고리", disabled=True),
            "budget_str": st.column_config.TextColumn("목표 금액(원)")
        },
        key="budget_editor"
    )
    if st.button("예산 저장", width="stretch"):
        out = edited.copy()
        out["budget"] = out["budget_str"].apply(lambda x: to_int_money(x, 0))
        save_budget_month(out[["category","budget"]], y, m)
        clear_cache_and_rerun(f"{y}년 {m}월 예산이 저장되었습니다!")

# --- Fixed tab ---
with tab_fixed:
    st.subheader("고정지출 설정")
    st.caption(f"고정지출은 반영 시 모두 '{FIXED_CATEGORY}' 카테고리로 들어가며, 같은 월에 중복 추가되지 않아요.")
    fdf = load_fixed()
    if len(fdf)==0:
        fdf = pd.DataFrame([{"fixed_id":str(uuid.uuid4()),"name":"예: 월세","amount":0,"day":1,"memo":""}])
    view = fdf.copy()
    view["amount_str"] = view["amount"].apply(money_str)
    edited = st.data_editor(
        view[["fixed_id","name","amount_str","day","memo"]].reset_index(drop=True),
        width="stretch", hide_index=True, num_rows="dynamic",
        column_config={
            "fixed_id": st.column_config.TextColumn("ID", disabled=True),
            "name": st.column_config.TextColumn("항목"),
            "amount_str": st.column_config.TextColumn("금액(원)"),
            "day": st.column_config.NumberColumn("결제일", min_value=1, max_value=31, step=1),
            "memo": st.column_config.TextColumn("메모"),
        },
        key="fixed_editor"
    )
    if st.button("고정지출 저장", width="stretch"):
        out = edited.copy()
        out["amount"] = out["amount_str"].apply(lambda x: to_int_money(x, 0))
        save_fixed(out[["fixed_id","name","amount","day","memo"]])
        clear_cache_and_rerun("고정지출이 저장되었습니다!")

# --- Simple log tabs ---
def simple_log_tab(title: str, ws_title: str, default_type: str):
    st.subheader(f"{title} 내역 입력")
    c1,c2,c3 = st.columns([1.2,1,1])
    with c1: d = st.date_input("날짜", value=date.today(), key=f"{ws_title}_date")
    with c2: typ = st.text_input("구분", value=default_type, key=f"{ws_title}_type")
    with c3: amt = st.text_input("금액(원)", value="", key=f"{ws_title}_amount")
    memo = st.text_input("메모", value="", key=f"{ws_title}_memo")
    if st.button("추가", key=f"add_{ws_title}", width="stretch"):
        append_simple(ws_title, d, typ, to_int_money(amt,0), memo)
        clear_cache_and_rerun("저장되었습니다.")
    st.markdown("---")
    y,m = month_selector(ws_title)
    df = load_simple(ws_title)
    df["dt"] = parse_date_col(df)
    cur = df[(df["dt"].dt.year==y) & (df["dt"].dt.month==m) & (df["user"]==current_user())].copy()
    cur = cur.sort_values("dt", ascending=False)
    view = cur[["date","type","amount","memo"]].copy()
    view["amount"] = view["amount"].apply(money_str)
    st.dataframe(view.reset_index(drop=True), width="stretch", hide_index=True)
    download_df_excel(view, f"{ws_title}_{y}-{m:02d}.xlsx", "선택 월 데이터 엑셀 다운로드")

with tab_events:
    simple_log_tab("경조사비", "events", "event")

with tab_zeropay:
    simple_log_tab("제로페이", "zeropay", "zeropay")

# --- Cards tab ---
with tab_cards:
    st.subheader("신용카드")
    st.caption("카드 혜택과 정기결제 정보를 관리합니다.")
    cards = load_cards().reset_index(drop=True)
    ed_cards = st.data_editor(cards, width="stretch", hide_index=True, num_rows="dynamic", key="cards_editor",
                              column_config={
                                  "card_name": st.column_config.TextColumn("카드명"),
                                  "benefits": st.column_config.TextColumn("혜택"),
                              })
    if st.button("카드 저장", width="stretch"):
        save_cards(ed_cards)
        clear_cache_and_rerun("카드 정보가 저장되었습니다.")

    st.markdown("---")
    subs = ensure_columns(ws_read_df("card_subscriptions", CARD_SUBS_COLS), CARD_SUBS_COLS).reset_index(drop=True)
    ed_subs = st.data_editor(subs, width="stretch", hide_index=True, num_rows="dynamic", key="subs_editor",
                             column_config={
                                 "card_name": st.column_config.TextColumn("카드명"),
                                 "merchant": st.column_config.TextColumn("가맹점/서비스"),
                                 "amount": st.column_config.TextColumn("금액(원)"),
                                 "day": st.column_config.NumberColumn("결제일", min_value=1, max_value=31, step=1),
                                 "memo": st.column_config.TextColumn("메모"),
                             })
    if st.button("정기결제 저장", width="stretch"):
        save_card_subs(ed_subs)
        clear_cache_and_rerun("정기결제가 저장되었습니다.")
