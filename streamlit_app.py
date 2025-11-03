# Ver 0.6

# Perfume Project — Main Page UI (Revised)
# Streamlit-native image rendering + buttons (no raw HTML <img>/<form>)
# • Popup to capture Name & Tel → plays BenHur-Music.mp3 while initializing (max 5s)
# • Stop music on timeout or when HeyGen returns 200 OK (token created)
# • Avatar viewer kept high & tall; sidebar retains Start/End
# • Bottom row: 5 pictures in a single row, touch/click to make avatar speak
# • Assets live at repo root: ./assets/<file>.png

import atexit
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests
import streamlit as st
import streamlit.components.v1 as components

# ---------------- Page setup (portrait-first) ----------------
st.set_page_config(page_title="Perfume • Avatar", layout="wide", initial_sidebar_state="expanded")

# Minimal mobile-first CSS. No custom HTML gallery anymore; keep general look tidy.
st.markdown(
    """
    <style>
      .block-container { padding-top: .4rem; padding-bottom: .6rem; max-width: 900px; }
      .avatar-wrap { margin-top: .2rem; }
      .avatar-stage iframe { width: 100%; height: 58vh; border: 0; border-radius: 16px; background: #000; }
      @media (max-width: 480px) { .avatar-stage iframe { height: 55vh; } }
      .stButton>button { border-radius: 12px; height: 44px; }
      section[data-testid='stSidebar'] .block-container { padding-top: .6rem; }
      /* Make picture buttons compact */
      .perfume-btn > button { width: 100%; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- Secrets / Keys ----------------
SECRETS = st.secrets if "secrets" in dir(st) else {}
HEYGEN_API_KEY = (
    SECRETS.get("HeyGen", {}).get("heygen_api_key")
    or SECRETS.get("heygen", {}).get("heygen_api_key")
    or os.getenv("HEYGEN_API_KEY")
)
OPENAI_API_KEY = (
    SECRETS.get("openai", {}).get("secret_key")
    or os.getenv("OPENAI_API_KEY")
)
if not HEYGEN_API_KEY:
    st.error("Missing HeyGen API key in .streamlit/secrets.toml or env HEYGEN_API_KEY")
    st.stop()

# ---------------- Endpoints ----------------
BASE = "https://api.heygen.com/v1"
API_STREAM_NEW = f"{BASE}/streaming.new"
API_CREATE_TOKEN = f"{BASE}/streaming.create_token"
API_STREAM_TASK = f"{BASE}/streaming.task"
API_STREAM_STOP = f"{BASE}/streaming.stop"

HEADERS_XAPI = {
    "accept": "application/json",
    "x-api-key": HEYGEN_API_KEY,
    "Content-Type": "application/json",
}

def _headers_bearer(tok: str):
    return {
        "accept": "application/json",
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    }

# ---------------- Fixed Avatar ----------------
FIXED_AVATAR = {
    "avatar_id": "June_HR_public",
    "default_voice": "68dedac41a9f46a6a4271a95c733823c",
    "pose_name": "June HR",
}

# ---------------- Session State ----------------
ss = st.session_state
ss.setdefault("Name_Tel", "")
ss.setdefault("popup_done", False)
ss.setdefault("play_bgm", False)
ss.setdefault("init_deadline", 0.0)
ss.setdefault("session_id", None)
ss.setdefault("session_token", None)
ss.setdefault("offer_sdp", None)
ss.setdefault("rtc_config", None)
ss.setdefault("viewer_ready", False)
ss.setdefault("gpt_query", "Hello, welcome.")

# ---------------- HTTP Helpers ----------------
def _post_xapi(url, payload=None):
    r = requests.post(url, headers=HEADERS_XAPI, data=json.dumps(payload or {}), timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{url} -> {r.status_code}: {body}")
    return r.status_code, body


def _post_bearer(url, token, payload=None):
    r = requests.post(url, headers=_headers_bearer(token), data=json.dumps(payload or {}), timeout=60)
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{url} -> {r.status_code}: {body}")
    return r.status_code, body


# ---------------- HeyGen helpers ----------------
def new_session(avatar_id: str, voice_id: Optional[str] = None):
    payload = {"avatar_id": avatar_id}
    if voice_id:
        payload["voice_id"] = voice_id
    _, body = _post_xapi(API_STREAM_NEW, payload)
    data = body.get("data") or {}
    sid = data.get("session_id")
    offer_sdp = (data.get("offer") or data.get("sdp") or {}).get("sdp")
    ice2 = data.get("ice_servers2")
    ice1 = data.get("ice_servers")
    if isinstance(ice2, list) and ice2:
        rtc_config = {"iceServers": ice2}
    elif isinstance(ice1, list) and ice1:
        rtc_config = {"iceServers": ice1}
    else:
        rtc_config = {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
    if not sid or not offer_sdp:
        raise RuntimeError(f"Missing session_id or offer in response: {body}")
    return {"session_id": sid, "offer_sdp": offer_sdp, "rtc_config": rtc_config}


def create_session_token(session_id: str) -> str:
    _, body = _post_xapi(API_CREATE_TOKEN, {"session_id": session_id})
    tok = (body.get("data") or {}).get("token") or (body.get("data") or {}).get("access_token")
    if not tok:
        raise RuntimeError(f"Missing token in response: {body}")
    return tok


def send_text_to_avatar(session_id: str, session_token: str, text: str):
    _post_bearer(
        API_STREAM_TASK,
        session_token,
        {
            "session_id": session_id,
            "task_type": "repeat",
            "task_mode": "sync",
            "text": text,
        },
    )


def stop_session(session_id: Optional[str], session_token: Optional[str]):
    if not (session_id and session_token):
        return
    try:
        _post_bearer(API_STREAM_STOP, session_token, {"session_id": session_id})
    except Exception:
        pass


@atexit.register
def _graceful_shutdown():
    try:
        sid = st.session_state.get("session_id")
        tok = st.session_state.get("session_token")
        if sid and tok:
            stop_session(sid, tok)
    except Exception:
        pass

# ---------------- Popup (Name & Tel) ----------------
with st.container(border=True):
    if not ss.popup_done:
        st.markdown("### Enter your details to start")
        name_tel = st.text_input("Name & Tel:", key="name_tel_input", placeholder="e.g., Alex Tan, +65 9xxx xxxx")
        c1, c2 = st.columns(2)
        ok = c1.button("OK", type="primary")
        cancel = c2.button("Cancel")
        if ok:
            ss.Name_Tel = name_tel.strip()
            ss.popup_done = True
            ss.play_bgm = True
            ss.init_deadline = time.time() + 5.0
            try:
                created = new_session(FIXED_AVATAR["avatar_id"], FIXED_AVATAR.get("default_voice"))
                sid, offer_sdp, rtc_config = created["session_id"], created["offer_sdp"], created["rtc_config"]
                tok = create_session_token(sid)  # 200 OK ⇒ init completed
                ss.session_id, ss.session_token = sid, tok
                ss.offer_sdp, ss.rtc_config = offer_sdp, rtc_config
                ss.viewer_ready = True
            except Exception as e:
                st.error(f"Avatar init failed: {e}")
        elif cancel:
            ss.Name_Tel = ""
            ss.popup_done = True
            ss.play_bgm = False

# ---------------- Background music control ----------------
benhur_path = Path(__file__).parent / "BenHur-Music.mp3"
if ss.play_bgm and benhur_path.exists():
    components.html("""
        <audio id='bgm' src='BenHur-Music.mp3' autoplay loop></audio>
    """, height=0)

if ss.play_bgm and (time.time() >= ss.init_deadline or ss.viewer_ready):
    components.html("""
        <script>
          const a = document.getElementById('bgm'); if (a) { a.pause(); a.currentTime = 0; }
        </script>
    """, height=0)
    ss.play_bgm = False

# ---------------- Sidebar — Start / End (retain) ----------------
with st.sidebar:
    st.markdown("### HeyGen Controls")
    if st.button("Start"):
        try:
            created = new_session(FIXED_AVATAR["avatar_id"], FIXED_AVATAR.get("default_voice"))
            sid, offer_sdp, rtc_config = created["session_id"], created["offer_sdp"], created["rtc_config"]
            tok = create_session_token(sid)
            ss.session_id, ss.session_token = sid, tok
            ss.offer_sdp, ss.rtc_config = offer_sdp, rtc_config
            ss.viewer_ready = True
        except Exception as e:
            st.error(f"Start failed: {e}")
    if st.button("End"):
        stop_session(ss.session_id, ss.session_token)
        ss.session_id = None
        ss.session_token = None
        ss.offer_sdp = None
        ss.rtc_config = None
        ss.viewer_ready = False

# ---------------- Main Viewer (kept high and large) ----------------
viewer_path = Path(__file__).parent / "viewer.html"

st.markdown("<div class='avatar-wrap'>", unsafe_allow_html=True)
if ss.viewer_ready and viewer_path.exists():
    html = (
        viewer_path.read_text(encoding="utf-8")
        .replace("__SESSION_TOKEN__", ss.session_token)
        .replace("__AVATAR_NAME__", FIXED_AVATAR["pose_name"])
        .replace("__SESSION_ID__", ss.session_id)
        .replace("__OFFER_SDP__", json.dumps(ss.offer_sdp)[1:-1])
        .replace("__RTC_CONFIG__", json.dumps(ss.rtc_config or {}))
    )
    components.html(html, height=int(0.58 * 1000), scrolling=False)
else:
    st.info("Avatar not started yet. Use the left panel to Start.")
st.markdown("</div>", unsafe_allow_html=True)

# ---------------- Bottom Picture Row (touch to speak) ----------------
# Files expected at repo root under ./assets
ASSETS_DIR = Path(__file__).parent / "assets"

perfumes = [
    {"id": 1, "name": "Endless Mountains & Rivers", "file": "1_Endless_rivers.png", "line": "The Perfume Name is: Endless Mountains & Rivers"},
    {"id": 2, "name": "Flowing Gently into Calm", "file": "2_Flowing_Calm.png", "line": "The Perfume Name is: Flowing Gently into Calm"},
    {"id": 3, "name": "Stillness in the Mountains", "file": "3_Still_Mountain.png", "line": "The Perfume Name is Stillness in the Mountains"},
    {"id": 4, "name": "Wind Through Wooden Frames", "file": "4_Wind_Frames.png", "line": "The Perfume Name is: Wind Through Wooden Frames"},
    {"id": 5, "name": "Rain In The Hills", "file": "5_Rain_Hills.png", "line": "The Perfume Name is: Rain In The Hills"},
]

# Debug toggle to help verify paths in Cloud
debug_assets = st.toggle("Debug assets (show path checks)", value=False)
if debug_assets:
    st.write({
        "cwd": os.getcwd(),
        "ASSETS_DIR": str(ASSETS_DIR),
        "assets_dir_exists": ASSETS_DIR.exists(),
        "assets_list": sorted([p.name for p in ASSETS_DIR.iterdir()]) if ASSETS_DIR.exists() else [],
    })

st.subheader("Tap a picture to speak")

cols = st.columns(5, gap="small")
for i, pf in enumerate(perfumes):
    with cols[i]:
        rel_path = Path("assets") / pf["file"]
        abs_path = ASSETS_DIR / pf["file"]
        # Prefer absolute path (more robust on Cloud)
        img_path = abs_path if abs_path.exists() else rel_path
        if debug_assets:
            st.caption(f"Using: {img_path}")
        try:
            st.image(str(img_path), use_container_width=True)
        except Exception:
            st.warning(f"Missing image: {rel_path}")
        if st.button(pf["name"], key=f"tap_{pf['id']}"):
            if ss.session_id and ss.session_token:
                try:
                    send_text_to_avatar(ss.session_id, ss.session_token, pf["line"])
                    st.success("Sent to avatar")
                except Exception as e:
                    st.error(f"Failed to speak: {e}")
            else:
                st.warning("Start the avatar first from the sidebar.")

# ---------------- Optional: ChatGPT relay (kept minimal) ----------------
colA, colB = st.columns([1,1])
with colA:
    if st.button("Instruction"):
        if ss.session_id and ss.session_token:
            send_text_to_avatar(
                ss.session_id,
                ss.session_token,
                "To speak to me, press the speak button, pause a second and then speak. Once you have spoken press the Stop button.",
            )
        else:
            st.warning("Start a session first.")
with colB:
    if st.button("ChatGPT -> Avatar"):
        user_text = (ss.get("gpt_query") or "").strip()
        if not user_text:
            st.warning("Type something below first.")
        else:
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a clear, concise assistant."},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.6,
                    "max_tokens": 300,
                }
                r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
                body = r.json()
                reply = (body.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                if reply:
                    if ss.session_id and ss.session_token:
                        send_text_to_avatar(ss.session_id, ss.session_token, reply)
                        st.success("Sent ChatGPT reply to avatar.")
                    ss.gpt_query = reply
                else:
                    st.error("No reply from ChatGPT.")
            except Exception as e:
                st.error(f"ChatGPT call failed: {e}")

ss.gpt_query = st.text_area("Message", value=ss.get("gpt_query", "Hello, welcome."), height=120)
