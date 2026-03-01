# ADB Web Controller — High Performance Edition

Stream your Android screen to a browser at 30–60fps using the scrcpy server
protocol with native H.264 hardware decoding via the WebCodecs API.

---

## Quick Start

1. **Double-click `START.bat`**
2. Open **http://localhost:7070** in Chrome or Edge
3. Click **Connect** in the sidebar

That's it. The `scrcpy-server.jar` is downloaded automatically on first run.

---

## Requirements

| Tool | Notes |
|------|-------|
| **Python 3.8+** | https://www.python.org/downloads/ — check "Add to PATH" |
| **ADB** | https://developer.android.com/tools/releases/platform-tools |
| **Chrome or Edge 94+** | WebCodecs API is required for H.264 decode |
| **USB Debugging** | Settings → Developer Options → USB Debugging |

### Python packages (auto-installed by START.bat)
```
pip install flask flask-sock
```

---

## Phone Setup

1. Enable **Developer Options**:  
   Settings → About Phone → tap **Build Number** 7 times

2. Enable **USB Debugging**:  
   Settings → Developer Options → USB Debugging → ON

3. Connect via USB and accept the "Allow USB Debugging?" prompt on your phone

### WiFi ADB (optional, after USB pairing)
```
adb tcpip 5555
adb connect <phone-ip>:5555
```
Then you can unplug the USB cable.

---

## Features

- **60fps H.264 video** — hardware decoded in browser via WebCodecs
- **Full touch** — click, drag, scroll all work as native touch inputs
- **Keyboard** — type directly when the screen is focused; special keys mapped
- **Type Text** button — paste longer text strings to the phone
- **Paste Clipboard** — sends your PC clipboard text to the phone
- **Navigation** — Back, Home, Recents, Power, Menu buttons
- **Volume** — Up, Down, Mute
- **Screenshot** — saves a lossless PNG from the current frame
- **Quick Keys** panel — arrows, Enter, Delete, Page Up/Down, Cut/Copy/Paste/SelectAll

---

## Performance

The scrcpy server streams H.264 directly over ADB at up to 60fps with an 8Mbps
bitrate. The browser decodes it using hardware acceleration (GPU). Typical
end-to-end latency is **30–80ms** on USB, **80–200ms** on WiFi.

To tune performance, edit the top of `server.py`:

```python
VIDEO_BIT_RATE = 8000000   # 8 Mbps — lower for WiFi
MAX_FPS        = 60        # cap framerate
```

---

## Troubleshooting

**"No device connected"**  
→ Run `adb devices` in cmd — is your device listed as `device` (not `unauthorized`)?  
→ Check you accepted the debugging prompt on the phone.

**Black screen / no video**  
→ The scrcpy-server.jar version must match `SCRCPY_VER` in server.py.  
→ Delete `scrcpy-server.jar` and restart to re-download.

**"WebCodecs not supported"**  
→ Use Chrome 94+ or Edge 94+. Firefox does not support WebCodecs.

**Port 7070 already in use**  
→ Edit `PORT = 7070` in `server.py`.

---

## Architecture

```
Phone
  └─ scrcpy-server.jar (pushed by ADB)
       ├─ H.264 video stream  ──┐
       └─ Control socket     ──┤
                               │ ADB tunnel (USB or WiFi)
Windows PC                     │
  └─ server.py (Python/Flask)  │
       ├─ /ws  ◄───────────────┘  (proxies binary H.264 + control)
       └─ /    (serves index.html)
              │
              ▼
Browser (Chrome/Edge)
  └─ index.html
       ├─ WebSocket (binary) → VideoDecoder (WebCodecs) → <canvas>
       └─ Mouse/keyboard events → JSON → WebSocket → control socket → phone
```

---

## Files

```
adb_web_controller/
  server.py            ← Python backend
  static/
    index.html         ← Browser frontend  
  START.bat            ← Windows launcher
  scrcpy-server.jar    ← Downloaded automatically on first run
```
