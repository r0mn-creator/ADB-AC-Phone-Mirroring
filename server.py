#!/usr/bin/env python3
"""
Phone Mirror — server.py
========================
Python backend that bridges an Android phone (via ADB + scrcpy v3 protocol)
to a browser frontend over WebSocket. Streams H.264 video + AAC audio from
the phone and relays touch/key inputs back.

Requirements:  pip install flask flask-sock
Usage:         python server.py   (or double-click START.bat)

Files:
  server.py          — THIS FILE (Python backend)
  static/index.html  — Browser frontend (served by this server)
  scrcpy-server.jar  — scrcpy server binary (pushed to phone via ADB)
  START.bat          — Windows launcher script
"""

# ── Standard library imports ──────────────────────────────────────────────────
import sys              # System exit on fatal errors
import json             # JSON encode/decode for WebSocket text messages
import struct           # Binary pack/unpack for scrcpy protocol messages
import socket           # TCP sockets to connect to scrcpy on the phone
import threading        # Background threads for video/audio streaming
import time             # Sleep delays for connection timing
import subprocess       # Run ADB commands as child processes
import logging          # Structured log output
import urllib.request   # Auto-download scrcpy-server.jar if missing
import re               # Regex to parse version from error messages
from pathlib import Path                                                # File path handling
from flask import Flask, send_from_directory, request as flask_request  # HTTP server + static files
from flask_sock import Sock                                             # WebSocket support for Flask

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")  # Show INFO+
log = logging.getLogger(__name__)                                              # Named logger

# ── Configuration ─────────────────────────────────────────────────────────────
HOST               = "127.0.0.1"          # Listen on localhost only
PORT               = 7070                 # HTTP/WebSocket port for the browser
ADB                = "adb"                # ADB executable name (must be on PATH)
SCRCPY_JAR         = "scrcpy-server.jar"  # scrcpy server jar filename in this folder
SCRCPY_VER         = "3.1"                # Default version (auto-detected on first run)
SCRCPY_URL         = (                    # Download URL if jar is missing
    f"https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{SCRCPY_VER}/scrcpy-server-v{SCRCPY_VER}"
)
SCRCPY_SOCKET      = "scrcpy"             # Abstract socket name on the Android device
LOCAL_PORT_VIDEO   = 27183                # Local TCP port forwarded to video socket
LOCAL_PORT_AUDIO   = 27184                # Local TCP port forwarded to audio socket
LOCAL_PORT_CONTROL = 27185                # Local TCP port forwarded to control socket

# ── Flask app ─────────────────────────────────────────────────────────────────
app  = Flask(__name__, static_folder="static")  # Serve static/index.html
sock = Sock(app)                                # Add WebSocket support

# ── Global session state ──────────────────────────────────────────────────────
session_lock   = threading.Lock()  # Protects active_session from race conditions
active_session = None              # The currently running ScrcpySession (or None)


# ══════════════════════════════════════════════════════════════════════════════
# ADB HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def adb_run(*args, timeout=15):
    """Run an ADB command. Returns (stdout, stderr, returncode)."""
    r = subprocess.run(          # Execute ADB with given arguments
        [ADB, *args],            # e.g. ["adb", "devices"]
        capture_output=True,     # Capture stdout and stderr
        text=True,               # Decode output as UTF-8
        timeout=timeout          # Kill if takes too long
    )
    return r.stdout.strip(), r.stderr.strip(), r.returncode  # Return all three

def adb_devices():
    """Return list of connected+authorized device serial numbers."""
    out, _, _ = adb_run("devices")  # Run "adb devices"
    return [                        # Filter to only authorized devices
        l.split("\t")[0]            # Extract serial number (before tab)
        for l in out.splitlines()[1:]  # Skip the "List of devices" header
        if "\tdevice" in l          # Only lines with "device" status
    ]

