#!/usr/bin/env python3
"""
ADB Web Controller
==================
scrcpy v3 protocol: video + audio + control sockets.
Connection order when audio=true: video (1st), audio (2nd), control (3rd).

Requirements:  pip install flask flask-sock
Usage:         python server.py  →  open http://localhost:7070 in Chrome/Edge
"""

import sys, json, struct, socket, threading, time, subprocess, logging, urllib.request
from pathlib import Path
from flask import Flask, send_from_directory, request as flask_request
from flask_sock import Sock

# ── Config ──────────────────────────────────────────────────────────────────
HOST               = "127.0.0.1"
PORT               = 7070
ADB                = "adb"
SCRCPY_JAR         = "scrcpy-server.jar"
SCRCPY_VER         = "3.1"
SCRCPY_URL         = (
    f"https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{SCRCPY_VER}/scrcpy-server-v{SCRCPY_VER}"
)
SCRCPY_SOCKET      = "scrcpy"
LOCAL_PORT_VIDEO   = 27183
LOCAL_PORT_AUDIO   = 27184
LOCAL_PORT_CONTROL = 27185

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app  = Flask(__name__, static_folder="static")
sock = Sock(app)

session_lock   = threading.Lock()
active_session = None

# ── ADB helpers ──────────────────────────────────────────────────────────────

def adb_run(*args, timeout=15):
    r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def adb_devices():
    out, _, _ = adb_run("devices")
    return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]

def adb_shell(*args, timeout=10):
    return adb_run("shell", *args, timeout=timeout)

def adb_push(src, dst):
    _, _, rc = adb_run("push", src, dst, timeout=30)
    return rc == 0

def adb_forward(local_port, abstract_name):
    _, _, rc = adb_run("forward", f"tcp:{local_port}", f"localabstract:{abstract_name}")
    return rc == 0

def adb_forward_remove(local_port):
    adb_run("forward", "--remove", f"tcp:{local_port}")

# ── JAR download ─────────────────────────────────────────────────────────────

def ensure_jar():
    jar = Path(SCRCPY_JAR)
    if jar.exists():
        log.info(f"scrcpy-server.jar found ({jar.stat().st_size // 1024} KB)")
        return True
    log.info(f"Downloading scrcpy-server v{SCRCPY_VER} ...")
    try:
        urllib.request.urlretrieve(SCRCPY_URL, SCRCPY_JAR)
        log.info(f"Downloaded → {SCRCPY_JAR}")
        return True
    except Exception as e:
        log.error(f"Download failed: {e}")
        return False

# ── Session ───────────────────────────────────────────────────────────────────

