--[[
RadioOS Bridge — Cyber Engine Tweaks Autorun Script
=====================================================
Exports live Cyberpunk 2077 game state to a JSON file so ARIA (the RadioOS
AI companion) can read it and provide contextual commentary.

INSTALLATION
  1. Install Cyber Engine Tweaks (CET): https://www.nexusmods.com/cyberpunk2077/mods/107
  2. Copy this file to:
       %CP2077_INSTALL%\bin\x64\plugins\cyber_engine_tweaks\mods\RadioOSBridge\init.lua
     (Create the RadioOSBridge folder if it doesn't exist)
  3. Launch Cyberpunk 2077. ARIA will start receiving game state automatically.

OUTPUT FILE
  %USERPROFILE%\RadioOSBridge\cp2077_state.json
  Updated every 0.5 seconds while the game is running.

STATE FIELDS
  player_name           string  — character name
  health_pct            float   — 0.0 to 1.0
  is_alive              bool
  level                 int     — player level
  street_cred           int     — street cred level
  in_combat             bool
  enemy_count           int     — hostile NPCs in combat range
  nearest_enemy_m       float   — distance to nearest hostile (metres)
  wanted_level          int     — 0–5 stars
  district              string  — current district name
  location              string  — current sub-location / POI name
  coords_x              float
  coords_y              float
  active_quest          string  — current active journal quest name
  active_objective      string  — current quest objective text
  in_vehicle            bool
  vehicle_name          string
  vehicle_speed         float   — m/s when in vehicle
  vehicle_type          string  — car/bike/truck/av/unknown
  last_item             string  — most recently picked up item name
  nearby_poi            string  — nearest point of interest name
  nearby_poi_dist       float   — distance to POI (metres)
  weapon_name           string  — current equipped weapon display name
  weapon_type           string  — pistol/rifle/shotgun/sniper/melee/mantis/none
  is_sprinting          bool
  is_crouching          bool
  is_airborne           bool
  is_swimming           bool
  is_climbing           bool
  eddies                int     — current eurodollar balance
  game_hour             float   — in-game time 0.0–23.99
  ram_current           float   — current RAM pool
  ram_max               float   — max RAM pool
  stamina_pct           float   — 0.0 to 1.0
  has_sandevistan       bool
  has_optical_camo      bool
  has_berserk           bool
  weather               string  — current weather name
  is_raining            bool
  in_dialogue           bool
  dialogue_npc          string  — name of NPC currently talking to
  kills_this_combat     int     — kills since combat started
  session_kills         int     — total kills this session
  headshots_this_combat int     — headshots since combat started
  session_headshots     int     — total headshots this session
  ts                    float   — Unix timestamp of this snapshot
--]]

-- outputPath is set once inside onInit to avoid top-level os.* crashes
local outputPath = nil

local bridge = {
    tickRate              = 0.5,
    lastWrite             = 0,
    lastItem              = "",
    kills_this_combat     = 0,
    session_kills         = 0,
    headshots_this_combat = 0,
    session_headshots     = 0,
    was_in_combat         = false,
}

-- CET sandboxes io.open to the mod's own directory.
-- Use bare filename only — no path prefix.
local MOD_OUTPUT_FILE = "cp2077_state.json"
local BRIDGE_VERSION = "2026-04-10-race-sidecar-v2"

local function resolveOutputPath()
    local f, err = io.open(MOD_OUTPUT_FILE, "w")
    if f then
        f:write('{"ts":0}')
        f:close()
        print("[RadioOSBridge] write OK: " .. MOD_OUTPUT_FILE)
        return MOD_OUTPUT_FILE
    else
        print("[RadioOSBridge] write FAIL: " .. tostring(err))
        return nil
    end
end

-- ── helpers ──────────────────────────────────────────────────────────────────

local function ensureDir(path)
    pcall(function() os.execute('mkdir "' .. path .. '" 2>nul') end)
end

local function safeStr(v)
    if v == nil then return "" end
    return tostring(v)
end

local function safeNum(v, default)
    if v == nil then return default or 0 end
    local n = tonumber(v)
    return n or (default or 0)
end

local function safeBool(v)
    if v == nil then return false end
    return v == true
end

local function writeJSON(data)
    if not outputPath then return end
    local path = outputPath
    local f    = io.open(path, "w")
    if not f then
        -- Attempt dir creation and retry once
        local dir = outputPath:match("^(.+)\\[^\\]+$") or ""
        ensureDir(dir)
        f = io.open(path, "w")
        if not f then return end
    end

    local lines = {}
    lines[#lines+1] = "{"

    local function appendKV(key, val, last)
        local comma = last and "" or ","
        if type(val) == "string" then
            -- Escape quotes and backslashes
            val = val:gsub("\\", "\\\\"):gsub('"', '\\"')
            lines[#lines+1] = '  "' .. key .. '": "' .. val .. '"' .. comma
        elseif type(val) == "boolean" then
            lines[#lines+1] = '  "' .. key .. '": ' .. (val and "true" or "false") .. comma
        else
            lines[#lines+1] = '  "' .. key .. '": ' .. tostring(val) .. comma
        end
    end

    appendKV("player_name",           data.player_name)
    appendKV("health_pct",            data.health_pct)
    appendKV("is_alive",              data.is_alive)
    appendKV("level",                 data.level)
    appendKV("street_cred",           data.street_cred)
    appendKV("in_combat",             data.in_combat)
    appendKV("enemy_count",           data.enemy_count)
    appendKV("nearest_enemy_m",       data.nearest_enemy_m)
    appendKV("wanted_level",          data.wanted_level)
    appendKV("district",              data.district)
    appendKV("location",              data.location)
    appendKV("coords_x",              data.coords_x)
    appendKV("coords_y",              data.coords_y)
    appendKV("active_quest",          data.active_quest)
    appendKV("active_objective",      data.active_objective)
    appendKV("in_vehicle",            data.in_vehicle)
    appendKV("vehicle_name",          data.vehicle_name)
    appendKV("vehicle_speed",         data.vehicle_speed)
    appendKV("vehicle_type",          data.vehicle_type)
    appendKV("last_item",             data.last_item)
    appendKV("nearby_poi",            data.nearby_poi)
    appendKV("nearby_poi_dist",       data.nearby_poi_dist)
    appendKV("weapon_name",           data.weapon_name)
    appendKV("weapon_type",           data.weapon_type)
    appendKV("is_sprinting",          data.is_sprinting)
    appendKV("is_crouching",          data.is_crouching)
    appendKV("is_airborne",           data.is_airborne)
    appendKV("is_swimming",           data.is_swimming)
    appendKV("is_climbing",           data.is_climbing)
    appendKV("eddies",                data.eddies)
    appendKV("game_hour",             data.game_hour)
    appendKV("ram_current",           data.ram_current)
    appendKV("ram_max",               data.ram_max)
    appendKV("stamina_pct",           data.stamina_pct)
    appendKV("has_sandevistan",       data.has_sandevistan)
    appendKV("has_optical_camo",      data.has_optical_camo)
    appendKV("has_berserk",           data.has_berserk)
    appendKV("weather",               data.weather)
    appendKV("is_raining",            data.is_raining)
    appendKV("in_dialogue",           data.in_dialogue)
    appendKV("dialogue_npc",          data.dialogue_npc)
    appendKV("kills_this_combat",     data.kills_this_combat)
    appendKV("session_kills",         data.session_kills)
    appendKV("headshots_this_combat", data.headshots_this_combat)
    appendKV("session_headshots",     data.session_headshots)
    appendKV("ts",                    data.ts)

    if data.mte_race_mode ~= nil then
        appendKV("mte_race_mode",          data.mte_race_mode)
        appendKV("mte_race_state",         data.mte_race_state)
        appendKV("mte_race_position",      data.mte_race_position)
        appendKV("mte_race_field_size",    data.mte_race_field_size)
        appendKV("mte_race_time",          data.mte_race_time)
        appendKV("mte_track_name",         data.mte_track_name)
        appendKV("mte_knockout_remaining", data.mte_knockout_remaining)
        appendKV("mte_knockout_danger",    data.mte_knockout_danger)
        appendKV("mte_knockout_elim_cd",   data.mte_knockout_elim_cd)
        appendKV("mte_knockout_phase",     data.mte_knockout_phase)
    end

    appendKV("bridge_version",        data.bridge_version)
    appendKV("bridge_ncm_sidecar",    data.bridge_ncm_sidecar)
    appendKV("bridge_mte_seen",       data.bridge_mte_seen)
    appendKV("bridge_ncm_active",     data.bridge_ncm_active, true)

    lines[#lines+1] = "}"
    f:write(table.concat(lines, "\n"))
    f:close()
end

-- ── game state readers ────────────────────────────────────────────────────────

local function getPlayerName()
    local ok, result = pcall(function()
        local player = Game.GetPlayer()
        if not player then return "V" end
        -- Try PlayerDevelopmentSystem for character name
        local pds = Game.GetScriptableSystemsContainer():Get("PlayerDevelopmentSystem")
        if pds then
            local perk = pds:GetDevelopmentData(player)
            if perk then
                local n = perk:GetCharacterName()
                if n and not tostring(n):find("LocKey") then return tostring(n) end
            end
        end
        return "V"
    end)
    return ok and safeStr(result) or "V"
end

local function getHealthPct()
    local ok, result = pcall(function()
        local player = Game.GetPlayer()
        if not player then return 1.0 end
        local hpComp = player:GetHealthComponent()
        if not hpComp then return 1.0 end
        local hp    = hpComp:GetHP()
        local maxHp = hpComp:GetMaxHP()
        if maxHp and maxHp > 0 then
            return hp / maxHp
        end
        return 1.0
    end)
    return ok and safeNum(result, 1.0) or 1.0
end

local function isAlive()
    local ok, result = pcall(function()
        local player = Game.GetPlayer()
        if not player then return true end
        return not player:IsDead()
    end)
    return ok and safeBool(result) or true
end

local function getLevel()
    local ok, result = pcall(function()
        local ss = Game.GetStatsSystem()
        local player = Game.GetPlayer()
        if not ss or not player then return 1 end
        local entityID = player:GetEntityID()
        return ss:GetStatValue(entityID, "Level") or 1
    end)
    return ok and math.floor(safeNum(result, 1)) or 1
end

local function getStreetCred()
    local ok, result = pcall(function()
        local ss = Game.GetStatsSystem()
        local player = Game.GetPlayer()
        if not ss or not player then return 0 end
        local entityID = player:GetEntityID()
        return ss:GetStatValue(entityID, "StreetCred") or 0
    end)
    return ok and math.floor(safeNum(result, 0)) or 0
end

local function getCombatInfo()
    local inCombat, enemyCount, nearestDist = false, 0, 999.0
    pcall(function()
        local cs = Game.GetTargetingSystem()
        if cs then
            inCombat = Game.GetPlayer():IsInCombat()
        end
        -- Enemy count via threat system
        local threatSystem = Game.GetThreatTrackingSystem()
        if threatSystem then
            local threats = threatSystem:GetHostileActors(Game.GetPlayer())
            if threats then
                enemyCount = #threats
                for _, threat in ipairs(threats) do
                    pcall(function()
                        local pos    = threat:GetWorldPosition()
                        local playerPos = Game.GetPlayer():GetWorldPosition()
                        local dx = pos.x - playerPos.x
                        local dy = pos.y - playerPos.y
                        local dist = math.sqrt(dx*dx + dy*dy)
                        if dist < nearestDist then nearestDist = dist end
                    end)
                end
            end
        end
    end)
    return inCombat, enemyCount, nearestDist
end

local function getWantedLevel()
    local ok, result = pcall(function()
        local ls = Game.GetScriptableSystemsContainer():Get("NCPD_WantedSystem")
        if ls then
            return ls:GetWantedLevel() or 0
        end
        -- Alternate path
        local player = Game.GetPlayer()
        if player then
            local wantedComp = player:GetWantedSystem()
            if wantedComp then
                return wantedComp:GetWantedLevel() or 0
            end
        end
        return 0
    end)
    return ok and math.floor(safeNum(result, 0)) or 0
end

local function getLocation()
    local district, location, cx, cy = "", "", 0.0, 0.0
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local worldPos = player:GetWorldPosition()
        cx = worldPos.x
        cy = worldPos.y

        -- District / community name via mapping manager
        local mm = Game.GetMappinSystem()
        if mm then
            local communityName = mm:GetCommunityNameStringID(worldPos)
            if communityName then
                location = tostring(communityName)
            end
        end

        -- District override via sector data
        local sectorManager = Game.GetDistrictManager()
        if sectorManager then
            local districtName = sectorManager:GetDistrictName(worldPos)
            if districtName then
                district = tostring(districtName)
            end
        end
    end)
    return district, location, cx, cy
end

local function getQuestInfo()
    local questName, objective = "", ""
    pcall(function()
        local js = Game.GetQuestsSystem()
        if not js then return end
        local activeQuest = js:GetActiveQuest()
        if activeQuest then
            questName = tostring(activeQuest:GetDisplayName() or "")
            local phase = activeQuest:GetCurrentObjective()
            if phase then
                objective = tostring(phase:GetDisplayName() or "")
            end
        end
    end)
    return questName, objective
end

local function getVehicleInfo()
    local inVehicle, vehicleName = false, ""
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local mountInfo = player:GetMountedVehicle()
        if mountInfo then
            inVehicle = true
            local ds = mountInfo:GetDisplayName()
            if ds then vehicleName = tostring(ds) end
        end
    end)
    return inVehicle, vehicleName
end

local function getNearbyPOI()
    local poiName, poiDist = "", 999.0
    pcall(function()
        local ms = Game.GetMappinSystem()
        if not ms then return end
        local player  = Game.GetPlayer()
        local pos     = player:GetWorldPosition()
        local mappins = ms:GetAllMappins()
        if mappins then
            for _, m in ipairs(mappins) do
                pcall(function()
                    local mpos = m:GetWorldPosition()
                    local dx   = mpos.x - pos.x
                    local dy   = mpos.y - pos.y
                    local dist = math.sqrt(dx*dx + dy*dy)
                    if dist < poiDist then
                        poiDist = dist
                        local label = m:GetLabel()
                        if label then poiName = tostring(label) end
                    end
                end)
            end
        end
    end)
    return poiName, poiDist
end

-- ── new deep-state readers ────────────────────────────────────────────────────

local function getMovementState()
    local sprinting, crouching, airborne, swimming, climbing = false, false, false, false, false
    pcall(function()
        local bb = Game.GetBlackboardSystem():Get(GetAllBlackboardDefs().PlayerStateMachine)
        if not bb then return end
        local loco = bb:GetInt(GetAllBlackboardDefs().PlayerStateMachine.Locomotion)
        -- Locomotion enum: 0=idle,1=walk,2=run,3=sprint,4=jump,5=fall,6=slide,7=swim,8=ladder
        if loco == 3 then sprinting = true end
        if loco == 5 or loco == 4 then airborne = true end
        if loco == 7 then swimming = true end
        if loco == 8 then climbing = true end
        local crouch = bb:GetInt(GetAllBlackboardDefs().PlayerStateMachine.Crouch)
        -- Crouch enum: 0=stand, 1=crouch, 2=crawl
        if crouch and crouch >= 1 then crouching = true end
    end)
    return sprinting, crouching, airborne, swimming, climbing
end

local function getCurrentWeapon()
    local weapName, weapType = "", "none"
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local weapon = player:GetCurrentWeapon()
        if not weapon then return end
        local itemData = weapon:GetItemData()
        if itemData then
            local nameStr = itemData:GetNameAsString()
            if nameStr then weapName = tostring(nameStr) end
        end
        -- Classify by TweakDB tags
        local tweakID = weapon:GetTDBID()
        if tweakID then
            local record = TweakDB:GetRecord(tweakID)
            if record then
                local tags = {}
                pcall(function()
                    local tagContainer = record:Tags()
                    if tagContainer then
                        for i = 0, tagContainer:Size() - 1 do
                            tags[tostring(tagContainer:Get(i))] = true
                        end
                    end
                end)
                if tags["Weapon.Pistol"] or tags["Weapon.Revolver"] then
                    weapType = "pistol"
                elseif tags["Weapon.AssaultRifle"] or tags["Weapon.SubmachineGun"] then
                    weapType = "rifle"
                elseif tags["Weapon.ShotgunDual"] or tags["Weapon.Shotgun"] then
                    weapType = "shotgun"
                elseif tags["Weapon.SniperRifle"] or tags["Weapon.Precision"] then
                    weapType = "sniper"
                elseif tags["Weapon.Melee"] or tags["Weapon.Katana"] or tags["Weapon.Blunt"] then
                    weapType = "melee"
                elseif tags["Weapon.MantisBlades"] or tags["Cyberware"] then
                    weapType = "mantis"
                else
                    weapType = "ranged"
                end
            end
        end
    end)
    return weapName, weapType
end

local function getVehicleDetails(inVehicle)
    local speed, vtype = 0.0, "unknown"
    if not inVehicle then return speed, vtype end
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local vehicle = player:GetMountedVehicle()
        if not vehicle then return end
        -- Speed via linear velocity magnitude
        local phys = vehicle:GetPhysicsComponent()
        if phys then
            local vel = phys:GetLinearVelocity()
            if vel then
                speed = math.sqrt(vel.X*vel.X + vel.Y*vel.Y + vel.Z*vel.Z)
            end
        end
        -- Type via record tags
        pcall(function()
            local rec = vehicle:GetRecord()
            if rec then
                local factory = tostring(rec:GetFactory() or "")
                if factory:find("motorcycle") or factory:find("bike") then
                    vtype = "bike"
                elseif factory:find("truck") or factory:find("van") then
                    vtype = "truck"
                elseif factory:find("av") then
                    vtype = "av"
                else
                    vtype = "car"
                end
            end
        end)
    end)
    return speed, vtype
end

local function getEddies()
    local amount = 0
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local ts = Game.GetTransactionSystem()
        if not ts then return end
        local moneyID = MarketSystem.Money()
        amount = ts:GetItemQuantity(player, moneyID) or 0
    end)
    return math.floor(safeNum(amount, 0))
end

local function getGameHour()
    local hour = 12.0
    pcall(function()
        local gt = Game.GetTimeSystem():GetGameTime()
        if gt then
            hour = (gt:Hours() or 12) + (gt:Minutes() or 0) / 60.0
        end
    end)
    return safeNum(hour, 12.0)
end

local function getNetrunnerState()
    local ramCur, ramMax, stamPct = 0.0, 0.0, 1.0
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local sps = Game.GetStatPoolsSystem()
        if not sps then return end
        local eid = player:GetEntityID()
        ramCur = sps:GetStatPoolValue(eid, gamedataStatPoolType.Memory) or 0
        ramMax = sps:GetStatPoolMaxPointValue(eid, gamedataStatPoolType.Memory) or 0
        local stam    = sps:GetStatPoolValue(eid, gamedataStatPoolType.Stamina) or 0
        local stamMax = sps:GetStatPoolMaxPointValue(eid, gamedataStatPoolType.Stamina) or 1
        if stamMax > 0 then stamPct = stam / stamMax end
    end)
    return safeNum(ramCur, 0), safeNum(ramMax, 0), safeNum(stamPct, 1.0)
