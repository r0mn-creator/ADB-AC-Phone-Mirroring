# Phone Mirror for Assetto Corsa

Mirror your Android phone screen onto the in-car display in Assetto Corsa using Custom Shaders Patch (CSP).

## What It Does

- Streams your phone screen (H.264 video + AAC audio) to the in-car touchscreen
- Touch input on the AC display controls the phone (taps, swipes, scrolling)
- Rotate button to force landscape/portrait display
- Phone audio plays through Assetto Corsa
- Navigation buttons: Back, Home, Recent Apps, Volume Up/Down

---

## All Files Needed

### Folder 1: `adb_web_controller/` (Python server — runs on your PC)

| File | What It Does | Get It From |
|------|-------------|-------------|
| `server.py` | Python backend — talks to phone via ADB, streams to browser | Included |
| `static/index.html` | Browser frontend — decodes video, handles touch, rotate button | Included |
| `START.bat` | Double-click to launch the server | Included |
| `scrcpy-server.jar` | scrcpy server binary (pushed to phone) | See download below |

### Folder 2: `mirror/` (Lua app — goes inside AC)

| File | What It Does | Get It From |
|------|-------------|-------------|
| `app.lua` | CSP touchscreen app — hosts browser widget inside AC | Included |
| `manifest.ini` | App metadata (name, icon, description) | Included |
| `icon.png` | App icon shown in the AC touchscreen launcher | Included |

### Software You Need Installed

| Software | What For | Download Link |
|----------|---------|---------------|
| **Python 3.8+** | Runs the server | https://www.python.org/downloads/ |
| **Flask + flask-sock** | Python web framework | `pip install flask flask-sock` (auto-installed by START.bat) |
| **ADB (Android Debug Bridge)** | Communicates with the phone | https://developer.android.com/tools/releases/platform-tools |
| **scrcpy-server.jar** | Captures the phone screen | https://github.com/Genymobile/scrcpy/releases |
| **Assetto Corsa** | The game | Steam |
| **Custom Shaders Patch** | Adds the touchscreen display to AC | https://acstuff.ru/patch/ |

---

## Where Each File Goes

### Server files (adb_web_controller)

Put this folder **anywhere on your PC** — it runs independently. For example:

```
C:\PhoneMirror\
├── server.py              ← Python backend
├── START.bat              ← Double-click to launch
├── scrcpy-server.jar      ← Downloaded from scrcpy releases
└── static\
    └── index.html         ← Browser frontend (rotate button is here)
```

### Lua app files (mirror)

Put this folder inside AC's CSP android auto apps directory:

```
<AC_ROOT>\extension\lua\joypad-assist\android_auto\apps\mirror\
├── app.lua                ← CSP touchscreen app
├── manifest.ini           ← App metadata
└── icon.png               ← App launcher icon
```

Where `<AC_ROOT>` is your Assetto Corsa installation folder, for example:
```
C:\Steam\steamapps\common\assettocorsa\
```

So the full path to app.lua would be something like:
```
C:\Steam\steamapps\common\assettocorsa\extension\lua\joypad-assist\android_auto\apps\mirror\app.lua
```

---

## How to Get scrcpy-server.jar

1. Go to https://github.com/Genymobile/scrcpy/releases
2. Download the latest release zip (e.g. `scrcpy-win64-vX.X.X.zip`)
3. Open the zip and find the file called `scrcpy-server` (no extension) or `scrcpy-server.jar`
4. Copy it to the same folder as `server.py`
5. **Rename it to `scrcpy-server.jar`** if it doesn't already have the .jar extension

The server will auto-detect the version — you don't need to match any specific version.

---

## How to Set Up ADB

1. Download Android SDK Platform Tools from:
   https://developer.android.com/tools/releases/platform-tools
