-- Real graphics for test runs (tools/video.py). A headless server cannot draw, so the runner asks
-- for a frame: this marks the game with where to look and saves it. After the run a graphical
-- Factorio loads each such save on its own (single player) and on its first tick this takes
-- screenshots of every engineer and its home, to script-output/npc-video/<save>/, with a
-- frame.json saying which picture is which. The live server never draws (it is multiplayer).
--   /npc-camera frame {"name":"video-0001"} | done   (RCON or admins)
local Camera={}

local VIEWS={{kind="engineer",zoom=0.5},{kind="base",zoom=0.25}}

local function round(v) return math.floor(v*10+0.5)/10 end

local function number(v,lo,hi,default)
  v=tonumber(v) or default
  return math.max(lo,math.min(hi,v))
end

-- Where to look is decided here, on the server, when the frame is asked for; the client only draws.
local function views(zoom)
  local out={}
  for id=1,10 do
    local npc=storage.npcs and storage.npcs[id]
    if npc then
      local body=npc.character
      local alive=body and body.valid
      local surface=alive and body.surface.index or 1
      for _,view in ipairs(VIEWS) do
        local at=view.kind=="engineer" and (alive and body.position) or (view.kind=="base" and npc.home)
        if at then
          out[#out+1]={id=id,name=npc.name,kind=view.kind,surface=surface,x=round(at.x),y=round(at.y),
            zoom=zoom[view.kind] or view.zoom}
        end
      end
    end
  end
  return out
end

function Camera.frame(opts)
  opts=type(opts)=="table" and opts or {}
  local name=type(opts.name)=="string" and opts.name:gsub("[^%w%-_]","_"):sub(1,64) or ("video-"..game.tick)
  local zoom=type(opts.zoom)=="table" and opts.zoom or {}
  local daytime=number(opts.daytime,0,0.99,0)   -- 0 is noon: no dark frames; false keeps the game's own
  if opts.daytime==false then daytime=nil end
  local c={name=name,tick=game.tick,
    resolution={x=math.floor(number(opts.width,64,4096,1280)/2)*2,y=math.floor(number(opts.height,64,4096,720)/2)*2},
    daytime=daytime,alt=opts.alt==true,views=views(zoom)}
  storage.camera=c
  game.server_save(name)   -- the save carries the mark; the runner clears it (done) once it is written
  return {ok=true,save=name,tick=game.tick,views=#c.views}
end

function Camera.done()
  storage.camera=nil
  return {ok=true,tick=game.tick}
end

local function shoot(c)
  local dir="npc-video/"..c.name.."/"
  local shots={}
  for _,v in ipairs(c.views or {}) do
    local file=v.id.."-"..v.kind..".jpg"
    local ok,problem=pcall(game.take_screenshot,{surface=game.surfaces[v.surface] or game.surfaces[1],
      position={x=v.x,y=v.y},resolution=c.resolution,zoom=v.zoom,path=dir..file,show_gui=false,
      show_entity_info=c.alt,daytime=c.daytime,anti_alias=true,quality=90,force_render=true})
    shots[#shots+1]={id=v.id,name=v.name,kind=v.kind,file=ok and file or nil,x=v.x,y=v.y,zoom=v.zoom,
      error=not ok and tostring(problem):sub(1,200) or nil}
  end
  game.set_wait_for_screenshots_to_finish()
  helpers.write_file(dir.."frame.json",helpers.table_to_json({tick=c.tick,save=c.name,resolution=c.resolution,views=shots}),false)
end

-- First tick of a single-player load of a marked save: one set of pictures, then the mark is gone.
function Camera.tick()
  local c=storage.camera
  if not c or game.is_multiplayer() then return end
  storage.camera=nil
  local ok,problem=pcall(shoot,c)
  if not ok then log("[npc-factions] camera: "..tostring(problem)) end
end

function Camera.register_commands()
  commands.add_command("npc-camera","Test runs: save a frame for tools/video.py (frame {json} | done)",function(command)
    local player=command.player_index and game.get_player(command.player_index)
    if player and not player.admin then player.print("Only admins can use the camera."); return end
    local p=command.parameter or ""
    local ok,result=pcall(function()
      if p=="frame" or p:sub(1,6)=="frame " then
        local parsed,value=pcall(helpers.json_to_table,p:sub(7))
        return Camera.frame(parsed and value or {})
      elseif p=="done" then return Camera.done() end
      return {ok=false,reason="usage: frame {json} | done"}
    end)
    if not ok then result={ok=false,reason=tostring(result):sub(1,300)} end
    if player then player.print(helpers.table_to_json(result))
    else rcon.print("NPC_CAMERA_RESULT "..helpers.table_to_json(result)) end
  end)
end

return Camera
