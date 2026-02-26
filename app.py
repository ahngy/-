# ============================================================
# 🔐 로그인 (users가 있으면 아이디/비번, 없으면 기존 단일 비번)
# ============================================================
PASSWORD = st.secrets.get("app", {}).get("password", "ab190427")
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

    if isinstance(USERS, dict) and len(USERS) > 0:
        username = st.text_input("아이디", value="")
        pw = st.text_input("비밀번호", type="password")
        login = st.button("로그인", use_container_width=True)
    if has_users:
        username = st.text_input("아이디", value="", key="login_username")
        pw = st.text_input("비밀번호", type="password", key="login_password")
        login = st.button("로그인", use_container_width=True, key="login_btn")

        if login:
            if username in USERS and pw.strip() == str(USERS[username]).strip():
@@ -50,11 +58,11 @@ def do_logout():
            else:
                st.error("아이디/비밀번호가 틀렸어요.")
    else:
        pw = st.text_input("비밀번호를 입력하세요", type="password")
        login = st.button("로그인", use_container_width=True)
        pw = st.text_input("비밀번호를 입력하세요", type="password", key="login_password_only")
        login = st.button("로그인", use_container_width=True, key="login_btn_only")

        if login:
            if pw.strip() == PASSWORD:
            if pw.strip() == str(PASSWORD).strip():
                st.session_state.authenticated = True
                st.session_state.current_user = "me"
                st.success("로그인 성공!")
@@ -158,7 +166,7 @@ def clear_cache_and_rerun(msg: str | None = None):
# ============================================================
expense_categories = [
    "1. 식재료", "2. 외식/배달", "3. 생활", "4. 육아용품", "5. 여가",
    "6. 교통비/유류", "7. 의료", "8. 기타", "9. 고정지출", "10. 목돈지출"
    "6. 교통/유류", "7. 의료", "8. 기타", "9. 고정지출", "10. 목돈지출"
]
income_categories = ["월급", "부수입", "이자", "캐시백", "기타"]
FIXED_CATEGORY = "9. 고정지출"
@@ -1522,3 +1530,4 @@ def simple_log_tab(title: str, ws_title: str, state_key: str):
        clear_cache_and_rerun("저장되었습니다!")

st.caption("Made by Gayoung")
