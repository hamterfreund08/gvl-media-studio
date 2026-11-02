obs = obslua
local ffi = require("ffi")

ffi.cdef [[
void* ShellExecuteA(void* hwnd, const char* operation, const char* file, const char* parameters, const char* directory, int show);
]]

local SOURCE_NAME = "GVL Broadcast Overlay"
local OVERLAY_URL = "http://127.0.0.1:40231/media/overlay"
local EXE_NAME = "GVL Media Studio.exe"
local WIDTH = 1920
local HEIGHT = 1080
local CHECK_MS = 500
local COMMAND_POLL_MS = 100
local RESTORE_MS = 400
local MAX_CHECKS = 120

local shell = ffi.load("shell32")
local checks = 0
local restore_name = nil

local function file_exists(path)
	local file = io.open(path, "r")
	if file then
		file:close()
		return true
	end
	return false
end

local function ready_file()
	return script_path() .. "ready.txt"
end

local function start_service()
	os.remove(ready_file())
	local exe = script_path() .. EXE_NAME
	if file_exists(exe) then
		shell.ShellExecuteA(nil, "open", exe, "--service", script_path(), 0)
	else
		obs.script_log(obs.LOG_WARNING, "GVL Media Studio nicht gefunden: " .. exe)
	end
end

local function add_to_scene(scene_source, source)
	local scene = obs.obs_scene_from_source(scene_source)
	if scene ~= nil and obs.obs_scene_find_source(scene, SOURCE_NAME) == nil then
		local item = obs.obs_scene_add(scene, source)
		obs.obs_sceneitem_set_locked(item, true)
	end
end

local function create_overlay()
	local settings = obs.obs_data_create()
	obs.obs_data_set_string(settings, "url", OVERLAY_URL)
	obs.obs_data_set_int(settings, "width", WIDTH)
	obs.obs_data_set_int(settings, "height", HEIGHT)
	obs.obs_data_set_bool(settings, "shutdown", false)
	obs.obs_data_set_bool(settings, "restart_when_active", false)
	local source = obs.obs_source_create("browser_source", SOURCE_NAME, settings, nil)
	obs.obs_data_release(settings)
	local scenes = obs.obs_frontend_get_scenes()
	if scenes ~= nil then
		for _, scene_source in ipairs(scenes) do
			add_to_scene(scene_source, source)
		end
		obs.source_list_release(scenes)
	end
	obs.obs_source_release(source)
end

local function ensure_overlay()
	local source = obs.obs_get_source_by_name(SOURCE_NAME)
	if source == nil then
		create_overlay()
	else
		obs.obs_source_release(source)
	end
end

local function reload_overlay()
	local source = obs.obs_get_source_by_name(SOURCE_NAME)
	if source == nil then
		return
	end
	local settings = obs.obs_source_get_settings(source)
	obs.obs_data_set_string(settings, "url", OVERLAY_URL .. "?v=" .. tostring(os.time()))
	obs.obs_source_update(source, settings)
	obs.obs_data_release(settings)
	obs.obs_source_release(source)
end

local function emit(line)
	local file = io.open(script_path() .. "replay_events.txt", "a")
	if file then
		file:write(line .. "\n")
		file:close()
	end
end

local function report_scenes()
	local names = {}
	local scenes = obs.obs_frontend_get_scenes()
	if scenes ~= nil then
		for _, scene_source in ipairs(scenes) do
			table.insert(names, obs.obs_source_get_name(scene_source))
		end
		obs.source_list_release(scenes)
	end
	emit("scenes|" .. table.concat(names, "\t"))
	local current = obs.obs_frontend_get_current_scene()
	if current ~= nil then
		emit("current|" .. obs.obs_source_get_name(current))
		obs.obs_source_release(current)
	end
end

local function overlay_on_top(scene_source)
	local source = obs.obs_get_source_by_name(SOURCE_NAME)
	if source == nil then
		return
	end
	add_to_scene(scene_source, source)
	obs.obs_source_release(source)
	local scene = obs.obs_scene_from_source(scene_source)
	if scene == nil then
		return
	end
	local item = obs.obs_scene_find_source(scene, SOURCE_NAME)
	if item ~= nil then
		obs.obs_sceneitem_set_order(item, obs.OBS_ORDER_MOVE_TOP)
	end
end