def adb_shell(*args, timeout=10):
    """Run a shell command on the connected Android device."""
    return adb_run("shell", *args, timeout=timeout)  # Prefix with "shell"

def adb_push(src, dst):
    """Push a local file to the Android device. Returns True on success."""
    _, _, rc = adb_run("push", src, dst, timeout=30)  # Push with 30s timeout
    return rc == 0                                     # True if exit code is 0

def adb_forward(local_port, abstract_name):
    """Set up ADB port forwarding. Returns True on success."""
    _, _, rc = adb_run("forward", f"tcp:{local_port}", f"localabstract:{abstract_name}")
    return rc == 0  # True if forwarding was established

def adb_forward_remove(local_port):
    """Remove an ADB port forwarding rule."""
    adb_run("forward", "--remove", f"tcp:{local_port}")  # Remove the forward


# ══════════════════════════════════════════════════════════════════════════════
# SCRCPY-SERVER JAR DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

def ensure_jar():
    """Check if scrcpy-server.jar exists locally; download if missing."""
    jar = Path(SCRCPY_JAR)              # Path to the jar file
    if jar.exists():                    # Already downloaded
        log.info(f"scrcpy-server.jar found ({jar.stat().st_size // 1024} KB)")
        return True                     # Ready to use
    log.info(f"Downloading scrcpy-server v{SCRCPY_VER} ...")
    try:
        urllib.request.urlretrieve(SCRCPY_URL, SCRCPY_JAR)  # Download from GitHub
        log.info(f"Downloaded → {SCRCPY_JAR}")
        return True                     # Download succeeded
    except Exception as e:
        log.error(f"Download failed: {e}")
        return False                    # Download failed


# ══════════════════════════════════════════════════════════════════════════════
# SCRCPY SESSION — manages the entire phone connection
# ══════════════════════════════════════════════════════════════════════════════

