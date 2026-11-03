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
local CONFERENCE_TYPE = "vdoninja_service"
local CONFERENCE_BACKUP = "conference_backup.json"

local shell = ffi.load("shell32")
local checks = 0
local restore_name = nil
local pending = {}
local save_requested = false
local save_requested_at = 0
local SAVE_RETRY_NS = 1500000000
local queue_ui
local restore_after_stop = false

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

local function restore_due()
	obs.timer_remove(restore_due)
	queue_ui({ "restore" })
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
	obs.timer_remove(restore_due)
	obs.timer_add(restore_due, RESTORE_MS)
end

local function backup_path()
	return script_path() .. CONFERENCE_BACKUP
end

local function module_ready()
	return obs.obs_service_get_display_name(CONFERENCE_TYPE) ~= nil
end

local function current_service_type()
	local service = obs.obs_frontend_get_streaming_service()
	if service == nil then
		return ""
	end
	return obs.obs_service_get_type(service) or ""
end

local function current_stream_id()
	local service = obs.obs_frontend_get_streaming_service()
	if service == nil then
		return ""
	end
	local settings = obs.obs_service_get_settings(service)
	local stream_id = obs.obs_data_get_string(settings, "stream_id")
	obs.obs_data_release(settings)
	return stream_id
end

local function use_service(service)
	obs.obs_frontend_set_streaming_service(service)
	obs.obs_frontend_save_streaming_service()
	obs.obs_service_release(service)
end

local function report_conference()
	emit("module|" .. (module_ready() and "1" or "0"))
	if current_service_type() == CONFERENCE_TYPE then
		emit("conference|on|" .. current_stream_id())
	else
		emit("conference|off")
	end
	emit("streaming|" .. (obs.obs_frontend_streaming_active() and "1" or "0"))
end

local function start_conference(stream_id, password)
	if not module_ready() then
		emit("conference|missing")
		return
	end
	if obs.obs_frontend_streaming_active() and current_service_type() ~= CONFERENCE_TYPE then
		emit("conference|busy")
		return
	end
	local current = obs.obs_frontend_get_streaming_service()
	if current ~= nil and obs.obs_service_get_type(current) ~= CONFERENCE_TYPE then
		local backup = obs.obs_data_create()
		local settings = obs.obs_service_get_settings(current)
		obs.obs_data_set_string(backup, "type", obs.obs_service_get_type(current))
		obs.obs_data_set_obj(backup, "settings", settings)
		obs.obs_data_save_json(backup, backup_path())
		obs.obs_data_release(settings)
		obs.obs_data_release(backup)
	end
	local settings = obs.obs_data_create()
	obs.obs_data_set_string(settings, "stream_id", stream_id)
	obs.obs_data_set_string(settings, "password", password)
	local service = obs.obs_service_create(CONFERENCE_TYPE, "GVL Konferenz", settings, nil)
	obs.obs_data_release(settings)
	if service == nil then
		emit("conference|missing")
		return
	end
	use_service(service)
	emit("conference|on|" .. stream_id)
end

local function restore_service()
	restore_after_stop = false
	local service = nil
	local backup = obs.obs_data_create_from_json_file(backup_path())
	if backup ~= nil then
		local kind = obs.obs_data_get_string(backup, "type")
		local settings = obs.obs_data_get_obj(backup, "settings")
		if kind ~= "" and kind ~= CONFERENCE_TYPE then
			service = obs.obs_service_create(kind, "default_service", settings, nil)
		end
		if settings ~= nil then
			obs.obs_data_release(settings)
		end
		obs.obs_data_release(backup)
	end
	if service == nil then
		service = obs.obs_service_create("rtmp_common", "default_service", nil, nil)
	end
	if service ~= nil then
		use_service(service)
	end
	os.remove(backup_path())
	emit("conference|off")
end

local function stop_conference()
	if current_service_type() ~= CONFERENCE_TYPE then
		os.remove(backup_path())
		emit("conference|off")
	elseif obs.obs_frontend_streaming_active() then
		restore_after_stop = true
		obs.obs_frontend_streaming_stop()
	else
		restore_service()
	end
end

-- Timer laufen im Grafik-Thread. Szene, Übergang und Stream-Dienst darf man dort nicht umstellen: OBS wartet dafür
-- auf den Oberflächen-Thread, der seinerseits auf dieses Skript wartet, und friert ein. Solche Befehle laufen
-- deshalb im Speicher-Callback, den OBS im Oberflächen-Thread aufruft.
local function run_pending()
	local commands = pending
	pending = {}
	save_requested = false
	for _, parts in ipairs(commands) do
		if parts[1] == "start" then
			start_conference(parts[2], parts[3])
		elseif parts[1] == "stop" then
			stop_conference()
		elseif parts[1] == "scene" then
			switch_scene(parts[2])
		elseif parts[1] == "restore" then
			restore_transition()
		end
	end
end

local function on_save(_, saving)
	if saving and #pending > 0 then
		run_pending()
	end
end

queue_ui = function(parts)
	table.insert(pending, parts)
	if not save_requested then
		save_requested = true
		save_requested_at = obs.os_gettime_ns()
		obs.obs_frontend_save()
	end
end

-- Während OBS lädt oder sich beendet, speichert es nicht. Dann bleibt der Befehl liegen, bis ein neuer Versuch klappt.
local function retry_save()
	if save_requested and #pending > 0 and obs.os_gettime_ns() - save_requested_at > SAVE_RETRY_NS then
		save_requested_at = obs.os_gettime_ns()
		obs.obs_frontend_save()
	end
end

local function conference_command(command)
	local parts = {}
	for part in (command .. "|"):gmatch("([^|]*)|") do
		table.insert(parts, part)
	end
	local kind = parts[2]
	if kind == "start" and parts[3] and parts[4] and parts[3] ~= "" and parts[4] ~= "" then
		queue_ui({ "start", parts[3], parts[4] })
	elseif kind == "stop" then
		queue_ui({ "stop" })
	elseif kind == "stream" then
		if parts[3] == "1" and current_service_type() == CONFERENCE_TYPE and not obs.obs_frontend_streaming_active() then
			obs.obs_frontend_streaming_start()
		elseif parts[3] == "0" and current_service_type() == CONFERENCE_TYPE and obs.obs_frontend_streaming_active() then
			obs.obs_frontend_streaming_stop()
		end
	end
end

local function wait_for_service()
	checks = checks + 1
	if file_exists(ready_file()) then
		obs.timer_remove(wait_for_service)
		reload_overlay()
		report_scenes()
		report_conference()
	elseif checks >= MAX_CHECKS then
		obs.timer_remove(wait_for_service)
	end
end

local function poll_commands()
	retry_save()
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
			queue_ui({ "scene", command:sub(7) })
		elseif command:sub(1, 11) == "conference|" then
			conference_command(command)
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
	elseif event == obs.OBS_FRONTEND_EVENT_STREAMING_STARTED then
		emit("streaming|1")
	elseif event == obs.OBS_FRONTEND_EVENT_STREAMING_STOPPED then
		emit("streaming|0")
		if restore_after_stop then
			restore_service()
		end
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
	obs.obs_frontend_add_save_callback(on_save)
	obs.timer_add(wait_for_service, CHECK_MS)
	obs.timer_add(poll_commands, COMMAND_POLL_MS)
end
