--[[
  Phone Mirror — app.lua
  =======================
  CSP touchscreen app for Assetto Corsa. Hosts a CEF (Chromium) browser
  widget that loads the Phone Mirror web frontend from the Python server.
  
  Goes in: <AC_ROOT>/extension/lua/joypad-assist/android_auto/apps/mirror/app.lua
]]

-- Load the shared WebBrowser module from CSP's built-in library
local WebBrowser = require('shared/web/browser')  -- CSP's CEF browser wrapper

-- ── Config: server address (must match server.py HOST:PORT) ──────────────
local ADB_HOST = ac.load('.ADBPhone.host') or 'localhost'  -- Load saved host or default
local ADB_PORT = ac.load('.ADBPhone.port') or '7070'       -- Load saved port or default
local ADB_URL  = 'http://' .. ADB_HOST .. ':' .. ADB_PORT .. '/v2'  -- Full URL to the frontend

-- ── UI constants ─────────────────────────────────────────────────────────
local btnSize = vec2(32, 32)  -- Size of navbar buttons (32x32 pixels)

-- ── State variables ──────────────────────────────────────────────────────
local browser      = nil   -- CEF WebBrowser instance (created on first frame)
local navbar       = 1     -- Navbar visibility (1=visible, 0=hidden, animated)
local navbarActive = 1     -- Target navbar state (1=show, 0=hide)
local navbarHold   = 0     -- Timestamp: navbar stays visible until this time
local keyboard     = false -- True when phone requested virtual keyboard

-- ── Phone key helper: send keycode via REST API ──────────────────────────
local function phoneKey(keycode)
  -- POST to /api/keycode to send a key press+release to the phone
  web.post(
    'http://' .. ADB_HOST .. ':' .. ADB_PORT .. '/api/keycode',  -- API endpoint
    { keycode = keycode },  -- JSON body with the Android keycode
    function () end         -- Empty callback (fire and forget)
  )
end

-- ── Create the CEF browser instance ──────────────────────────────────────
local function getBrowser()
  if browser then return browser end  -- Return existing instance if already created

  browser = WebBrowser({              -- Create new CEF browser with these settings:
    dataKey          = 'adbphone',    -- Storage key for cookies/cache persistence
    backgroundColor  = rgbm.colors.black,  -- Black background behind the page
    redirectAudio    = true,          -- Route phone audio through AC speakers
    spoofGeolocation = false,         -- Don't fake GPS location
  })
  :onDrawEmpty('message')             -- Show "message" placeholder when page is empty
  :setMobileMode('landscape')         -- Hint browser to use landscape layout
  :navigate(ADB_URL)                  -- Load the Phone Mirror frontend URL
  :setColorScheme('dark')             -- Use dark color scheme for the browser
  :setPixelDensity(1.0)              -- 1:1 pixel mapping (no DPI scaling)
  :setUserAgent(                      -- Spoof as desktop Chrome (for MSE compatibility)
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    .. 'AppleWebKit/537.36 (KHTML, like Gecko) '
    .. 'Chrome/124.0.0.0 Safari/537.36'
  )
  :drawTouches(rgbm(1, 1, 1, 0.2))   -- Show subtle white touch indicators
  :blockURLs(WebBrowser.adsFilter())  -- Block ads in the browser

  return browser  -- Return the new browser instance
end

-- ══════════════════════════════════════════════════════════════════════════
-- MAIN DRAW LOOP — called every frame by CSP
-- ══════════════════════════════════════════════════════════════════════════