local function find_transition(test)
	local found = nil
	local transitions = obs.obs_frontend_get_transitions()
	if transitions ~= nil then
		for _, transition in ipairs(transitions) do
			if found == nil and test(transition) then
				found = obs.obs_source_get_ref(transition)
			end
		end
		obs.source_list_release(transitions)
	end
	return found
end

local function restore_transition()
	obs.timer_remove(restore_transition)
	if restore_name == nil then
		return
	end
	local name = restore_name
	restore_name = nil
	local transition = find_transition(function(t) return obs.obs_source_get_name(t) == name end)
	if transition ~= nil then
		obs.obs_frontend_set_current_transition(transition)
		obs.obs_source_release(transition)
	end
end

local function use_cut()
	local cut = find_transition(function(t) return obs.obs_source_get_id(t) == "cut_transition" end)
	if cut == nil then
		return
	end
	local current = obs.obs_frontend_get_current_transition()
	if current ~= nil then
		if restore_name == nil and obs.obs_source_get_id(current) ~= "cut_transition" then
			restore_name = obs.obs_source_get_name(current)
		end
		obs.obs_source_release(current)
	end
	obs.obs_frontend_set_current_transition(cut)
	obs.obs_source_release(cut)
end

local function switch_scene(name)
	local target = obs.obs_get_source_by_name(name)
	if target == nil then
		return
	end
	overlay_on_top(target)
	use_cut()
	if obs.obs_frontend_preview_program_mode_active() then
		obs.obs_frontend_set_current_preview_scene(target)
		obs.obs_frontend_preview_program_trigger_transition()
	else
		obs.obs_frontend_set_current_scene(target)
	end
	obs.obs_source_release(target)
	obs.timer_remove(restore_transition)
	obs.timer_add(restore_transition, RESTORE_MS)
end

local function wait_for_service()
	checks = checks + 1
	if file_exists(ready_file()) then
		obs.timer_remove(wait_for_service)
		reload_overlay()
		report_scenes()
	elseif checks >= MAX_CHECKS then
		obs.timer_remove(wait_for_service)
	end
end

local function poll_commands()
	local path = script_path() .. "replay_cmd.txt"
	local file = io.open(path, "r")
	if not file then
		return
	end
	local commands = {}
	for line in file:lines() do
		table.insert(commands, line)
	end
	file:close()
	os.remove(path)
	for _, command in ipairs(commands) do
		if command == "start" then
			obs.obs_frontend_replay_buffer_start()
		elseif command == "stop" then
			obs.obs_frontend_replay_buffer_stop()
		elseif command == "save" then
			obs.obs_frontend_replay_buffer_save()
		elseif command:sub(1, 6) == "scene|" then
			switch_scene(command:sub(7))
		end
	end
end

local function on_event(event)
	if event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STARTED then
		emit("started")
	elseif event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_STOPPED then
		emit("stopped")
	elseif event == obs.OBS_FRONTEND_EVENT_REPLAY_BUFFER_SAVED then
		emit("saved|" .. tostring(obs.obs_frontend_get_last_replay()))
	end
	if event == obs.OBS_FRONTEND_EVENT_FINISHED_LOADING or event == obs.OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED then
		ensure_overlay()
		reload_overlay()
	end
	if event == obs.OBS_FRONTEND_EVENT_FINISHED_LOADING or event == obs.OBS_FRONTEND_EVENT_SCENE_COLLECTION_CHANGED
		or event == obs.OBS_FRONTEND_EVENT_SCENE_LIST_CHANGED or event == obs.OBS_FRONTEND_EVENT_SCENE_CHANGED then
		report_scenes()
	end
end

local function add_to_current(_, _)
	local source = obs.obs_get_source_by_name(SOURCE_NAME)
	local current = obs.obs_frontend_get_current_scene()
	if source ~= nil and current ~= nil then
		add_to_scene(current, source)
	end
	obs.obs_source_release(source)
	obs.obs_source_release(current)
	return false
end

function script_description()
	return "GVL Media Studio: Match-Grafiken aus dem Spiel direkt in OBS."
end

function script_properties()
	local props = obs.obs_properties_create()
	obs.obs_properties_add_button(props, "add_overlay", "Overlay in aktuelle Szene legen", add_to_current)
	return props
end

function script_load(_)
	start_service()
	obs.obs_frontend_add_event_callback(on_event)
	obs.timer_add(wait_for_service, CHECK_MS)
	obs.timer_add(poll_commands, COMMAND_POLL_MS)
end