end

local function getStatusEffects()
    local sande, optCamo, berserk = false, false, false
    pcall(function()
        local player = Game.GetPlayer()
        if not player then return end
        local ses = GameInstance.GetStatusEffectSystem()
        if not ses then return end
        local effects = ses:GetAppliedEffects(player:GetEntityID())
        if not effects then return end
        for _, eff in ipairs(effects) do
            pcall(function()
                local id = tostring(eff:GetTDBID() or "")
                local lower = id:lower()
                if lower:find("sandevistan") then sande = true end
                if lower:find("opticalcamo") or lower:find("optical_camo") then optCamo = true end
                if lower:find("berserk") then berserk = true end
            end)
        end
    end)
    return sande, optCamo, berserk
end

local function getEnvironment()
    local weatherName, raining = "", false
    pcall(function()
        local ws = Game.GetWeatherSystem()
        if not ws then return end
        local w = ws:GetCurrentWeather()
        if w then
            weatherName = tostring(w)
            local lower = weatherName:lower()
            raining = lower:find("rain") ~= nil or lower:find("fog") ~= nil or lower:find("storm") ~= nil
        end
    end)
    return weatherName, raining
end

local function getDialogueState()
    local inDialog, npcName = false, ""
    pcall(function()
        local bb = Game.GetBlackboardSystem():Get(GetAllBlackboardDefs().UIInteractions)
        if bb then
            local active = bb:GetBool(GetAllBlackboardDefs().UIInteractions.IsInteractive)
            if active then
                inDialog = true
                -- Try to get NPC name from current spoken entity
                local convSys = Game.GetQuestsSystem()
                -- fallback: dialogue NPC name via active interaction hub
                pcall(function()
                    local ihub = bb:GetEntityID(GetAllBlackboardDefs().UIInteractions.ActiveChoiceHubID)
                    if ihub then
                        local ent = Game.FindEntityByID(ihub)
                        if ent then
                            local comp = ent:GetComponent("DisplayNameComponent")
                            if comp then npcName = tostring(comp:GetDisplayName() or "") end
                        end
                    end
                end)
            end
        end
    end)
    return inDialog, npcName