return function (dt)  -- dt = delta time since last frame (seconds)
  touchscreen.forceAwake()       -- Keep full frame rate (CSP throttles to 1fps when idle)
  system.fullscreen()            -- Use full display area (no window chrome)
  touchscreen.boostFrameRate()   -- Request highest possible frame rate

  local page = getBrowser()      -- Get or create the browser instance
  local size = ui.availableSpace()  -- Get the drawable area dimensions

  -- Sync AC volume slider to browser audio output every frame
  if page and page.setAudioVolume then     -- Check method exists (CSP version compat)
    page:setAudioVolume(touchscreen.getVolume())  -- Apply AC volume to phone audio
  end

  -- Draw the browser fullscreen
  ui.dummy(size)                 -- Reserve the full display area
  page:resize(size)              -- Tell browser to match display size
  local r1, r2 = ui.itemRect()  -- Get the corners of the reserved area
  if not page:focused() then     -- Ensure browser has input focus
    page:focus(true)             -- Give focus (needed for keyboard input)
  end
  page:draw(r1, r2, false)      -- Draw the browser content to the display

  -- ── Touch routing ──────────────────────────────────────────────────────
  if touchscreen.touched() then          -- User is touching the display
    local pos  = ui.mouseLocalPos()      -- Get touch position in local coordinates
    local navH = 32 * navbar             -- Current navbar height (animated)
    -- Only send touch to browser if within the video area (not navbar, not keyboard)
    if pos.y > navH and pos.y < size.y - math.max(0, touchscreen.keyboardOffset() - system.bottomBarHeight) then
      page:touchInput({ pos:div(size) }) -- Forward touch to browser (normalized coordinates)
      if ui.mouseDelta().y > 20 then     -- Swipe down detected
        navbarActive = 1                 -- Show navbar
        navbarHold   = os.preciseClock() + 0.6  -- Keep it visible for 0.6 seconds
      elseif navbarHold < os.preciseClock() then
        navbarActive = 0                 -- Hide navbar after hold timer expires
      end
    else
      page:touchInput({})                -- No touch (outside video area)
    end
  else
    page:touchInput({})                  -- No touch (finger lifted)
    keyboard = page:requestedVirtualKeyboard()  -- Check if phone wants a keyboard
  end

  -- ── Virtual keyboard support ───────────────────────────────────────────
  if keyboard then
    local c = touchscreen.inputBehaviour()  -- Get keyboard input
    if type(c) == 'string' then          -- Text input
      page:textInput(c)                  -- Forward text to browser
    elseif type(c) == 'number' then      -- Key code
      page:keyEvent(c, false)            -- Key down
      page:keyEvent(c, true)             -- Key up
    end
  end

  -- ── Navbar (animated slide in/out from top) ────────────────────────────
  navbar = math.applyLag(navbar, navbarActive, 0.8, dt)  -- Smooth animation
  local y = -32 + 32 * navbar           -- Y position (slides in/out)

  if navbar > 0.01 or page:loading() then  -- Show if visible or page is loading
    ui.setCursor(vec2(0, y))             -- Position at top of display
    ui.childWindow('##navbar', vec2(ui.windowWidth(), 33), function ()

      if navbar > 0.01 then             -- Only draw buttons if navbar is visible
        -- Background bar
        ui.drawRectFilled(vec2(), vec2(ui.windowWidth(), 32), rgbm(0.06, 0.06, 0.06, 0.96))

        -- ── Browser navigation buttons ───────────────────────────────
        ui.setCursorX(4) ui.setCursorY(0)
        -- Back button (browser history)
        if touchscreen.button('##back', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowLeft, 14, not page:canGoBack()) then
          page:navigate('back')          -- Go back in browser history
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        -- Forward button (browser history)
        if touchscreen.button('##fwd', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowRight, 14, not page:canGoForward()) then
          page:navigate('forward')       -- Go forward in browser history
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        -- Reload button
        if touchscreen.button('##reload', btnSize, rgbm.colors.transparent, 0, ui.Icons.Restart, 14) then
          page:reload(true)              -- Hard reload the page
        end

        -- ── Divider line ─────────────────────────────────────────────
        ui.sameLine(0, 8) ui.setCursorY(8)
        ui.drawSimpleLine(ui.cursor(), ui.cursor() + vec2(0, 16), rgbm(0.28,0.28,0.28,1), 1)
        ui.sameLine(0, 8) ui.setCursorY(0)

        -- ── Android system key buttons ───────────────────────────────
        -- Back (Android KEYCODE_BACK = 4)
        if touchscreen.button('##aback', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowLeft, 12) then
          phoneKey(4)                    -- Send KEYCODE_BACK to phone
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        -- Home (Android KEYCODE_HOME = 3)
        if touchscreen.button('##ahome', btnSize, rgbm.colors.transparent, 0, ui.Icons.Home, 14) then
          phoneKey(3)                    -- Send KEYCODE_HOME to phone
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        -- Recent apps (Android KEYCODE_APP_SWITCH = 187)
        if touchscreen.button('##arecents', btnSize, rgbm.colors.transparent, 0, ui.Icons.Skip, 12) then
          phoneKey(187)                  -- Send KEYCODE_APP_SWITCH to phone
        end

        -- ── Divider line ─────────────────────────────────────────────
        ui.sameLine(0, 8) ui.setCursorY(8)
        ui.drawSimpleLine(ui.cursor(), ui.cursor() + vec2(0, 16), rgbm(0.28,0.28,0.28,1), 1)
        ui.sameLine(0, 8) ui.setCursorY(0)

        -- ── Reconnect button (reload the Phone Mirror page) ─────────
        if touchscreen.button('##reconnect', btnSize, rgbm.colors.transparent, 0, ui.Icons.Undo, 13) then
          page:navigate(ADB_URL)         -- Navigate back to the Phone Mirror URL
        end

        -- ── Volume buttons (right-aligned) ───────────────────────────
        ui.sameLine(0, math.max(4, ui.availableSpaceX() - 72))  -- Push to right edge
        ui.setCursorY(0)
        -- Volume up (Android KEYCODE_VOLUME_UP = 24)
        if touchscreen.button('##volu', btnSize, rgbm.colors.transparent, 0, ui.Icons.Plus, 13) then
          phoneKey(24)                   -- Send KEYCODE_VOLUME_UP to phone
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        -- Volume down (Android KEYCODE_VOLUME_DOWN = 25)
        if touchscreen.button('##vold', btnSize, rgbm.colors.transparent, 0, ui.Icons.Minus, 13) then
          phoneKey(25)                   -- Send KEYCODE_VOLUME_DOWN to phone
        end
      end

      -- ── Loading bar (blue progress bar at bottom of navbar) ────────
      ui.drawSimpleLine(vec2(0,32), vec2(ui.windowWidth(),32), rgbm(0.22,0.22,0.22,navbar), 2)
      if page:loading() then             -- Show progress while loading
        ui.drawSimpleLine(vec2(0,32), vec2(ui.windowWidth()*page:loadingProgress(),32), rgbm(0,0.5,1,1), 2)
      end
    end)
  end
end  -- End of main draw function
