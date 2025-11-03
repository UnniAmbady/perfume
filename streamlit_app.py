
# Ver 0.5
# Perfume Project — Main Page UI
# Streamlit app focusing on: start popup, music during avatar init (5s or until 200 OK),
# tall avatar view near the top, bottom picture row (touch-friendly), and sidebar Start/End.
# Keeps the HeyGen + ChatGPT wiring and most utilities from Avatharam-2.2-Ver-8.1, while
# simplifying button coloring per your note.    

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

# Make base CSS mobile-first (iPhone/iPad portrait) and remove black borders.
st.markdown(
    """
    <style>
      .block-container { padding-top: .4rem; padding-bottom: .6rem; max-width: 760px; }
      /* Keep the avatar high on screen */
      .avatar-wrap { margin-top: .2rem; }
      /* Viewer frame tweaks to avoid big black border */
      .avatar-stage iframe { width: 100%; height: 58vh; border: 0; border-radius: 16px; background: #000; }
      @media (max-width: 480px) {
        .avatar-stage iframe { height: 55vh; }
      }

      /* Bottom picture strip */
      .thumb-strip { position: relative; margin-top: .6rem; }
      .thumb-row { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(100px, 1fr);
                   gap: 12px; overflow-x: auto; padding: 8px 2px 2px 2px; -webkit-overflow-scrolling: touch; }
      /* Hide scrollbars */
      .thumb-row { scrollbar-width: none; } /* Firefox */
      .thumb-row::-webkit-scrollbar { display: none; }

      .thumb { aspect-ratio: 1 / 1; border-radius: 14px; border: 1px solid #333; display: grid; place-items: center; }
      .thumb img { width: 100%; height: 100%; object-fit: cover; border-radius: 14px; user-select: none; -webkit-user-drag: none; }
      .thumb button { all: unset; cursor: pointer; width: 100%; height: 100%; display: block; }

      /* Make click target equal to the image (tap-friendly) */
      .thumb .tap { position: relative; display: block; width: 100%; height: 100%; }

      /* Minimal buttons */
      .stButton>button { border-radius: 12px; height: 44px; }

      /* Sidebar spacing */
      section[data-testid='stSidebar'] .block-container { padding-top: .6rem; }
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

# ---------------- Fixed Avatar (keep same) ----------------
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
    body = {}
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{url} -> {r.status_code}: {body}")
    return r.status_code, body


def _post_bearer(url, token, payload=None):
    r = requests.post(url, headers=_headers_bearer(token), data=json.dumps(payload or {}), timeout=60)
    body = {}
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
    status, body = _post_xapi(API_CREATE_TOKEN, {"session_id": session_id})
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
# We model a modal-like gate: on first load show an input form. On OK: start BGM and the 5s timer
# and immediately begin avatar initialization.

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
            # Begin initializing the avatar (3–5 sec in practice). As soon as we get
            # a 200 for token creation, we consider init completed and will stop music.
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
# Start music when play_bgm is True; stop when (deadline passed) OR (viewer_ready True)
benhur_path = Path(__file__).parent / "BenHur-Music.mp3"
if ss.play_bgm and benhur_path.exists():
    components.html("""
        <audio id='bgm' src='BenHur-Music.mp3' autoplay loop></audio>
    """, height=0)

# Evaluate stop condition
if ss.play_bgm and (time.time() >= ss.init_deadline or ss.viewer_ready):
    # render a tiny script that stops audio on the client
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
    # Taller frame to minimize borders and maximize avatar presence
    components.html(html, height= int(0.58 * 1000), scrolling=False)  # ~58vh fallback
else:
    st.info("Avatar not started yet. Use the left panel to Start.")
st.markdown("</div>", unsafe_allow_html=True)

# ---------------- Bottom Picture Row (touch to speak) ----------------
# You can place your actual images under ./assets/perfume{1..5}.jpg
# If files are absent, colored placeholders render instead.

perfumes = [
    {
        "id": 1,
        "name": "Endless Mountains & Rivers",
        "img": "assets/1_Endless_rivers.png",
        "line": "The Perfume Name is: Endless Mountains & Rivers",
    },
    {
        "id": 2,
        "name": "Flowing Gently into Calm",
        "img": "assets/2_Flowing_Calm.png",
        "line": "The Perfume Name is: Flowing Gently into Calm",
    },
    {
        "id": 3,
        "name": "Stillness in the Mountains",
        "img": "assets/3_Still_Mountain.png",
        "line": "The Perfume Name is Stillness in the Mountains",
    },
    {
        "id": 4,
        "name": "Wind Through Wooden Frames",
        "img": "assets/4_Wind_Frames.png",
        "line": "The Perfume Name is: Wind Through Wooden Frames",
    },
    {
        "id": 5,
        "name": "Rain In The Hills",
        "img": "assets/5_Rain_Hills.png",
        "line": "The Perfume Name is: Rain In The Hills",
    },
]

st.markdown("<div class='thumb-strip'>", unsafe_allow_html=True)
st.markdown("#### Tap a picture to speak")

# Render three visible, two overflow to the right (natural on mobile). No scrollbar shown.
# We still need a way to detect a tap in Streamlit. Use one button per tile wrapping the image.
# This remains touch-friendly on Windows touch screens as well.

row = st.container()
with row:
    st.markdown("<div class='thumb-row'>", unsafe_allow_html=True)
    chosen = None
    for pf in perfumes:
        img_path = Path(pf["img"]).as_posix()
        exists = Path(pf["img"]).exists()
        col = st.container()
        with col:
            c = st.columns(1)[0]
            with c:
                # Build a tiny HTML tile with a form-like button
                if exists:
                    st.markdown(
                        f"""
                        <div class='thumb'>
                          <form action='' method='post'>
                            <button class='tap' name='tap_{pf['id']}' value='{pf['id']}'></button>
                            <img src='{img_path}' alt='{pf['name']}' />
                          </form>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""
                        <div class='thumb' style='background: linear-gradient(135deg,#333,#111); color:#eee; font-weight:600;'>
                          <form action='' method='post'>
                            <button class='tap' name='tap_{pf['id']}' value='{pf['id']}'></button>
                            <div style='text-align:center;padding:8px;'>#{pf['id']}<br/>{pf['name']}</div>
                          </form>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
            # Convert posted value into Streamlit state using query params (simple technique)
            
    st.markdown("</div>", unsafe_allow_html=True)

# Read posted selection from query params (works because the button submits to the same URL)
params = st.query_params
sel_id = None
for i in range(1, 6):
    k = f"tap_{i}"
    if k in params:
        try:
            sel_id = int(params[k])
        except Exception:
            pass
        # Clear the param so repeated renders don't retrigger
        params.pop(k, None)
        st.query_params.clear()
        break

if sel_id:
    sel = next((x for x in perfumes if x["id"] == sel_id), None)
    if sel and ss.session_id and ss.session_token:
        try:
            send_text_to_avatar(ss.session_id, ss.session_token, sel["line"])
            st.success(f"Spoken: {sel['line']}")
        except Exception as e:
            st.error(f"Failed to speak: {e}")
    elif sel:
        st.warning("Start the avatar first from the sidebar.")

st.markdown("</div>", unsafe_allow_html=True)

# ---------------- Optional: ChatGPT relay (kept minimal, no button color styling) ----------------
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

# Message edit area
ss.gpt_query = st.text_area("Message", value=ss.get("gpt_query", "Hello, welcome."), height=120)