class ScrcpySession:
    """
    Manages a scrcpy v3 session: video + audio + control sockets.
    
    Socket connection order (required by scrcpy protocol):
      1. Video socket  (first  — receives dummy byte + device name + codec meta)
      2. Audio socket  (second — receives ONLY codec meta, 4 bytes)
      3. Control socket (third — no handshake, immediately ready for commands)
    """

    def __init__(self):
        """Set up default state for a new session."""
        self.video_sock    = None       # TCP socket: receives H.264 video frames
        self.audio_sock    = None       # TCP socket: receives AAC audio frames
        self.control_sock  = None       # TCP socket: sends touch/key commands
        self.proc          = None       # Subprocess running scrcpy on the phone
        self.device_name   = "Android"  # Device name from handshake metadata
        self.width         = 0          # Video width in pixels (from handshake)
        self.height        = 0          # Video height in pixels (from handshake)
        self.running       = False      # True while stream threads should run

        self.video_clients  = set()            # Set of WebSocket clients for video
        self.video_lock     = threading.Lock() # Thread-safe access to video_clients
        self.last_video_cfg = None             # Cached SPS/PPS for late-joining clients

        self.audio_clients  = set()            # Set of WebSocket clients for audio
        self.audio_lock     = threading.Lock() # Thread-safe access to audio_clients
        self.last_audio_cfg = None             # Cached AudioSpecificConfig for late joiners

        self.video_thread = None  # Background thread reading video frames
        self.audio_thread = None  # Background thread reading audio frames

    # ── START SESSION ─────────────────────────────────────────────────────────

    def start(self):
        """Start the full scrcpy session: push jar, launch server, connect."""
        log.info("Starting scrcpy session...")

        # Push the scrcpy server jar to the phone's temp folder
        if not adb_push(SCRCPY_JAR, "/data/local/tmp/scrcpy-server.jar"):
            raise RuntimeError("adb push failed — is USB debugging enabled?")

        # Kill any leftover scrcpy process from a previous run
        adb_shell("pkill", "-f", "scrcpy-server", timeout=5)
        time.sleep(0.3)  # Brief pause for cleanup

        # Set up ADB port forwarding (all 3 ports → same abstract socket)
        for p in (LOCAL_PORT_VIDEO, LOCAL_PORT_AUDIO, LOCAL_PORT_CONTROL):
            adb_forward(p, SCRCPY_SOCKET)  # Forward each local port

        # Launch the scrcpy server on the phone (with version auto-detection)
        self._launch_server()

        # Connect sockets in STRICT ORDER: video first, audio second, control third
        self.video_sock   = self._connect(LOCAL_PORT_VIDEO,   "video",   retries=12)
        self.audio_sock   = self._connect(LOCAL_PORT_AUDIO,   "audio",   retries=8)
        self.control_sock = self._connect(LOCAL_PORT_CONTROL, "control", retries=8)

        # Read handshake data from video and audio sockets
        self._read_video_handshake(self.video_sock)    # 77 bytes: dummy + name + meta
        self._read_audio_handshake(self.audio_sock)    # 4 bytes: codec ID only

        # Mark session as running (enables stream loops)
        self.running = True

        # Start background threads to read and broadcast frames
        self.video_thread = threading.Thread(       # Video stream thread
            target=self._stream_loop,               # Thread function
            args=(self.video_sock, "video",         # Socket and name
                  self.video_clients, self.video_lock,  # Client set and lock
                  "last_video_cfg"),                 # Attribute name for caching config
            daemon=True)                            # Dies when main thread exits
        self.audio_thread = threading.Thread(       # Audio stream thread
            target=self._stream_loop,               # Same function, different socket
            args=(self.audio_sock, "audio",
                  self.audio_clients, self.audio_lock,
                  "last_audio_cfg"),
            daemon=True)

        self.video_thread.start()  # Begin reading H.264 frames
        self.audio_thread.start()  # Begin reading AAC frames

        log.info(f"Session started: '{self.device_name}' {self.width}x{self.height}")

    # ── STOP SESSION ──────────────────────────────────────────────────────────

    def stop(self):
        """Clean up: close sockets, kill server process, remove port forwards."""
        self.running = False  # Signal stream threads to exit their loops
        for s in (self.video_sock, self.audio_sock, self.control_sock):
            if s:                      # Only close if socket exists
                try: s.close()         # Close the TCP socket
                except: pass           # Ignore errors during cleanup
        if self.proc:                  # Kill the scrcpy server process
            try: self.proc.kill()      # Force kill
            except: pass               # Ignore if already dead
        for p in (LOCAL_PORT_VIDEO, LOCAL_PORT_AUDIO, LOCAL_PORT_CONTROL):
            adb_forward_remove(p)      # Remove each port forward
        log.info("Session stopped")

    # ── SOCKET CONNECTION ─────────────────────────────────────────────────────

    def _connect(self, port, name, retries=8):
        """Connect a TCP socket to a local ADB-forwarded port, with retries."""
        for i in range(retries):       # Try multiple times (server may be starting)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Create TCP socket
                s.settimeout(4)                     # 4-second timeout for connect
                s.connect(("127.0.0.1", port))      # Connect to ADB forward tunnel
                s.settimeout(None)                  # Switch to blocking mode for reads
                log.info(f"Connected: {name} :{port}")
                return s                            # Return the connected socket
            except Exception as e:
                log.warning(f"  [{name}] retry {i+1}/{retries}: {e}")
                time.sleep(0.6)                     # Wait before retrying
        raise RuntimeError(f"Could not connect to {name} :{port}")  # All retries failed

    def _recv_exact(self, s, n):
        """Read exactly n bytes from socket s. Blocks until all bytes arrive."""
        buf = bytearray()                  # Accumulate received bytes here
        while len(buf) < n:                # Keep reading until we have enough
            chunk = s.recv(n - len(buf))   # Read up to remaining bytes
            if not chunk:                  # Empty read = socket closed
                raise EOFError(f"Socket closed at {len(buf)}/{n}")
            buf.extend(chunk)              # Append to accumulator
        return bytes(buf)                  # Return as immutable bytes

    # ── VIDEO HANDSHAKE (first socket — 77 bytes) ────────────────────────────

    def _read_video_handshake(self, s):
        """
        Read video socket handshake. Per scrcpy v3 protocol, the FIRST
        socket receives: 1 dummy byte + 64 bytes device name + 12 bytes
        codec metadata (codec_id + width + height).
        """
        try:
            self._recv_exact(s, 1)             # Dummy byte (forward mode detection)
            name_raw = self._recv_exact(s, 64) # Device name (64 bytes, null-padded)
            self.device_name = (               # Decode the device name
                name_raw.split(b'\x00')[0]     # Split at first null byte
                .decode("utf-8", errors="replace")  # Decode as UTF-8
                .strip()                       # Remove whitespace
            )
            meta = self._recv_exact(s, 12)     # 12 bytes: codec(4) + width(4) + height(4)
            codec_id    = struct.unpack(">I", meta[0:4])[0]   # Codec ID (e.g. 0x68323634 = h264)
            self.width  = struct.unpack(">I", meta[4:8])[0]   # Video width in pixels
            self.height = struct.unpack(">I", meta[8:12])[0]  # Video height in pixels
            log.info(f"Video: '{self.device_name}' codec=0x{codec_id:08x} {self.width}x{self.height}")
        except Exception as e:
            log.warning(f"Video handshake failed: {e}")
            if not self.width:                 # Fallback: query device for screen size
                out, _, _ = adb_shell("wm", "size")
                for part in ("Override size:", "Physical size:"):  # Try both possible formats
                    if part in out:
                        w, h = out.split(part)[-1].strip().split()[0].split("x")
                        self.width, self.height = int(w), int(h)
                        break
                if not self.width:             # Ultimate fallback
                    self.width, self.height = 1080, 1920

    # ── AUDIO HANDSHAKE (second socket — 4 bytes ONLY) ───────────────────────

    def _read_audio_handshake(self, s):
        """
        Read audio socket handshake. The audio socket is the SECOND socket,
        so it does NOT receive a dummy byte or device name. It gets ONLY
        4 bytes of codec metadata (the audio codec ID).
        
        BUG FIX: The original code read 77 bytes here (same as video),
        which consumed actual audio frame data as handshake bytes. This
        caused slow loading and broken audio.
        """
        try:
            meta = self._recv_exact(s, 4)              # Read ONLY 4 bytes (codec ID)
            codec_id = struct.unpack(">I", meta)[0]    # Unpack as big-endian u32
            codec_name = (                             # Convert to readable ASCII
                codec_id.to_bytes(4, 'big')
                .decode('ascii', 'replace')
                .strip('\x00')
            )
            log.info(f"Audio: codec=0x{codec_id:08x} ({codec_name})")
        except Exception as e:
            log.warning(f"Audio handshake failed: {e}")  # Non-fatal — video still works

    # ── SERVER LAUNCHER (with version auto-detection) ─────────────────────────

    def _launch_server(self):
        """
        Launch the scrcpy server on the Android device via ADB shell.
        If the jar version doesn't match, parse the error message to
        find the actual version and retry automatically.
        """
        global SCRCPY_VER                      # May update if version mismatch detected

        cmd = self._build_cmd(SCRCPY_VER)      # Build launch command with current version
        log.info(f"Launching scrcpy server (version {SCRCPY_VER})...")

        self.proc = subprocess.Popen(          # Start server as background process
            [ADB, "shell", cmd],               # Run via ADB shell
            stdout=subprocess.PIPE,            # Capture stdout
            stderr=subprocess.PIPE             # Capture stderr for error messages
        )
        time.sleep(1.2)                        # Wait for server to initialize

        # Check if server died immediately (usually a version mismatch)
        if self.proc.poll() is not None:       # poll() returns exit code if dead
            stderr = self.proc.stderr.read().decode("utf-8", errors="replace")

            # Look for version mismatch error: "server version (X.X.X) does not match"
            match = re.search(r'server version \(([0-9.]+)\)', stderr)
            if match:                          # Found the actual jar version
                actual_ver = match.group(1)    # Extract version string
                log.info(f"Version mismatch: jar is v{actual_ver}, retrying...")
                SCRCPY_VER = actual_ver        # Update global for future use

                cmd = self._build_cmd(actual_ver)  # Rebuild command with correct version
                self.proc = subprocess.Popen(      # Retry launch
                    [ADB, "shell", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                time.sleep(1.2)                    # Wait again

                if self.proc.poll() is not None:   # Still dead after retry
                    stderr2 = self.proc.stderr.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"scrcpy server failed after retry: {stderr2}")
            else:
                raise RuntimeError(f"scrcpy server failed: {stderr}")

        log.info("scrcpy server launched")

    def _build_cmd(self, version):
        """Build the ADB shell command string to launch scrcpy server."""
        return (
            "CLASSPATH=/data/local/tmp/scrcpy-server.jar "  # Java classpath
            "app_process / com.genymobile.scrcpy.Server "    # Android process launcher
            f"{version} "                   # Protocol version (must match jar)
            "tunnel_forward=true "          # Forward mode: device listens, we connect
            "video=true "                   # Enable video stream
            "audio=true "                   # Enable audio stream
            "control=true "                 # Enable control input (touch/keys)
            "video_codec=h264 "             # H.264 video (browser MSE compatible)
            "audio_codec=aac "              # AAC audio (browser MSE compatible)
            "video_bit_rate=8000000 "       # 8 Mbps video quality
            "audio_bit_rate=128000 "        # 128 kbps audio quality
            "max_fps=60 "                   # 60 FPS cap
            "max_size=0 "                   # Native resolution (no downscale)
            "lock_video_orientation=-1 "    # Follow device orientation
            "stay_awake=true "              # Keep phone awake while mirroring
            "show_touches=false "           # No touch circles on phone screen
            "power_off_on_close=false "     # Don't turn off phone on disconnect
            "clipboard_autosync=false "     # Skip clipboard sync
        )

    # ── STREAM LOOP (shared by video and audio threads) ───────────────────────

    def _stream_loop(self, sock, name, clients, lock, cfg_attr):
        """
        Read frames from a scrcpy socket and broadcast to WebSocket clients.
        
        scrcpy frame format (12-byte header + payload):
          [0-7]  pts_and_flags (u64 big-endian):
                   bit 63 = is_config (SPS/PPS for video, ASC for audio)
                   bits 0-62 = presentation timestamp
          [8-11] packet_size (u32 big-endian): payload byte count
          [12..] payload: raw H.264 or AAC data
        """
        log.info(f"{name} loop started")
        frame_index = 0                        # Counter for synthetic PTS generation
        while self.running:                    # Loop until session is stopped
            try:
                hdr      = self._recv_exact(sock, 12)              # Read 12-byte header
                pts_raw  = struct.unpack(">Q", hdr[:8])[0]         # Unpack PTS + flags
                pkt_size = struct.unpack(">I", hdr[8:12])[0]       # Unpack payload size

                if pkt_size == 0 or pkt_size > 20_000_000:         # Sanity check
                    log.warning(f"{name}: odd pkt size {pkt_size}")
                    continue                                       # Skip bad packets

                data      = self._recv_exact(sock, pkt_size)       # Read the frame payload
                is_config = bool(pts_raw >> 63)                    # Bit 63 = config flag
                pts_us    = frame_index * 33333                    # Synthetic PTS (~30fps)
                if not is_config:              # Only increment for non-config frames
                    frame_index += 1

                # Broadcast this frame to all subscribed WebSocket clients
                self._broadcast(data, is_config, pts_us, clients, lock, cfg_attr)

            except Exception as e:
                if self.running:               # Only log if we didn't intentionally stop
                    log.error(f"{name} loop error: {e}")
                break                          # Exit loop on any error
        log.info(f"{name} loop ended")

    def _broadcast(self, data, is_config, pts_us, clients, lock, cfg_attr):
        """
        Send a frame to all subscribed WebSocket clients.
        
        Wire format sent to browser: [flags(1 byte)][pts(8 bytes LE)][payload]
          flags: bit 0 = is_config (1 = SPS/PPS or AudioSpecificConfig)
        """
        # Pack the header + payload into one binary message
        payload = struct.pack("<BQ", 0x01 if is_config else 0x00, pts_us) + data
        if is_config:                          # Cache config for late-joining clients
            setattr(self, cfg_attr, payload)   # Store as self.last_video_cfg or last_audio_cfg
        dead = set()                           # Track disconnected clients
        with lock:
            cl = list(clients)                 # Snapshot the client set (thread-safe)
        for ws in cl:                          # Send to each client
            try:
                ws.send(payload)               # Send binary WebSocket message
            except Exception:
                dead.add(ws)                   # Mark failed sends for removal
        if dead:
            with lock:
                clients -= dead                # Remove dead clients from the set

    # ── CLIENT SUBSCRIBE/UNSUBSCRIBE ──────────────────────────────────────────

    def subscribe_video(self, ws):
        """Add a WebSocket client to the video broadcast list."""
        if self.last_video_cfg:                # If we have cached SPS/PPS
            try: ws.send(self.last_video_cfg)  # Send immediately so client can init MSE
            except: pass
        with self.video_lock:
            self.video_clients.add(ws)         # Add to broadcast set

    def unsubscribe_video(self, ws):
        """Remove a WebSocket client from the video broadcast list."""
        with self.video_lock:
            self.video_clients.discard(ws)     # Remove (no error if not found)

    def subscribe_audio(self, ws):
        """Add a WebSocket client to the audio broadcast list."""
        if self.last_audio_cfg:                # If we have cached AudioSpecificConfig
            try: ws.send(self.last_audio_cfg)  # Send immediately
            except: pass
        with self.audio_lock:
            self.audio_clients.add(ws)         # Add to broadcast set

    def unsubscribe_audio(self, ws):
        """Remove a WebSocket client from the audio broadcast list."""
        with self.audio_lock:
            self.audio_clients.discard(ws)     # Remove

    def send_control(self, data):
        """Send a binary control message (touch/key/scroll) to the phone."""
        if self.control_sock and self.running:  # Only if socket exists and session is active
            try:
                self.control_sock.sendall(data) # Send all bytes reliably
            except Exception as e:
                log.warning(f"Control send error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL MESSAGE BUILDERS (scrcpy binary protocol)
# These build the exact byte sequences that scrcpy expects.
# PROVEN WORKING — do not modify.
# ══════════════════════════════════════════════════════════════════════════════

SC_TYPE_INJECT_KEYCODE    = 0   # Message type: key press/release
SC_TYPE_INJECT_TEXT       = 1   # Message type: text input
SC_TYPE_INJECT_TOUCH      = 2   # Message type: touch event
SC_TYPE_INJECT_SCROLL     = 3   # Message type: scroll event
SC_TYPE_BACK_OR_SCREEN_ON = 4   # Message type: back button or wake screen
ACTION_DOWN = 0                 # Touch/key action: finger down / key pressed
ACTION_UP   = 1                 # Touch/key action: finger up / key released
ACTION_MOVE = 2                 # Touch action: finger moved
KEY_DOWN    = 0                 # Key action: pressed
KEY_UP      = 1                 # Key action: released
POINTER_ID_MOUSE = 0xFFFFFFFFFFFFFFFE  # Pointer ID for generic finger input

def ctrl_touch(action, x, y, w, h, pressure=1.0):
    """Build a 32-byte touch control message (PROVEN WORKING — do not modify)."""
    press = int(max(0.0, min(1.0, pressure)) * 65535)  # Float pressure → u16 fixed-point
    return struct.pack(">BBQiiHHHII",  # Big-endian binary format
        SC_TYPE_INJECT_TOUCH,          # [0]    type = 2
        action,                        # [1]    action: 0=down, 1=up, 2=move
        POINTER_ID_MOUSE,              # [2-9]  pointer ID (u64)
        int(x), int(y),                # [10-17] x,y coordinates (i32, i32)
        int(w), int(h),                # [18-21] screen width,height (u16, u16)
        press,                         # [22-23] pressure (u16)
        1 if action == ACTION_DOWN else 0,  # [24-27] action_button (u32)
        0 if action == ACTION_UP   else 1)  # [28-31] buttons held (u32)

def ctrl_scroll(x, y, w, h, hscroll, vscroll):
    """Build a scroll control message (PROVEN WORKING — do not modify)."""
    return struct.pack(">BiiHHffI",    # Big-endian binary format
        SC_TYPE_INJECT_SCROLL,         # type = 3
        int(x), int(y),               # scroll position
        int(w), int(h),                # screen dimensions
        float(hscroll), float(vscroll),# scroll amounts (float32)
        0)                             # buttons = 0

def ctrl_keycode(action, keycode, meta=0):
    """Build a keycode control message (PROVEN WORKING — do not modify)."""
    return struct.pack(">BBiii",       # Big-endian binary format
        SC_TYPE_INJECT_KEYCODE,        # type = 0
        action,                        # 0=down, 1=up
        keycode,                       # Android keycode (e.g. 3=HOME, 4=BACK)
        0,                             # repeat count
        meta)                          # meta keys (shift/ctrl/alt)

def ctrl_text(text):
    """Build a text input control message (PROVEN WORKING — do not modify)."""
    enc = text.encode("utf-8")         # Encode text as UTF-8 bytes
    return struct.pack(">BI",          # type(u8) + length(u32)
        SC_TYPE_INJECT_TEXT,           # type = 1
        len(enc)) + enc               # length + text bytes

def ctrl_back_or_screen_on():
    """Build a back/screen-on control message (PROVEN WORKING — do not modify)."""
    return struct.pack(">BB",          # type(u8) + action(u8)
        SC_TYPE_BACK_OR_SCREEN_ON,     # type = 4
        KEY_DOWN)                      # action = key down


# ══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@sock.route("/ws")
def ws_video(ws):
    """
    WebSocket endpoint: video stream + control commands.
    Sends binary video frames TO the browser.
    Receives JSON touch/key/scroll commands FROM the browser.
    PROVEN WORKING — do not modify touch handling.
    """
    global active_session
    with session_lock:
        session = active_session               # Grab current session (thread-safe)

    if not session or not session.running:      # No active session
        ws.send(json.dumps({"type": "error", "msg": "No active session. Click Connect."}))
        return

    session.subscribe_video(ws)                # Register for video broadcasts
    ws.send(json.dumps({                       # Send device info to initialize frontend
        "type":   "device_info",
        "name":   session.device_name,
        "width":  session.width,
        "height": session.height,
    }))

    try:
        while True:                            # Main receive loop
            raw = ws.receive()                 # Block until message arrives
            if raw is None: break              # Client disconnected
            try: msg = json.loads(raw)         # Parse JSON command
            except: continue                   # Skip malformed messages
            t = msg.get("type")                # Get command type
            sw = session.width                 # Current screen width
            sh = session.height                # Current screen height
            # ── Route each command type to its handler ────────────────────
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
        session.unsubscribe_video(ws)          # Unregister on disconnect


@sock.route("/ws/audio")
def ws_audio(ws):
    """
    WebSocket endpoint: audio stream only.
    Sends binary AAC frames TO the browser.
    """
    global active_session
    with session_lock:
        session = active_session               # Grab current session

    if not session or not session.running:
        ws.send(json.dumps({"type": "error", "msg": "No audio session"}))
        return

    session.subscribe_audio(ws)                # Register for audio broadcasts
    try:
        while True:
            raw = ws.receive()                 # Keep connection alive
            if raw is None: break              # Client disconnected
    except Exception as e:
        log.debug(f"Audio WS disconnect: {e}")
    finally:
        session.unsubscribe_audio(ws)          # Unregister on disconnect


# ══════════════════════════════════════════════════════════════════════════════
# REST API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/devices")
def api_devices():
    """Return list of connected ADB devices as JSON."""
    return json.dumps({"devices": adb_devices()})

@app.route("/api/status")
def api_status():
    """Return session status (running, name, width, height) as JSON."""
    with session_lock:
        s = active_session
    if s and s.running:
        return json.dumps({"running": True, "name": s.device_name, "width": s.width, "height": s.height})
    return json.dumps({"running": False})

@app.route("/api/start", methods=["POST"])
def api_start():
    """Start a new scrcpy session (stops existing one first)."""
    global active_session
    with session_lock:
        if active_session and active_session.running:
            active_session.stop()              # Stop old session
        try:
            s = ScrcpySession()                # Create new session
            s.start()                          # Start it
            active_session = s                 # Store globally
            return json.dumps({"ok": True, "name": s.device_name, "width": s.width, "height": s.height})
        except Exception as e:
            log.error(f"Start failed: {e}")
            return json.dumps({"ok": False, "error": str(e)}), 500

@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the active scrcpy session."""
    global active_session
    with session_lock:
        if active_session:
            active_session.stop()
            active_session = None
    return json.dumps({"ok": True})

@app.route("/api/keycode", methods=["POST"])
def api_keycode():
    """Send a keycode (press+release) to the phone. Used by Lua navbar."""
    global active_session
    with session_lock:
        s = active_session
    if not s or not s.running:
        return json.dumps({"ok": False, "error": "no session"}), 400
    kc = int(flask_request.get_json(force=True).get("keycode", 0))
    s.send_control(ctrl_keycode(KEY_DOWN, kc))  # Key press
    s.send_control(ctrl_keycode(KEY_UP,   kc))  # Key release
    return json.dumps({"ok": True})

# ── Static file serving ───────────────────────────────────────────────────────

@app.route("/")
def index():
    """Redirect root URL to /v2."""
    from flask import redirect
    return redirect("/v2", code=302)

@app.route("/v2")
def index_v2():
    """Serve index.html with aggressive no-cache headers."""
    from flask import make_response
    resp = make_response(send_from_directory("static", "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"        # HTTP 1.0 compat
    resp.headers["Expires"] = "-1"             # Already expired
    return resp

@app.route("/<path:path>")
def static_files(path):
    """Serve any other static file with no-cache."""
    from flask import make_response
    resp = make_response(send_from_directory("static", path))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Check Python dependencies are installed
    missing = []
    try: import flask
    except ImportError: missing.append("flask")
    try: import flask_sock
    except ImportError: missing.append("flask-sock")
    if missing:
        print(f"Missing: pip install {' '.join(missing)}")
        sys.exit(1)

    # Check ADB is available
    out, _, rc = adb_run("version")
    if rc != 0:
        print("ERROR: adb not found. Install Android Platform Tools and add to PATH.")
        sys.exit(1)
    log.info(f"ADB: {out.splitlines()[0]}")

    # Ensure scrcpy-server.jar exists
    if not ensure_jar():
        print(f"\nManual download: {SCRCPY_URL}")
        print(f"Save as:         {SCRCPY_JAR}\n")

    # Auto-start session if a device is already connected
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

    # Start the Flask HTTP/WebSocket server (blocks until Ctrl+C)
    log.info(f"\n  Open http://localhost:{PORT}  in Chrome or Edge\n")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