2. Extract the zip (e.g. to `C:\platform-tools\`)
3. Add the folder to your system PATH:
   - Press Win+R, type `sysdm.cpl`, press Enter
   - Go to Advanced → Environment Variables
   - Under System Variables, find `Path`, click Edit
   - Click New, paste `C:\platform-tools\`
   - Click OK on all dialogs
4. Open a new terminal and verify: `adb version`

---

## Phone Setup

1. On your Android phone, go to **Settings → About Phone**
2. Tap **Build Number** 7 times to enable Developer Options
3. Go to **Settings → Developer Options**
4. Enable **USB Debugging**
5. Connect phone to PC via USB cable
6. When prompted on the phone, tap **Allow** to authorize the computer
7. Verify connection: open a terminal and run `adb devices`
   - You should see your device listed as `device` (not `unauthorized`)

---

## How to Use

### Step 1: Start the server

Double-click `START.bat` (or run `python server.py` in a terminal).

You should see:
```
[INFO] Device found: XXXXXXXX — auto-starting...
[INFO] scrcpy server launched
[INFO] Session started: 'Your Phone' 1080x2400
[INFO] Open http://localhost:7070 in Chrome or Edge
```

### Step 2: Launch Assetto Corsa

1. Start AC with a car that has the Android Auto display
2. The "ADB Phone" app appears in the touchscreen launcher
3. Tap it — your phone screen appears on the in-car display
4. Touch the display to interact with your phone

### Step 3: Use the rotate button

If your phone is playing a landscape video but the display shows it in portrait:
- Tap the **rotate button** (bottom-right corner of the display, circular arrow icon)
- The display dimensions swap and the video fills the screen properly
- Tap again to swap back

---

## Car Configuration

Your car needs the Android Auto display configured in its `ext_config.ini`. Example:

```ini
[INCLUDE: android_auto/config.ini]
[Display_AndroidAuto]
Meshes = GEO_display_COMM       ; Your car's display mesh name
Resolution = 1024, 1024         ; Texture resolution
Size = 1013, 397                ; Screen area size in pixels
Offset = 5, 5                   ; Screen area offset
Scale = 1                       ; Adjust if needed
```

Optional performance improvements:

```ini
[EXTRA_FX]
GLASS_FILTER = GEO_display_COMM
SKIP_GBUFFER = center_interior_glass

[INCLUDE: common/materials_interior.ini]
[Material_DigitalScreen]
Materials = INT_displays_COMM
ScreenScale = 600
ScreenAspectRatio = 0.5
MatrixType = TN
```

---

## Troubleshooting

### "No ADB devices found"
- Check USB cable (use the cable that came with the phone)
- Enable USB debugging on the phone (Settings → Developer Options)
- Run `adb kill-server` then `adb start-server`
- On the phone, revoke USB debugging authorizations and re-authorize

### "scrcpy server failed: version mismatch"
- The server auto-detects the version. If it still fails, check the error message for the actual version and ensure the jar file is not corrupted.

### Server starts but no video in AC
- Make sure `server.py` is running BEFORE opening the app in AC
- Try opening `http://localhost:7070` in Chrome on your PC first to verify it works
- Check that `ADB_HOST` and `ADB_PORT` in `app.lua` match the server

### Touch works but no audio
- Audio requires `redirectAudio = true` in `app.lua` (already set)
- First touch on the display may be needed to start audio (browser autoplay policy)
- Check phone volume is not muted

### Rotate button not visible
- The button sits at the bottom-right with z-index 99999
- It has a near-opaque dark background with white border — should be visible on any content

---

## Architecture

```
┌──────────┐  ADB/scrcpy   ┌──────────────┐  WebSocket  ┌──────────────┐  CSP/CEF   ┌──────────┐
│  Android  │◄════════════►│  server.py   │◄══════════►│  index.html  │◄═════════►│  app.lua │
│  Phone    │  H.264+AAC   │  (Python)    │  binary     │  (Browser)   │  display   │  (AC)    │
│           │  +touch ctrl │  port 7070   │  frames     │              │           │          │
└──────────┘               └──────────────┘             └──────────────┘           └──────────┘
```

- **server.py**: Pushes scrcpy to phone, opens video/audio/control sockets, broadcasts frames over WebSocket, relays touch commands back
- **index.html**: Decodes H.264/AAC using Media Source Extensions (MSE), renders video, captures touch/scroll/keyboard, has rotate button
- **app.lua**: Hosts the CEF browser widget inside AC's touchscreen, routes touch events, syncs volume, shows navigation bar
