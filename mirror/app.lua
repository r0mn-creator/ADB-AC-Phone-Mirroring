local WebBrowser = require('shared/web/browser')

-- ── Config ────────────────────────────────────────────────────────────────────
local ADB_HOST = ac.load('.ADBPhone.host') or 'localhost'
local ADB_PORT = ac.load('.ADBPhone.port') or '7070'
local ADB_URL  = 'http://' .. ADB_HOST .. ':' .. ADB_PORT .. '/v2'

local btnSize = vec2(32, 32)

-- ── State ─────────────────────────────────────────────────────────────────────
local browser      = nil
local navbar       = 1
local navbarActive = 1
local navbarHold   = 0
local keyboard     = false

-- ── Phone key helper (Back/Home/Recents/Volume via REST) ──────────────────────
local function phoneKey(keycode)
  web.post('http://' .. ADB_HOST .. ':' .. ADB_PORT .. '/api/keycode',
    { keycode = keycode }, function () end)
end

-- ── Create the browser ────────────────────────────────────────────────────────
local function getBrowser()
  if browser then return browser end

  browser = WebBrowser({
    dataKey          = 'adbphone',
    backgroundColor  = rgbm.colors.black,
    redirectAudio    = false,
    spoofGeolocation = false,
  })
  :onDrawEmpty('message')
  :setMobileMode('landscape')
  :navigate(ADB_URL)
  :setColorScheme('dark')
  :setPixelDensity(1.0)
  :setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
  :drawTouches(rgbm(1, 1, 1, 0.2))
  :blockURLs(WebBrowser.adsFilter())

  return browser
end

-- ── Main loop ─────────────────────────────────────────────────────────────────
return function (dt)
  touchscreen.forceAwake()
  system.fullscreen()
  touchscreen.boostFrameRate()

  local page = getBrowser()
  local size = ui.availableSpace()

  -- Draw browser fullscreen
  ui.dummy(size)
  page:resize(size)
  local r1, r2 = ui.itemRect()
  if not page:focused() then page:focus(true) end
  page:draw(r1, r2, false)

  -- Touch routing
  if touchscreen.touched() then
    local pos  = ui.mouseLocalPos()
    local navH = 32 * navbar
    if pos.y > navH and pos.y < size.y - math.max(0, touchscreen.keyboardOffset() - system.bottomBarHeight) then
      page:touchInput({ pos:div(size) })
      if ui.mouseDelta().y > 20 then
        navbarActive = 1
        navbarHold   = os.preciseClock() + 0.6
      elseif navbarHold < os.preciseClock() then
        navbarActive = 0
      end
    else
      page:touchInput({})
    end
  else
    page:touchInput({})
    keyboard = page:requestedVirtualKeyboard()
  end

  -- Virtual keyboard
  if keyboard then
    local c = touchscreen.inputBehaviour()
    if type(c) == 'string' then
      page:textInput(c)
    elseif type(c) == 'number' then
      page:keyEvent(c, false)
      page:keyEvent(c, true)
    end
  end

  -- Navbar
  navbar = math.applyLag(navbar, navbarActive, 0.8, dt)
  local y = -32 + 32 * navbar

  if navbar > 0.01 or page:loading() then
    ui.setCursor(vec2(0, y))
    ui.childWindow('##navbar', vec2(ui.windowWidth(), 33), function ()

      if navbar > 0.01 then
        ui.drawRectFilled(vec2(), vec2(ui.windowWidth(), 32), rgbm(0.06, 0.06, 0.06, 0.96))

        -- Browser nav
        ui.setCursorX(4) ui.setCursorY(0)
        if touchscreen.button('##back', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowLeft, 14, not page:canGoBack()) then
          page:navigate('back')
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        if touchscreen.button('##fwd', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowRight, 14, not page:canGoForward()) then
          page:navigate('forward')
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        if touchscreen.button('##reload', btnSize, rgbm.colors.transparent, 0, ui.Icons.Restart, 14) then
          page:reload(true)
        end

        -- Divider
        ui.sameLine(0, 8) ui.setCursorY(8)
        ui.drawSimpleLine(ui.cursor(), ui.cursor() + vec2(0, 16), rgbm(0.28,0.28,0.28,1), 1)
        ui.sameLine(0, 8) ui.setCursorY(0)

        -- Android system keys
        if touchscreen.button('##aback', btnSize, rgbm.colors.transparent, 0, ui.Icons.ArrowLeft, 12) then
          phoneKey(4)    -- KEYCODE_BACK
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        if touchscreen.button('##ahome', btnSize, rgbm.colors.transparent, 0, ui.Icons.Home, 14) then
          phoneKey(3)    -- KEYCODE_HOME
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        if touchscreen.button('##arecents', btnSize, rgbm.colors.transparent, 0, ui.Icons.Skip, 12) then
          phoneKey(187)  -- KEYCODE_APP_SWITCH
        end

        -- Divider
        ui.sameLine(0, 8) ui.setCursorY(8)
        ui.drawSimpleLine(ui.cursor(), ui.cursor() + vec2(0, 16), rgbm(0.28,0.28,0.28,1), 1)
        ui.sameLine(0, 8) ui.setCursorY(0)

        -- Reconnect
        if touchscreen.button('##reconnect', btnSize, rgbm.colors.transparent, 0, ui.Icons.Undo, 13) then
          page:navigate(ADB_URL)
        end

        -- Volume right-aligned
        ui.sameLine(0, math.max(4, ui.availableSpaceX() - 72))
        ui.setCursorY(0)
        if touchscreen.button('##volu', btnSize, rgbm.colors.transparent, 0, ui.Icons.Plus, 13) then
          phoneKey(24)   -- KEYCODE_VOLUME_UP
        end
        ui.sameLine(0, 4) ui.setCursorY(0)
        if touchscreen.button('##vold', btnSize, rgbm.colors.transparent, 0, ui.Icons.Minus, 13) then
          phoneKey(25)   -- KEYCODE_VOLUME_DOWN
        end
      end

      -- Loading bar
      ui.drawSimpleLine(vec2(0,32), vec2(ui.windowWidth(),32), rgbm(0.22,0.22,0.22,navbar), 2)
      if page:loading() then
        ui.drawSimpleLine(vec2(0,32), vec2(ui.windowWidth()*page:loadingProgress(),32), rgbm(0,0.5,1,1), 2)
      end
    end)
  end
end