class ScrcpySession:
    def __init__(self):
        self.video_sock    = None
        self.audio_sock    = None
        self.control_sock  = None
        self.proc          = None
        self.device_name   = "Android"
        self.width         = 0
        self.height        = 0
        self.running       = False

        self.video_clients  = set()
        self.video_lock     = threading.Lock()
        self.last_video_cfg = None   # cached SPS/PPS for late joiners

        self.audio_clients  = set()
        self.audio_lock     = threading.Lock()
        self.last_audio_cfg = None   # cached AudioSpecificConfig for late joiners

        self.video_thread = None
        self.audio_thread = None

    # ── start ────────────────────────────────────────────────────────────────

    def start(self):
        log.info("Starting scrcpy session...")

        if not adb_push(SCRCPY_JAR, "/data/local/tmp/scrcpy-server.jar"):
            raise RuntimeError("adb push failed — is USB debugging enabled?")

        adb_shell("pkill", "-f", "scrcpy-server", timeout=5)
        time.sleep(0.3)

        for p in (LOCAL_PORT_VIDEO, LOCAL_PORT_AUDIO, LOCAL_PORT_CONTROL):
            adb_forward(p, SCRCPY_SOCKET)

        cmd = (
            "CLASSPATH=/data/local/tmp/scrcpy-server.jar "
            "app_process / com.genymobile.scrcpy.Server "
            f"{SCRCPY_VER} "
            "tunnel_forward=true "
            "video=true "
            "audio=true "
            "control=true "
            "video_codec=h264 "
            "audio_codec=aac "
            "video_bit_rate=8000000 "
            "audio_bit_rate=128000 "
            "max_fps=60 "
            "max_size=0 "
            "lock_video_orientation=-1 "
            "stay_awake=true "
            "show_touches=false "
            "power_off_on_close=false "
            "clipboard_autosync=false "
        )
        self.proc = subprocess.Popen(
            [ADB, "shell", cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        log.info("scrcpy server launched")
        time.sleep(1.2)

        # Connection order is critical: video first, audio second, control third
        self.video_sock   = self._connect(LOCAL_PORT_VIDEO,   "video",   retries=12)
        self.audio_sock   = self._connect(LOCAL_PORT_AUDIO,   "audio",   retries=8)
        self.control_sock = self._connect(LOCAL_PORT_CONTROL, "control", retries=8)

        self._read_handshake(self.video_sock, "video")
        self._read_handshake(self.audio_sock, "audio")

        self.running = True

        self.video_thread = threading.Thread(
            target=self._stream_loop,
            args=(self.video_sock, "video", self.video_clients, self.video_lock, "last_video_cfg"),
            daemon=True)
        self.audio_thread = threading.Thread(
            target=self._stream_loop,
            args=(self.audio_sock, "audio", self.audio_clients, self.audio_lock, "last_audio_cfg"),
            daemon=True)

        self.video_thread.start()
        self.audio_thread.start()

        log.info(f"Session started: '{self.device_name}' {self.width}×{self.height}")

    # ── stop ─────────────────────────────────────────────────────────────────

    def stop(self):
        self.running = False
        for s in (self.video_sock, self.audio_sock, self.control_sock):
            if s:
                try: s.close()
                except: pass
        if self.proc:
            try: self.proc.kill()
            except: pass
        for p in (LOCAL_PORT_VIDEO, LOCAL_PORT_AUDIO, LOCAL_PORT_CONTROL):
            adb_forward_remove(p)
        log.info("Session stopped")

    # ── socket helpers ────────────────────────────────────────────────────────

    def _connect(self, port, name, retries=8):
        for i in range(retries):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(4)
                s.connect(("127.0.0.1", port))
                s.settimeout(None)
                log.info(f"Connected: {name} :{port}")
                return s
            except Exception as e:
                log.warning(f"  [{name}] retry {i+1}/{retries}: {e}")
                time.sleep(0.6)
        raise RuntimeError(f"Could not connect to {name} :{port}")

    def _recv_exact(self, s, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise EOFError(f"Socket closed at {len(buf)}/{n}")
            buf.extend(chunk)
        return bytes(buf)

    def _read_handshake(self, s, name):
        try:
            self._recv_exact(s, 1)   # dummy byte
            hdr = self._recv_exact(s, 76)
            if name == "video":
                self.device_name = hdr[:64].split(b'\x00')[0].decode("utf-8", errors="replace").strip()
                codec_id = struct.unpack(">I", hdr[64:68])[0]
                self.width  = struct.unpack(">I", hdr[68:72])[0]
                self.height = struct.unpack(">I", hdr[72:76])[0]
                log.info(f"Video: '{self.device_name}' codec=0x{codec_id:08x} {self.width}×{self.height}")
            else:
                codec_id = struct.unpack(">I", hdr[64:68])[0]
                log.info(f"Audio: codec=0x{codec_id:08x} ({codec_id.to_bytes(4,'big').decode('ascii','replace')})")
        except Exception as e:
            log.warning(f"{name} handshake failed: {e}")
            if name == "video" and not self.width:
                out, _, _ = adb_shell("wm", "size")
                for part in ("Override size:", "Physical size:"):
                    if part in out:
                        w, h = out.split(part)[-1].strip().split()[0].split("x")
                        self.width, self.height = int(w), int(h)
                        break
                if not self.width:
                    self.width, self.height = 1080, 1920

    # ── stream loop (shared by video and audio) ───────────────────────────────

    def _stream_loop(self, sock, name, clients, lock, cfg_attr):
        log.info(f"{name} loop started")
        frame_index = 0
        while self.running:
            try:
                hdr      = self._recv_exact(sock, 12)
                pts_raw  = struct.unpack(">Q", hdr[:8])[0]
                pkt_size = struct.unpack(">I", hdr[8:12])[0]

                if pkt_size == 0 or pkt_size > 20_000_000:
                    log.warning(f"{name}: odd pkt size {pkt_size}")
                    continue

                data      = self._recv_exact(sock, pkt_size)
                is_config = bool(pts_raw >> 63)
                pts_us    = frame_index * 33333
                if not is_config:
                    frame_index += 1

                self._broadcast(data, is_config, pts_us, clients, lock, cfg_attr)

            except Exception as e:
                if self.running:
                    log.error(f"{name} loop error: {e}")
                break
        log.info(f"{name} loop ended")

    def _broadcast(self, data, is_config, pts_us, clients, lock, cfg_attr):
        payload = struct.pack("<BQ", 0x01 if is_config else 0x00, pts_us) + data
        if is_config:
            setattr(self, cfg_attr, payload)
        dead = set()
        with lock:
            cl = list(clients)
        for ws in cl:
            try:
                ws.send(payload)
            except Exception:
                dead.add(ws)
        if dead:
            with lock:
                clients -= dead

    def subscribe_video(self, ws):
        if self.last_video_cfg:
            try: ws.send(self.last_video_cfg)
            except: pass
        with self.video_lock:
            self.video_clients.add(ws)

    def unsubscribe_video(self, ws):
        with self.video_lock:
            self.video_clients.discard(ws)

    def subscribe_audio(self, ws):
        if self.last_audio_cfg:
            try: ws.send(self.last_audio_cfg)
            except: pass
        with self.audio_lock:
            self.audio_clients.add(ws)

    def unsubscribe_audio(self, ws):
        with self.audio_lock:
            self.audio_clients.discard(ws)

    def send_control(self, data):
        if self.control_sock and self.running:
            try:
                self.control_sock.sendall(data)
            except Exception as e:
                log.warning(f"Control send error: {e}")


# ── Control message builders ──────────────────────────────────────────────────

SC_TYPE_INJECT_KEYCODE    = 0
SC_TYPE_INJECT_TEXT       = 1
SC_TYPE_INJECT_TOUCH      = 2
SC_TYPE_INJECT_SCROLL     = 3
SC_TYPE_BACK_OR_SCREEN_ON = 4
ACTION_DOWN = 0; ACTION_UP = 1; ACTION_MOVE = 2
KEY_DOWN = 0;    KEY_UP   = 1
POINTER_ID_MOUSE = 0xFFFFFFFFFFFFFFFE

def ctrl_touch(action, x, y, w, h, pressure=1.0):
    press = int(max(0.0, min(1.0, pressure)) * 65535)
    return struct.pack(">BBQiiHHHII",
        SC_TYPE_INJECT_TOUCH, action, POINTER_ID_MOUSE,
        int(x), int(y), int(w), int(h), press,
        1 if action == ACTION_DOWN else 0,
        0 if action == ACTION_UP   else 1)

def ctrl_scroll(x, y, w, h, hscroll, vscroll):
    return struct.pack(">BiiHHffI",
        SC_TYPE_INJECT_SCROLL, int(x), int(y), int(w), int(h),
        float(hscroll), float(vscroll), 0)

def ctrl_keycode(action, keycode, meta=0):
    return struct.pack(">BBiii", SC_TYPE_INJECT_KEYCODE, action, keycode, 0, meta)

def ctrl_text(text):
    enc = text.encode("utf-8")
    return struct.pack(">BI", SC_TYPE_INJECT_TEXT, len(enc)) + enc

def ctrl_back_or_screen_on():
    return struct.pack(">BB", SC_TYPE_BACK_OR_SCREEN_ON, KEY_DOWN)


# ── WebSocket: video + control ────────────────────────────────────────────────

@sock.route("/ws")
def ws_video(ws):
    global active_session
    with session_lock: session = active_session

    if not session or not session.running:
        ws.send(json.dumps({"type": "error", "msg": "No active session. Click Connect."}))
        return

    session.subscribe_video(ws)
    ws.send(json.dumps({
        "type":   "device_info",
        "name":   session.device_name,
        "width":  session.width,
        "height": session.height,
    }))

    try:
        while True:
            raw = ws.receive()
            if raw is None: break
            try: msg = json.loads(raw)
            except: continue
            t = msg.get("type"); sw = session.width; sh = session.height
            if   t == "touch_down":          session.send_control(ctrl_touch(ACTION_DOWN, msg["x"], msg["y"], sw, sh, 1.0))
            elif t == "touch_move":          session.send_control(ctrl_touch(ACTION_MOVE, msg["x"], msg["y"], sw, sh, 1.0))
            elif t == "touch_up":            session.send_control(ctrl_touch(ACTION_UP,   msg["x"], msg["y"], sw, sh, 0.0))
            elif t == "scroll":              session.send_control(ctrl_scroll(msg["x"], msg["y"], sw, sh, msg.get("hscroll",0), msg.get("vscroll",0)))
            elif t == "keycode":             session.send_control(ctrl_keycode(KEY_DOWN, msg["keycode"], msg.get("meta",0))); session.send_control(ctrl_keycode(KEY_UP, msg["keycode"], msg.get("meta",0)))
            elif t == "text":                session.send_control(ctrl_text(msg["text"]))
            elif t == "back_or_screen_on":   session.send_control(ctrl_back_or_screen_on())
            elif t == "ping":                ws.send(json.dumps({"type": "pong"}))
    except Exception as e:
        log.debug(f"Video WS disconnect: {e}")
    finally:
        session.unsubscribe_video(ws)


# ── WebSocket: audio ──────────────────────────────────────────────────────────

@sock.route("/ws/audio")
def ws_audio(ws):
    global active_session
    with session_lock: session = active_session

    if not session or not session.running:
        ws.send(json.dumps({"type": "error", "msg": "No audio session"}))
        return

    session.subscribe_audio(ws)
    try:
        while True:
            raw = ws.receive()
            if raw is None: break
    except Exception as e:
        log.debug(f"Audio WS disconnect: {e}")
    finally:
        session.unsubscribe_audio(ws)


# ── REST ──────────────────────────────────────────────────────────────────────

@app.route("/api/devices")
def api_devices():
    return json.dumps({"devices": adb_devices()})

@app.route("/api/status")
def api_status():
    with session_lock: s = active_session
    if s and s.running:
        return json.dumps({"running": True, "name": s.device_name, "width": s.width, "height": s.height})
    return json.dumps({"running": False})

@app.route("/api/start", methods=["POST"])
def api_start():
    global active_session
    with session_lock:
        if active_session and active_session.running:
            active_session.stop()
        try:
            s = ScrcpySession()
            s.start()
            active_session = s
            return json.dumps({"ok": True, "name": s.device_name, "width": s.width, "height": s.height})
        except Exception as e:
            log.error(f"Start failed: {e}")
            return json.dumps({"ok": False, "error": str(e)}), 500

@app.route("/api/stop", methods=["POST"])
def api_stop():
    global active_session
    with session_lock:
        if active_session:
            active_session.stop()
            active_session = None
    return json.dumps({"ok": True})

@app.route("/api/keycode", methods=["POST"])
def api_keycode():
    global active_session
    with session_lock: s = active_session
    if not s or not s.running:
        return json.dumps({"ok": False, "error": "no session"}), 400
    kc = int(flask_request.get_json(force=True).get("keycode", 0))
    s.send_control(ctrl_keycode(KEY_DOWN, kc))
    s.send_control(ctrl_keycode(KEY_UP,   kc))
    return json.dumps({"ok": True})

@app.route("/")
def index():
    from flask import redirect
    return redirect("/v2", code=302)

@app.route("/v2")
def index_v2():
    from flask import make_response
    resp = make_response(send_from_directory("static", "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "-1"
    return resp

@app.route("/<path:path>")
def static_files(path):
    from flask import make_response
    resp = make_response(send_from_directory("static", path))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    missing = []
    try: import flask
    except ImportError: missing.append("flask")
    try: import flask_sock
    except ImportError: missing.append("flask-sock")
    if missing:
        print(f"Missing: pip install {' '.join(missing)}")
        sys.exit(1)

    out, _, rc = adb_run("version")
    if rc != 0:
        print("ERROR: adb not found. Install Android Platform Tools and add to PATH.")
        sys.exit(1)
    log.info(f"ADB: {out.splitlines()[0]}")

    if not ensure_jar():
        print(f"\nManual download: {SCRCPY_URL}")
        print(f"Save as:         {SCRCPY_JAR}\n")

    devs = adb_devices()
    if devs:
        log.info(f"Device found: {devs[0]} — auto-starting...")
        try:
            s = ScrcpySession()
            s.start()
            with session_lock:
                active_session = s
        except Exception as e:
            log.warning(f"Auto-start failed: {e}")

    log.info(f"\n🚀  Open http://localhost:{PORT}  in Chrome or Edge\n")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
