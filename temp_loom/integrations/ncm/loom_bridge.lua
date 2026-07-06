-- ============================================================
-- loom_bridge.lua  —  the seam between ncm.html and Night City Motorsports.
--
-- It does NOT drive the car. It controls STATE: open a race config, stage,
-- green-flag, abort — the same calls the in-game hotkeys already make
-- (Racing.openQuickRaceConfig / stageNow / startRace / abort). And it
-- publishes a read-only snapshot (state, grid, results, standings) the
-- portal polls. Browser <-> a local relay <-> these two files <-> CET.
--
-- Wiring (in init.lua):
--   local LoomBridge = require("modules/loom_bridge")   -- with the other requires
--   MTE.LoomBridge = LoomBridge                          -- in the MTE table
--   LoomBridge.init(MTE)                                 -- at the end of onInit
--   if LoomBridge and LoomBridge.update then LoomBridge.update(dt) end  -- in onUpdate
--
-- The relay (python) creates <mod>/bridge/ and owns the files; this side
-- only reads command.txt and writes state.json. Pure io, no sockets.
-- ============================================================

local Bridge = {}

-- CET resolves io paths relative to the game's bin/x64. This is that path to
-- the mod's bridge folder. Override Bridge.dir before init() if your layout differs.
Bridge.dir = "plugins/cyber_engine_tweaks/mods/MT_Ecosystem/bridge/"
Bridge.POLL_SECONDS = 0.3

-- ── tiny JSON encoder (write-only; the command side is line-based) ──────────
local function enc(v)
    local t = type(v)
    if t == "nil" then
        return "null"
    elseif t == "boolean" then
        return v and "true" or "false"
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then return "null" end
        return tostring(v)
    elseif t == "string" then
        return '"' .. v:gsub('[%z\1-\31\\"]', function(c)
            local m = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
            return m[c] or string.format('\\u%04x', string.byte(c))
        end) .. '"'
    elseif t == "table" then
        local n = 0
        for _ in pairs(v) do n = n + 1 end
        if n > 0 and n == #v then                      -- dense array
            local parts = {}
            for i = 1, #v do parts[i] = enc(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local parts = {}
        for k, val in pairs(v) do
            parts[#parts + 1] = '"' .. tostring(k) .. '":' .. enc(val)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return "null"
end

-- ── intents -> the EXISTING race-control surface (pcall-guarded) ────────────
local function dispatch(intent)
    local R = MTE and MTE.Racing
    if not R then print("[LoomBridge] Racing module unavailable"); return end
    local map = {
        open  = function() return R.openQuickRaceConfig and R.openQuickRaceConfig() end,
        stage = function() return R.stageNow and R.stageNow() end,
        start = function() return R.startRace and R.startRace() end,
        quali = function() return R.startQuali and R.startQuali() end,
        abort = function() return R.abort and R.abort() end,
    }
    local fn = map[intent]
    if not fn then print("[LoomBridge] unknown intent: " .. tostring(intent)); return end
    local ok, err = pcall(fn)
    if not ok then print(string.format("[LoomBridge] %s failed: %s", intent, tostring(err))) end
end

-- ── the read-only snapshot the portal renders ──────────────────────────────
local function snapshot()
    local R = MTE and MTE.Racing
    local snap = {
        connected = true,
        ts = os.time(),
        seqAck = Bridge._lastSeq or 0,
        state = (R and R.getState and R.getState()) or "unknown",
    }
    pcall(function()
        local field, out = R and R.getRaceEntries and R.getRaceEntries(), {}
        if type(field) == "table" then
            for i, e in ipairs(field) do
                out[i] = {
                    pos  = e.position or e.pos or i,
                    name = e.name or e.driverName or e.displayName or ("Car " .. i),
                    gap  = e.gap or e.gapText,
                    you  = e.isPlayer == true or e.player == true or nil,
                }
            end
        end
        snap.field = out
    end)
    pcall(function()
        local r = R and R.getResults and R.getResults()
        if type(r) == "table" then snap.results = r end
    end)
    pcall(function()
        local C = MTE.Championships
        local car = MTE.Careers and MTE.Careers.active and MTE.Careers.active()
        if C and C.activeSeason and C.driverStandings and car then
            local season = C.activeSeason(car)
            local rows = season and C.driverStandings(season) or nil
            if type(rows) == "table" then
                local out = {}
                for i, row in ipairs(rows) do
                    out[i] = { pos = i, name = row.name or row.driverId or "?", points = row.points or 0 }
                    if i >= 12 then break end
                end
                snap.standings = out
            end
        end
    end)
    pcall(function()
        local car = MTE.Careers and MTE.Careers.active and MTE.Careers.active()
        if car then snap.career = { name = car.name, stats = car.stats } end
    end)
    return snap
end

local function writeState()
    local ok, js = pcall(enc, snapshot())
    if not ok then return end
    local f = io.open(Bridge.statePath, "w")
    if f then f:write(js); f:close() end
end

local function readCommand()
    local f = io.open(Bridge.cmdPath, "r")
    if not f then return end
    local raw = f:read("*a"); f:close()
    if not raw or raw == "" then return end
    local seq = tonumber(raw:match("seq=(%d+)"))
    local intent = raw:match("intent=([%w_]+)")
    if seq and intent and seq ~= Bridge._lastSeq then
        Bridge._lastSeq = seq
        dispatch(intent)
    end
end

-- ── lifecycle ───────────────────────────────────────────────────────────────
function Bridge.init(_mte)
    Bridge._lastSeq = -1
    Bridge._acc = 0
    Bridge.cmdPath = Bridge.dir .. "command.txt"
    Bridge.statePath = Bridge.dir .. "state.json"
    print("[LoomBridge] ready — bridge dir: " .. Bridge.dir)
end

function Bridge.update(dt)
    Bridge._acc = (Bridge._acc or 0) + (dt or 0)
    if Bridge._acc < Bridge.POLL_SECONDS then return end
    Bridge._acc = 0
    pcall(readCommand)
    pcall(writeState)
end

return Bridge