end

-- ── NCM Racing Bridge ─────────────────────────────────────────────────────────
-- Subscribes to MT_Ecosystem Racing events (when MTE is installed) and polls
-- live race state, writing ncm_race_state.json alongside cp2077_state.json.

local NCM_OUTPUT_FILE = "ncm_race_state.json"

local ncm_bridge = {
    pendingEvents = {},
    raceInitDone  = false,
    seq           = 0,
}

-- Recursive JSON encoder (handles tables, strings, booleans, numbers, nil)
local function ncmEncodeValue(v)
    local t = type(v)
    if t == "nil"     then return "null" end
    if t == "boolean" then return v and "true" or "false" end
    if t == "number"  then
        if v ~= v then return "null" end  -- NaN guard
        return tostring(v)
    end
    if t == "string" then
        return '"' .. v:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '') .. '"'
    end
    if t == "table" then
        local n = #v
        if n > 0 then
            local parts = {}
            for i = 1, n do parts[i] = ncmEncodeValue(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, val in pairs(v) do
                parts[#parts + 1] = '"' .. tostring(k) .. '":' .. ncmEncodeValue(val)
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

local function ncmPushEvent(evType, data)
    local ts = 0
    pcall(function() ts = os.time() end)
    if #ncm_bridge.pendingEvents >= 20 then
        table.remove(ncm_bridge.pendingEvents, 1)
    end
    ncm_bridge.pendingEvents[#ncm_bridge.pendingEvents + 1] = {
        type = evType,
        data = data or {},
        ts   = ts,
    }
end

local function ncmIsRaceHot()
    local active = false
    pcall(function()
        if not MTE then return end
        if MTE.Knockout and tostring(MTE.Knockout.getState() or "idle") ~= "idle" then
            active = true
            return
        end
        if MTE.Racing and tostring(MTE.Racing.getState() or "idle") ~= "idle" then
            active = true
        end
    end)
    return active
end

local function ncmTelemetrySnapshot()
    local out = {
        throttle       = 0.0,
        brake          = 0.0,
        clutch         = 0.0,
        steering       = 0.0,
        handbrake      = false,
        speed_mps      = 0.0,
        speed_kph      = 0.0,
        gear           = 0,
        rpm            = 0.0,
        vehicle_health = 1.0,
        long_g         = 0.0,
        lat_g          = 0.0,
        accel_rate     = 0.0,
        decel_rate     = 0.0,
        in_vehicle     = false,
        vehicle_key    = "",
    }
    pcall(function()
        if not (MTE and MTE.Telemetry and MTE.Telemetry.state) then return end
        local s = MTE.Telemetry.state
        out.throttle       = tonumber(s.throttle) or 0.0
        out.brake          = tonumber(s.brake) or 0.0
        out.clutch         = tonumber(s.clutch) or 0.0
        out.steering       = tonumber(s.steering) or 0.0
        out.handbrake      = s.handbrake == true
        out.speed_mps      = tonumber(s.speed) or 0.0
        out.speed_kph      = out.speed_mps * 3.6
        out.gear           = tonumber(s.gear) or 0
        out.rpm            = tonumber(s.rpm) or 0.0
        out.vehicle_health = tonumber(s.vehicleHealth) or 1.0
        out.long_g         = tonumber(s.longG) or 0.0
        out.lat_g          = tonumber(s.latG) or 0.0
        out.accel_rate     = tonumber(s.accelRate) or 0.0
        out.decel_rate     = tonumber(s.decelRate) or 0.0
        out.in_vehicle     = s.inVehicle == true
        out.vehicle_key    = tostring(s.vehicleKey or "")
    end)
    return out
end

local function ncmCompactStanding(s, position, telemetry)
    if type(s) ~= "table" then s = {} end
    local isPlayer = s.isPlayer == true
    local speedMps = tonumber(s.speed) or 0.0
    if isPlayer and speedMps <= 0 and telemetry then
        speedMps = tonumber(telemetry.speed_mps) or 0.0
    end
    local currentLap = tonumber(s.currentLap or s.lap) or 0
    local entry = {
        position          = tonumber(position) or 0,
        name              = tostring(s.name or s.displayName or (isPlayer and "V" or "?")),
        driverId          = tostring(s.driverId or s.id or (isPlayer and "player" or "")),
        isPlayer          = isPlayer,
        finished          = s.finished == true,
        time              = tonumber(s.time) or 0.0,
        progress          = tonumber(s.progress) or 0.0,
        distanceAlong     = tonumber(s.distanceAlong) or 0.0,
        remainingDistance = tonumber(s.remainingDistance or s.distToFinish) or 0.0,
        distToFinish      = tonumber(s.distToFinish or s.remainingDistance) or 0.0,
        sectorIndex       = tonumber(s.sectorIndex) or 0,
        currentLap        = currentLap,
        lap               = currentLap,
        cpIndex           = tonumber(s.cpIndex) or 0,
        speed             = speedMps,
        speedMps          = speedMps,
        speedKph          = speedMps * 3.6,
        isRival           = s.isRival == true,
        launchable        = s.launchable == true,
        provenance        = tostring(s.provenance or ""),
    }
    if s.lateralDistance ~= nil then
        entry.lateralDistance = tonumber(s.lateralDistance) or 0.0
    end
    return entry
end

local function ncmAttachPlayerPack(live, compact, playerIndex)
    if playerIndex and playerIndex > 0 and compact[playerIndex] then
        live.player = compact[playerIndex]
        if playerIndex > 1 then
            live.ahead = compact[playerIndex - 1]
        end
        if playerIndex < #compact then
            live.behind = compact[playerIndex + 1]
        end
    end
end

local function ncmMetricPayload(payload)
    local data = (payload and payload.data) or {}
    local session = (payload and payload.session) or {}
    return {
        delta_v       = tonumber(data.deltaV) or 0.0,
        speed_kph     = tonumber(data.speed) or 0.0,
        g             = tonumber(data.g) or 0.0,
        lat_g         = tonumber(data.latG) or 0.0,
        time          = tonumber(session.duration) or 0.0,
        crash_count   = tonumber(session.crashCount) or 0,
        peak_speed    = tonumber(session.peakSpeed) or 0.0,
    }
end

local function writeNcmRaceState()
    local raceState = "idle"
    local telemetry = ncmTelemetrySnapshot()
    local live = {
        mode               = "",
        timer              = 0.0,
        estimated_position = 0,
        gap_text           = "",
        gap_behind_text    = "",
        field_size         = 0,
        track_name         = "",
        district           = "",
        race_type          = "",
        start_type         = "",
        distance           = 0.0,
        knockout_remaining = 0,
        knockout_danger    = false,
        knockout_elim_cd   = 0.0,
        knockout_phase     = "",
        telemetry          = telemetry,
        player             = {},
        ahead              = {},
        behind             = {},
        standings          = {},
    }
    pcall(function()
        if not MTE then return end

        if MTE.Knockout then
            local ks = tostring(MTE.Knockout.getState() or "idle")
            if ks ~= "idle" then
                raceState = ks
                live.mode = "knockout"
                live.timer = tonumber(MTE.Knockout.getRaceTimer()) or 0.0
                live.field_size = #(MTE.Knockout.getField() or {}) + 1
                live.knockout_remaining = tonumber(MTE.Knockout.getRemainingDrivers()) or 0
                live.knockout_danger = MTE.Knockout.isPlayerInDangerZone() == true
                live.knockout_elim_cd = tonumber(MTE.Knockout.getEliminationCountdown()) or 0.0
                live.knockout_phase = tostring(MTE.Knockout.getRacePhase() or "")
                live.estimated_position = tonumber(MTE.Knockout.getPlayerPosition()) or 0
                local kcfg = MTE.Knockout.getConfig()
                live.track_name = tostring((kcfg and kcfg.circuitName) or "")
                local standings = MTE.Knockout.getLiveStandings() or {}
                local compact = {}
                local playerIndex = tonumber(live.estimated_position) or 0
                for i, s in ipairs(standings) do
                    local entry = ncmCompactStanding(s, i, telemetry)
                    compact[#compact + 1] = entry
                    if entry.isPlayer then playerIndex = i end
                end
                live.standings = compact
                ncmAttachPlayerPack(live, compact, playerIndex)
                return
            end
        end

        if not MTE.Racing then return end
        raceState = tostring(MTE.Racing.getState() or "idle")
        if raceState ~= "idle" then
            live.mode               = "sprint"
            live.timer              = tonumber(MTE.Racing.getRaceTimer()) or 0.0
            live.gap_text           = tostring(MTE.Racing.getGapText() or "")
            pcall(function()
                if MTE.Racing.getGapBehindText then
                    live.gap_behind_text = tostring(MTE.Racing.getGapBehindText() or "")
                end
            end)
            live.field_size         = #(MTE.Racing.getField() or {}) + 1
            live.estimated_position = tonumber(MTE.Racing.getEstimatedPosition()) or 0
            local cfg = MTE.Racing.getConfig()
            if cfg then
                live.track_name = tostring(cfg.trackName or cfg.routeName or "")
                live.district   = tostring(cfg.district or "")
                live.race_type  = tostring(cfg.raceType or "")
                live.start_type = tostring(cfg.startType or "")
                live.distance   = tonumber(cfg.distance) or 0.0
            end
            if live.track_name == "" and cfg and cfg.nodeA and cfg.nodeB and MTE.Tracks then
                live.track_name = tostring(MTE.Tracks.getRouteName(cfg.nodeA, cfg.nodeB) or "")
            end
            local sds = MTE.Racing.getLiveStandings() or {}
            local compact = {}
            local playerIndex = tonumber(live.estimated_position) or 0
            for i, s in ipairs(sds) do
                local entry = ncmCompactStanding(s, i, telemetry)
                compact[#compact + 1] = entry
                if entry.isPlayer then playerIndex = i end
            end
            live.standings = compact
            ncmAttachPlayerPack(live, compact, playerIndex)
        end
    end)

    local ts = 0
    pcall(function() ts = os.time() end)
    ncm_bridge.seq = ncm_bridge.seq + 1

    local pendingSnapshot       = ncm_bridge.pendingEvents
    ncm_bridge.pendingEvents    = {}  -- clear after snapshot

    local payload = {
        bridge_version = BRIDGE_VERSION,
        seq            = ncm_bridge.seq,
        race_state     = raceState,
        pending_events = pendingSnapshot,
        live           = live,
        ts             = ts,
    }
    local f = io.open(NCM_OUTPUT_FILE, "w")
    if f then
        pcall(function() f:write(ncmEncodeValue(payload)) end)
        f:close()
    end
end

-- ── main tick ─────────────────────────────────────────────────────────────────

registerForEvent("onUpdate", function(delta)
    bridge.lastWrite = (bridge.lastWrite or 0) + delta
    if bridge.lastWrite < bridge.tickRate then return end
    bridge.lastWrite = 0

    local ok, err = pcall(function()
        local inCombat, enemyCount, nearestDist = getCombatInfo()
        local district, location, cx, cy        = getLocation()
        local questName, objective               = getQuestInfo()
        local inVehicle, vehicleName             = getVehicleInfo()
        local vehicleSpeed, vehicleType          = getVehicleDetails(inVehicle)
        local poiName, poiDist                   = getNearbyPOI()
        local isSprinting, isCrouching, isAirborne, isSwimming, isClimbing = getMovementState()
        local weapName, weapType                 = getCurrentWeapon()
        local ramCur, ramMax, stamPct            = getNetrunnerState()
        local hasSande, hasOptCamo, hasBerserk   = getStatusEffects()
        local weatherName, isRaining             = getEnvironment()
        local inDialog, dialogNpc                = getDialogueState()

        -- Reset per-combat kill counters when combat ends
        if bridge.was_in_combat and not inCombat then
            bridge.kills_this_combat     = 0
            bridge.headshots_this_combat = 0
        end
        bridge.was_in_combat = inCombat

        local ts = 0
        pcall(function() ts = os.time() end)

        local data = {
            player_name           = getPlayerName(),
            health_pct            = getHealthPct(),
            is_alive              = isAlive(),
            level                 = getLevel(),
            street_cred           = getStreetCred(),
            in_combat             = inCombat,
            enemy_count           = enemyCount,
            nearest_enemy_m       = nearestDist,
            wanted_level          = getWantedLevel(),
            district              = district,
            location              = location,
            coords_x              = cx,
            coords_y              = cy,
            active_quest          = questName,
            active_objective      = objective,
            in_vehicle            = inVehicle,
            vehicle_name          = vehicleName,
            vehicle_speed         = vehicleSpeed,
            vehicle_type          = vehicleType,
            last_item             = bridge.lastItem,
            nearby_poi            = poiName,
            nearby_poi_dist       = poiDist,
            weapon_name           = weapName,
            weapon_type           = weapType,
            is_sprinting          = isSprinting,
            is_crouching          = isCrouching,
            is_airborne           = isAirborne,
            is_swimming           = isSwimming,
            is_climbing           = isClimbing,
            eddies                = getEddies(),
            game_hour             = getGameHour(),
            ram_current           = ramCur,
            ram_max               = ramMax,
            stamina_pct           = stamPct,
            has_sandevistan       = hasSande,
            has_optical_camo      = hasOptCamo,
            has_berserk           = hasBerserk,
            weather               = weatherName,
            is_raining            = isRaining,
            in_dialogue           = inDialog,
            dialogue_npc          = dialogNpc,
            kills_this_combat     = bridge.kills_this_combat,
            session_kills         = bridge.session_kills,
            headshots_this_combat = bridge.headshots_this_combat,
            session_headshots     = bridge.session_headshots,
            ts                    = ts,
            bridge_version        = BRIDGE_VERSION,
            bridge_ncm_sidecar    = true,
            bridge_mte_seen       = type(MTE) == "table",
            bridge_ncm_active     = ncm_bridge.raceInitDone == true,
        }

        pcall(function()
            if type(MTE) ~= "table" then return end

            if MTE.Racing then
                local rs = MTE.Racing.getState()
                if rs and rs ~= "idle" then
                    data.mte_race_mode       = "sprint"
                    data.mte_race_state      = rs
                    data.mte_race_position   = MTE.Racing.getEstimatedPosition() or 0
                    local field = MTE.Racing.getField()
                    data.mte_race_field_size = field and (#field + 1) or 1
                    data.mte_race_time       = MTE.Racing.getRaceTimer() or 0
                    data.mte_track_name      = ""
                    local cfg = MTE.Racing.getConfig()
                    if cfg and cfg.nodeA and cfg.nodeB and MTE.Tracks then
                        data.mte_track_name = MTE.Tracks.getRouteName(cfg.nodeA, cfg.nodeB) or ""
                    end
                    data.mte_knockout_remaining = 0
                    data.mte_knockout_danger    = false
                    data.mte_knockout_elim_cd   = 0
                    data.mte_knockout_phase     = ""
                end
            end

            if MTE.Knockout then
                local ks = MTE.Knockout.getState()
                if ks and ks ~= "idle" then
                    data.mte_race_mode          = "knockout"
                    data.mte_race_state         = ks
                    data.mte_race_position      = MTE.Knockout.getPlayerPosition() or 0
                    data.mte_race_field_size    = MTE.Knockout.getRemainingDrivers() or 0
                    data.mte_race_time          = MTE.Knockout.getRaceTimer() or 0
                    local kcfg = MTE.Knockout.getConfig()
                    data.mte_track_name         = (kcfg and kcfg.circuitName) or ""
                    data.mte_knockout_remaining = MTE.Knockout.getRemainingDrivers() or 0
                    data.mte_knockout_danger    = MTE.Knockout.isPlayerInDangerZone() or false
                    data.mte_knockout_elim_cd   = MTE.Knockout.getEliminationCountdown() or 0
                    data.mte_knockout_phase     = MTE.Knockout.getRacePhase() or ""
                end
            end
        end)

        writeJSON(data)
    end)

    if not ok then
        -- Silently ignore ticks that fail to avoid spam
    end

    -- NCM race state: only write when bridge is active
    if ncm_bridge.raceInitDone then
        pcall(writeNcmRaceState)
    end
end)

-- Track item pickups and kills via events
registerForEvent("onInit", function()
    outputPath = resolveOutputPath()
    if outputPath then
        print("[RadioOSBridge] active " .. BRIDGE_VERSION .. " — " .. outputPath)
    else
        print("[RadioOSBridge] ERROR: no writable path found — all candidates failed")
    end

    -- Hook item pickup if the API supports it
    pcall(function()
        local ts = Game.GetTransactionSystem()
        if ts then
            ts:RegisterListener(Game.GetPlayer(), function(item, amount)
                if amount > 0 then
                    local id = item:GetID()
                    local tm = Game.GetTweakDBInterface()
                    if tm and id then
                        local name = tm:GetFlat(id, "displayName")
                        if name then
                            bridge.lastItem = tostring(name)
                        end
                    end
                end
            end)
        end
    end)

    -- Hook kill events for real-time kill/headshot counting
    pcall(function()
        Observe("NPCDeathEvent", "Execute", function(evt)
            pcall(function()
                bridge.kills_this_combat = bridge.kills_this_combat + 1
                bridge.session_kills     = bridge.session_kills + 1
                -- Detect headshots: check if death cause tag includes "head"
                local causeName = tostring(evt:GetCause() or "")
                if causeName:lower():find("head") then
                    bridge.headshots_this_combat = bridge.headshots_this_combat + 1
                    bridge.session_headshots     = bridge.session_headshots + 1
                end
            end)
        end)
    end)

    -- ── NCM race bridge ────────────────────────────────────────────────────
    pcall(function()
        if not MTE or not MTE.Events then
            print("[RadioOSBridge] MTE not available — NCM race bridge inactive (" .. BRIDGE_VERSION .. ")")
            return
        end

        local function compactField(field)
            local out = {}
            for _, e in ipairs(field or {}) do
                out[#out + 1] = {
                    name     = tostring(e.displayName or e.name or "?"),
                    driverId = tostring(e.driverId or e.id or ""),
                    isRival  = e.isRival == true,
                    gridPos  = tonumber(e.gridPos) or 0,
                }
            end
            return out
        end

        local function compactResults(results)
            local out = {}
            for _, r in ipairs(results or {}) do
                out[#out + 1] = {
                    position    = tonumber(r.position or r.playerPosition) or 0,
                    name        = tostring(r.name or r.displayName or "?"),
                    driverId    = tostring(r.driverId or r.id or ""),
                    isPlayer    = r.isPlayer == true,
                    time        = tonumber(r.time or r.raceTime) or 0,
                    factionName = tostring(r.factionName or ""),
                }
            end
            return out
        end

        local function routeNameFromConfig(cfg)
            cfg = cfg or {}
            local name = tostring(cfg.trackName or cfg.routeName or "")
            pcall(function()
                if name == "" and cfg.nodeA and cfg.nodeB and MTE.Tracks then
                    name = tostring(MTE.Tracks.getRouteName(cfg.nodeA, cfg.nodeB) or "")
                end
            end)
            return name
        end

        local function activeSeasonInfo()
            local info = {}
            pcall(function()
                local career = MTE.Careers and MTE.Careers.active()
                if not career then return end
                local season = MTE.Championships and MTE.Championships.activeSeason(career)
                if season and not season.completed then
                    info = {
                        round        = tonumber(season.currentRound) or 0,
                        total_rounds = tonumber(season.totalRounds) or 0,
                        tier         = tostring(career.tier or ""),
                    }
                end
            end)
            return info
        end

        MTE.Events.on("RoundReady", function(data)
            ncmPushEvent("RoundReady", {
                round      = tonumber(data.round) or 0,
                total      = tonumber(data.total) or 0,
                district   = tostring(data.district or ""),
                event_type = tostring(data.eventType or "sprint"),
                tier       = tostring(data.tier or ""),
                team_name  = tostring(data.teamName or ""),
            })
        end)

        MTE.Events.on("RacePreview", function(data)
            local cfg = data.config or {}
            ncmPushEvent("RacePreview", {
                track_name    = routeNameFromConfig(cfg),
                district      = tostring(cfg.district or ""),
                distance      = tonumber(cfg.distance) or 0,
                field_size    = #(data.field or {}),
                start_type    = tostring(cfg.startType or "grid"),
                race_type     = tostring(cfg.raceType or "sprint"),
                allow_stakes  = cfg.allowStakes == true,
                quali_enabled = cfg.qualiEnabled == true,
                field         = compactField(data.field),
                season        = activeSeasonInfo(),
            })
        end)

        MTE.Events.on("GridReady", function(data)
            ncmPushEvent("GridReady", {
                field      = compactField(data.field),
                field_size = #(data.field or {}),
            })
        end)

        MTE.Events.on("QualiStart", function(data)
            local cfg = data.config or {}
            ncmPushEvent("QualiStart", {
                district  = tostring(cfg.district or ""),
                race_type = tostring(cfg.raceType or "sprint"),
            })
        end)

        MTE.Events.on("QualiEnd", function(data)
            ncmPushEvent("QualiEnd", {
                player_time = tonumber(data.playerTime) or 0,
                grid_pos    = tonumber(data.gridPos) or 1,
            })
        end)

        MTE.Events.on("RaceCountdown", function(data)
            ncmPushEvent("RaceCountdown", { seconds = tonumber(data.seconds) or 3 })
        end)

        MTE.Events.on("RaceStart", function(data)
            local cfg = data.config or {}
            ncmPushEvent("RaceStart", {
                track_name = routeNameFromConfig(cfg),
                district   = tostring(cfg.district or ""),
                distance   = tonumber(cfg.distance) or 0,
                race_type  = tostring(cfg.raceType or "sprint"),
                start_type = tostring(cfg.startType or "grid"),
                season     = activeSeasonInfo(),
            })
        end)

        MTE.Events.on("RaceFinished", function(data)
            local results = compactResults(data.results)
            local champInfo = {}
            pcall(function()
                local career = MTE.Careers and MTE.Careers.active()
                if not career or not MTE.Championships then return end
                local season = MTE.Championships.activeSeason(career)
                if not season then return end
                local sTable = MTE.Championships.driverStandings(season)
                local champPos, champPts = nil, 0
                for i, ds in ipairs(sTable) do
                    if ds.driverId == "player" then
                        champPos = i; champPts = ds.points or 0; break
                    end
                end
                champInfo = {
                    round        = tonumber(season.currentRound) or 0,
                    total_rounds = tonumber(season.totalRounds) or 0,
                    champ_pos    = champPos,
                    champ_pts    = champPts,
                }
            end)
            ncmPushEvent("RaceFinished", {
                position        = tonumber(data.position) or 0,
                player_time     = tonumber(data.playerTime) or 0,
                payout          = tonumber(data.payout) or 0,
                rep_gain        = tonumber(data.repGain) or 0,
                track_name      = tostring(data.trackName or ""),
                route_id        = tostring(data.routeId or ""),
                session_id      = tostring(data.sessionId or ""),
                safety_rating   = tonumber(data.safetyRating) or 0,
                hype            = tonumber(data.hype) or 0,
                drag_strip      = data.dragStrip == true,
                is_clean        = data.isClean == true,
                wager_won       = data.wagerWon == true,
                wager_lost      = data.wagerLost == true,
                wager_amount    = tonumber(data.wagerAmount) or 0,
                bounty_resolved = data.bountyResolved == true,
                is_dnf          = data.isDNF == true,
                results         = results,
                season          = champInfo,
            })
        end)

        MTE.Events.on("KnockoutLobby", function(data)
            local cfg = data.config or {}
            local circuit = data.circuit or {}
            ncmPushEvent("KnockoutLobby", {
                circuit_name         = tostring(circuit.name or cfg.circuitName or ""),
                field_size           = #(data.field or {}),
                elimination_interval = tonumber(cfg.eliminationInterval) or 0,
            })
        end)

        MTE.Events.on("KnockoutCountdown", function(data)
            ncmPushEvent("KnockoutCountdown", {
                seconds = tonumber(data.seconds) or 3,
            })
        end)

        MTE.Events.on("KnockoutStart", function(data)
            local cfg = data.config or {}
            local remaining = 0
            pcall(function()
                if MTE.Knockout then
                    remaining = tonumber(MTE.Knockout.getRemainingDrivers()) or 0
                end
            end)
            ncmPushEvent("KnockoutStart", {
                track_name = tostring(cfg.circuitName or ""),
                field_size = remaining,
                remaining  = remaining,
            })
        end)

        MTE.Events.on("DriverEliminated", function(data)
            ncmPushEvent("DriverEliminated", {
                id        = tostring(data.id or ""),
                name      = tostring(data.name or "?"),
                is_player = data.isPlayer == true,
                time      = tonumber(data.time) or 0,
                remaining = tonumber(data.remaining) or 0,
            })
        end)

        MTE.Events.on("PlayerInDangerZone", function(data)
            ncmPushEvent("PlayerInDangerZone", {
                position     = tonumber(data.position) or 0,
                total        = tonumber(data.total) or 0,
                time_to_elim = tonumber(data.timeToElim) or 0,
            })
        end)

        MTE.Events.on("LeadChange", function(data)
            ncmPushEvent("LeadChange", {
                new_leader    = tostring(data.newLeader or "?"),
                new_leader_id = tostring(data.newLeaderId or ""),
            })
        end)

        MTE.Events.on("KnockoutFinished", function(data)
            local eliminationOrder = {}
            local trackName = ""
            pcall(function()
                if MTE.Knockout then
                    local cfg = MTE.Knockout.getConfig()
                    trackName = tostring((cfg and cfg.circuitName) or "")
                end
            end)
            for _, e in ipairs(data.eliminationOrder or {}) do
                eliminationOrder[#eliminationOrder + 1] = {
                    name     = tostring(e.name or "?"),
                    position = tonumber(e.position) or 0,
                    time     = tonumber(e.time) or 0,
                }
            end
            ncmPushEvent("KnockoutFinished", {
                player_position   = tonumber(data.playerPosition) or 0,
                player_won        = data.playerWon == true,
                payout            = tonumber(data.payout) or 0,
                rep_gain          = tonumber(data.repGain) or 0,
                race_time         = tonumber(data.raceTime) or 0,
                track_name        = trackName,
                elimination_order = eliminationOrder,
                results           = compactResults(data.results),
            })
        end)

        MTE.Events.on("CatchUpBonus", function(data)
            ncmPushEvent("CatchUpBonus", {
                amount = tonumber(data.amount) or 0,
                streak = tonumber(data.streak) or 0,
            })
        end)

        MTE.Events.on("WagerLost", function(data)
            ncmPushEvent("WagerLost", {
                amount = tonumber(data.amount) or 0,
            })
        end)

        MTE.Events.on("NewRecord", function(data)
            ncmPushEvent("NewRecord", {
                career_id   = tostring(data.careerId or ""),
                route_key   = tostring(data.routeKey or ""),
                time        = tonumber(data.time) or 0,
                driver_name = tostring(data.driverName or ""),
            })
        end)

        MTE.Events.on("PrestigeChange", function(data)
            ncmPushEvent("PrestigeChange", {
                new_tier = tostring(data.to or data.newTier or data.tier or ""),
                old_tier = tostring(data.from or data.oldTier or data.prevTier or ""),
                dir      = tostring(data.dir or ""),
            })
        end)

        MTE.Events.on("SeasonComplete", function(data)
            ncmPushEvent("SeasonComplete", {
                season      = tonumber(data.season) or 0,
                season_name = "Season " .. tostring(tonumber(data.season) or 0),
                rep_bonus   = tonumber(data.repBonus) or 0,
                eddies      = tonumber(data.eddies) or 0,
                win_pct     = tonumber(data.winPct) or 0,
                tier        = tostring((data.career and data.career.tier) or ""),
            })
        end)

        MTE.Events.on("ChampionshipComplete", function(data)
            ncmPushEvent("ChampionshipComplete", {
                final_position = tonumber(data.champPos or data.position or data.finalPosition) or 0,
                champ_pos      = tonumber(data.champPos or data.position or data.finalPosition) or 0,
                champion       = (tonumber(data.champPos or data.position or data.finalPosition) or 0) == 1,
                season_id      = tostring(data.seasonId or ""),
                tier           = tostring(data.tier or ""),
            })
        end)

        MTE.Events.on("ImpactSpike", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("ImpactSpike", ncmMetricPayload(payload))
        end)

        MTE.Events.on("CrashSpike", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("CrashSpike", ncmMetricPayload(payload))
        end)

        MTE.Events.on("HardBrake", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("HardBrake", ncmMetricPayload(payload))
        end)

        MTE.Events.on("AggressiveAccel", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("AggressiveAccel", ncmMetricPayload(payload))
        end)

        MTE.Events.on("HighSpeedRun", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("HighSpeedRun", ncmMetricPayload(payload))
        end)

        MTE.Events.on("SustainedCorner", function(payload)
            if not ncmIsRaceHot() then return end
            ncmPushEvent("SustainedCorner", ncmMetricPayload(payload))
        end)

        MTE.Events.on("SafetyIncidentRecorded", function(data)
            if not ncmIsRaceHot() then return end
            data = data or {}
            ncmPushEvent("SafetyIncidentRecorded", {
                label                 = tostring(data.label or ""),
                points                = tonumber(data.points) or 0,
                total_incident_points = tonumber(data.totalIncidentPoints) or 0,
                time                  = tonumber(data.time) or 0.0,
                scope_type            = tostring(data.scopeType or ""),
                career_id             = tostring(data.careerId or ""),
            })
        end)

        ncm_bridge.raceInitDone = true
        print("[RadioOSBridge] NCM race bridge active (" .. BRIDGE_VERSION .. ")")
    end)
end)

-- Module loaded successfully (outputPath set in onInit)
print("[RadioOSBridge] module loaded " .. BRIDGE_VERSION .. ", waiting for onInit...")
