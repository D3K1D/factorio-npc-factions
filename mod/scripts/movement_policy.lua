-- Native movement alternatives. This module scores real walk/yield attempts;
-- it never moves a character or grants progress by changing game state.
local Policy={schema='npc-movement-v1',version=1}
Policy.feature_names={'bias','progress','clearance','route_clear','forward','retreat',
  'sideways','length','traffic','human_traffic','enemy_traffic','goal_distance',
  'stalled','attempts','recent_visit','no_path','yield','crowded'}
local DEFAULT={0,2.0,.5,1.2,.8,.1,.3,.1,-1,-3,-2,-.5,.2,0,-.7,0,-.4,-.8}
local RECORD_LIMIT=512
local function copy(v)
  if type(v)~='table' then return v end
  local out={};for key,value in pairs(v)do out[key]=copy(value)end
  return out
end
local function clamp(x,lo,hi)return math.max(lo,math.min(hi,x))end
local function finite(x)return type(x)=='number' and x==x and x~=math.huge and x~=-math.huge and math.abs(x)<=100 end
local function point(p)return {x=p.x or p[1],y=p.y or p[2]}end
local function distance(a,b)local x,y=a.x-b.x,a.y-b.y;return math.sqrt(x*x+y*y)end
local function state()
  storage.movement_policy=storage.movement_policy or {weights=copy(DEFAULT),
    training=false,explore=false,trace=false,records={},next_seq=1,dropped=0,decisions=0}
  return storage.movement_policy
end
function Policy.status()
  local s=state()
  return {schema=Policy.schema,version=Policy.version,feature_names=copy(Policy.feature_names),
    weights=copy(s.weights),training=s.training,explore=s.explore,trace=s.trace,
    records=#s.records,next_seq=s.next_seq,dropped=s.dropped,decisions=s.decisions}
end
-- The private server-only fixture calls this while paused. No player command
-- calls configure; validation also rejects malformed external checkpoints.
function Policy.configure(config)
  if not game.tick_paused then return false,'paused_host_configuration_required' end
  if type(config)~='table' or config.schema~=Policy.schema
      or config.version~=nil and config.version~=Policy.version then return false,'movement_schema_mismatch' end
  if config.feature_names then
    if type(config.feature_names)~='table' or #config.feature_names~=#Policy.feature_names then
      return false,'movement_feature_names_mismatch' end
    for i,name in ipairs(Policy.feature_names)do
      if config.feature_names[i]~=name then return false,'movement_feature_names_mismatch' end
    end
  end
  local weights=config.weights or DEFAULT
  if type(weights)~='table' or #weights~=#Policy.feature_names then return false,'movement_weight_count' end
  for i=1,#Policy.feature_names do if not finite(weights[i]) then return false,'movement_weight_nonfinite' end end
  for _,key in ipairs({'training','explore','trace'})do
    if config[key]~=nil and type(config[key])~='boolean' then return false,'movement_flag_invalid:'..key end
  end
  local s=state()
  s.weights=copy(weights)
  s.training=config.training==true;s.explore=config.explore==true and s.training;s.trace=config.trace==true
  return true,Policy.status()
end
function Policy.records(after_seq,limit)
  local s=state();local after=math.max(0,math.floor(tonumber(after_seq) or 0))
  local count=clamp(math.floor(tonumber(limit) or 64),1,128)
  local records={}
  for _,row in ipairs(s.records)do if row.seq>after then
    records[#records+1]=copy(row)
    if #records>=count then break end
  end end
  return {schema=Policy.schema,records=records,next_seq=s.next_seq,dropped=s.dropped}
end
local function standable(npc,p)
  local c=npc.character
  return c.surface.can_place_entity{name=c.name,position=p,force=npc.force,
    build_check_type=defines.build_check_type.manual}
end
local COMPASS={{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}}
local function clearance(npc,p)
  local count=0
  for _,v in ipairs(COMPASS)do
    local m=math.sqrt(v[1]^2+v[2]^2)
    if standable(npc,{x=p.x+v[1]/m*1.5,y=p.y+v[2]/m*1.5})then count=count+1 end
  end
  return count/8
end
local function route_clear(npc,current,target)
  local count=0
  for _,f in ipairs({.45,.7,.95})do
    local p={x=current.x+(target.x-current.x)*f,y=current.y+(target.y-current.y)*f}
    if standable(npc,p)then count=count+1 end
  end
  return count/3
end
local function bodies(npc,current)
  local c=npc.character;local people={};local ids={}
  for id,other in pairs(storage.npcs or {})do
    if other.character and other.character.valid and other.character.surface==c.surface then
      ids[other.character.unit_number]=id
    end
  end
  local humans={}
  for _,player in pairs(game.players)do
    if player.character and player.character.valid and player.character.surface==c.surface then
      humans[player.character.unit_number]=true
    end
  end
  for _,entity in ipairs(c.surface.find_entities_filtered{
      position=current,radius=10,type={'character','unit'},limit=64})do
    if entity.valid and entity~=c then
      local kind=ids[entity.unit_number] and 'npc'
          or (entity.type=='character' or humans[entity.unit_number]) and 'human'
          or 'enemy'
      people[#people+1]={position=point(entity.position),kind=kind,id=ids[entity.unit_number] or 0,
        unit_number=entity.unit_number or 0}
    end
  end
  table.sort(people,function(a,b)
    return a.unit_number<b.unit_number or a.unit_number==b.unit_number and a.id<b.id
  end)
  return people
end
local function traffic_at(people,p)
  local traffic,human,enemy,crowded,nearest_human=0,0,0,0,64
  for _,other in ipairs(people)do local d=distance(p,other.position)
    if d<=6 then
      local contribution=1/(1+d)
      if other.kind=='npc' then traffic=traffic+contribution
      elseif other.kind=='human' then human=human+contribution;nearest_human=math.min(nearest_human,d)
      else enemy=enemy+contribution end
    end
    if d<=3 then crowded=crowded+1 end
  end
  return clamp(traffic/3,0,1),clamp(human,0,1),clamp(enemy/2,0,1),
    clamp(crowded/4,0,1),nearest_human
end
local function recent(nav,p,tick)
  local value=0
  for _,entry in ipairs(nav.recent_positions or {})do
    if tick-entry.tick<=1800 then
      value=math.max(value,clamp(1-distance(entry.position,p)/2,0,1))
    end
  end
  for _,entry in ipairs(nav.failed_targets or {})do
    if tick-entry.tick<=1800 then
      value=math.max(value,clamp(1-distance(entry.position,p)/2,0,1))
    end
  end
  return value
end
function Policy.note_position(nav,p,tick)
  if tick<(nav.next_position_memory_tick or 0) then return end
  nav.next_position_memory_tick=tick+45
  local list=nav.recent_positions or {};nav.recent_positions=list
  list[#list+1]={position=point(p),tick=tick}
  while #list>8 do table.remove(list,1)end
end
function Policy.note_failure(nav,p,tick,reason)
  local list=nav.failed_targets or {};nav.failed_targets=list
  list[#list+1]={position=point(p),tick=tick,reason=reason}
  while #list>8 do table.remove(list,1)end
end
local function features(npc,nav,current,goal,target,people,tick,cause,is_yield)
  local step=distance(current,target)
  local gd=distance(current,goal)
  local tx,ty=target.x-current.x,target.y-current.y
  local gx,gy=goal.x-current.x,goal.y-current.y
  local forward=step>0 and gd>0 and clamp((tx*gx+ty*gy)/(step*gd),-1,1) or 0
  local traffic,human,enemy,crowded=traffic_at(people,target)
  return {1,clamp((gd-distance(target,goal))/4,-1,1),clearance(npc,target),
    is_yield and 1 or route_clear(npc,current,target),forward,math.max(0,-forward),
    1-math.abs(forward),clamp(step/3.5,0,1),traffic,human,enemy,
    clamp(distance(target,goal)/64,0,1),
    clamp((tick-(nav.progress_tick or tick))/180,0,1),clamp((nav.requests or 0)/5,0,1),
    recent(nav,target,tick),cause=='no_path' and 1 or 0,is_yield and 1 or 0,crowded}
end
local function score(weights,features)
  local value=0
  for i=1,#Policy.feature_names do value=value+weights[i]*features[i] end
  return value
end
local function priority_blocker(npc,people,current,goal)
  local chosen,closest
  for _,other in ipairs(people)do
    local d=distance(current,other.position)
    local priority=other.kind=='human' or other.kind=='enemy'
      or other.kind=='npc' and other.id<npc.id
    if priority and d<2.5 then
      -- only someone just ahead, in the way (within 60 degrees of the heading): not anyone nearby
      local gx,gy=goal.x-current.x,goal.y-current.y
      local ox,oy=other.position.x-current.x,other.position.y-current.y
      local g=math.sqrt(gx*gx+gy*gy)
      if g>0 and d>0 and gx*ox+gy*oy>=0.5*g*d and (not closest or d<closest)then chosen,closest=other,d end
    end
  end
  return chosen
end
function Policy.choose(npc,nav,goal,tick,cause)
  local s=state();local current=point(npc.character.position);goal=point(goal)
  local people=bodies(npc,current)
  local gx,gy=goal.x-current.x,goal.y-current.y
  local norm=math.sqrt(gx*gx+gy*gy)
  if norm<.01 then gx,gy,norm=0,-1,1 end
  local forward={gx/norm,gy/norm};local left={-forward[2],forward[1]}
  local axes={{'forward',forward[1],forward[2]}, {'left',left[1],left[2]},
    {'right',-left[1],-left[2]}, {'back',-forward[1],-forward[2]},
    {'front_left',forward[1]+left[1],forward[2]+left[2]},
    {'front_right',forward[1]-left[1],forward[2]-left[2]},
    {'back_left',-forward[1]+left[1],-forward[2]+left[2]},
    {'back_right',-forward[1]-left[1],-forward[2]-left[2]}}
  local blocker=priority_blocker(npc,people,current,goal)
  local candidates={}
  for _,length in ipairs({2,3.5})do for _,axis in ipairs(axes)do
    local m=math.sqrt(axis[2]^2+axis[3]^2)
    local proposed={x=current.x+axis[2]/m*length,y=current.y+axis[3]/m*length}
    local found=npc.character.surface.find_non_colliding_position('character',proposed,.5,.25)
    if found and distance(found,current)>=.9 then
      local target=point(found)
      local blocked=blocker and distance(target,blocker.position)
        <=distance(current,blocker.position)+.2
      if not blocked then
        local f=features(npc,nav,current,goal,target,people,tick,cause,false)
        -- A direct walk is short and must have a plausible native corridor.
        if f[4]>=2/3 then
          candidates[#candidates+1]={index=#candidates+1,id=axis[1]..(length==2 and '2' or '35'),
            target=target,features=f,base_score=score(s.weights,f)}
        end
      end
    end
  end end
  local hold=features(npc,nav,current,goal,current,people,tick,cause,true)
  candidates[#candidates+1]={index=#candidates+1,id='yield',target=current,
    features=hold,base_score=score(s.weights,hold)}
  local best=1
  for i=2,#candidates do
    if candidates[i].base_score>candidates[best].base_score+1e-9 then best=i end
  end
  s.decisions=s.decisions+1
  local selected=best;local exploration=false
  if s.training and s.explore and s.decisions%4==0 and #candidates>1 then
    selected=1+((npc.id*131+s.decisions*17+(nav.recovery_attempts or 0)*7)%#candidates)
    exploration=selected~=best
  end
  local choice=candidates[selected]
  local episode={actor_id=npc.id,decision_tick=tick,cause=cause,
    candidates=candidates,chosen_candidate_index=selected,exploration=exploration,
    start_position=current,start_distance_to_goal=distance(current,goal),goal=goal,
    target=choice.target,start_clearance=clearance(npc,current),
    start_human_distance=select(5,traffic_at(people,current)),trace=s.trace}
  return choice,episode
end
function Policy.finish(npc,episode,tick,terminal_reason,path_reopened)
  if not episode or episode.finished then return end
  episode.finished=true
  local c=npc.character
  local final=c and c.valid and point(c.position) or episode.start_position
  local people=c and c.valid and bodies(npc,final) or {}
  local finish_human=select(5,traffic_at(people,final))
  local elapsed=math.max(0,tick-episode.decision_tick)
  local end_distance=distance(final,episode.goal)
  local actual_progress=episode.start_distance_to_goal-end_distance
  local displacement=distance(final,episode.start_position)
  local clearance_delta=c and c.valid and clearance(npc,final)-episode.start_clearance or 0
  local human_delta=finish_human-episode.start_human_distance
  local reached=distance(final,episode.target)<=.6
  local failed=terminal_reason=='no_path' or terminal_reason=='movement_stalled'
      or terminal_reason=='path_timeout' or terminal_reason=='recovery_timeout'
  local reward=clamp(clamp(actual_progress/4,-1,1)
      +(path_reopened and .6 or 0)+.15*clamp(displacement/3.5,0,1)
      +.2*clamp(clearance_delta,-1,1)+.2*clamp(human_delta/3,-1,1)
      -.2*clamp(elapsed/180,0,1)-(failed and .6 or 0),-2,2)
  if not episode.trace then return end
  local s=state()
  local row={schema=Policy.schema,seq=s.next_seq,actor_id=episode.actor_id,
    decision_tick=episode.decision_tick,cause=episode.cause,
    candidates=episode.candidates,chosen_candidate_index=episode.chosen_candidate_index,
    exploration=episode.exploration,
    outcome={end_tick=tick,start_distance_to_goal=episode.start_distance_to_goal,
      end_distance_to_goal=end_distance,actual_displacement=displacement,
      actual_progress=actual_progress,reached_target=reached,
      path_reopened=path_reopened==true,clearance_delta=clearance_delta,
      human_clearance_delta=human_delta,elapsed_ticks=elapsed,
      terminal_reason=terminal_reason,reward=reward}}
  s.next_seq=s.next_seq+1
  s.records[#s.records+1]=row
  while #s.records>RECORD_LIMIT do table.remove(s.records,1);s.dropped=s.dropped+1 end
end
function Policy.priority_blocker(npc,goal)
  local current=point(npc.character.position)
  return priority_blocker(npc,bodies(npc,current),current,goal)
end
function Policy.idle_blocker(npc)
  local current=point(npc.character.position)
  local nearest,nearest_distance
  for _,other in ipairs(bodies(npc,current))do
    local higher=other.kind=='human' or other.kind=='npc' and other.id<npc.id
    local d=distance(current,other.position)
    if higher and d<3.5 and (not nearest_distance or d<nearest_distance)then
      nearest,nearest_distance=other,d
    end
  end
  return nearest
end
-- Smooth walking. A character's walk animation restarts whenever its walking state stops or its
-- direction flips, so: one of the eight directions toward `to`, kept while the way still lies
-- within 32 degrees of it (no flicker between two directions on a slanted path), and a stop that
-- keeps the way it faces.
local OCTANTS
function Policy.steer(nav,from,to)
  OCTANTS=OCTANTS or {defines.direction.north,defines.direction.northeast,defines.direction.east,
    defines.direction.southeast,defines.direction.south,defines.direction.southwest,defines.direction.west,
    defines.direction.northwest}
  local bearing=math.deg(math.atan2(to.x-from.x,-(to.y-from.y)))%360
  local o=math.floor((bearing+22.5)/45)%8
  -- Held within 32 degrees, so a slanted path does not flicker; walking strictly (after getting
  -- stuck, in a forest), always the nearest direction.
  local kept=nav and not nav.strict and nav.octant
  if kept and math.abs((bearing-kept*45+180)%360-180)<=32 then o=kept end
  if nav then nav.octant=o end
  return OCTANTS[o+1]
end
function Policy.halt(character)
  if character and character.valid then
    local was=character.walking_state
    character.walking_state={walking=false,direction=was and was.direction or defines.direction.north}
  end
end
-- Waypoints already within reach are passed without stopping (the last one must be reached closer);
-- returns whether any was passed, and the point to steer at: the next waypoint, or the one after it
-- once the next is close, so the walk flows round the path's corners. Walking strictly (nav.strict,
-- once it got stuck), every waypoint is reached and no corner is cut.
local function at(w) local p=w.position or w; return {x=p.x,y=p.y} end
function Policy.follow(nav,from)
  local passed=false
  while nav.path[nav.index] do
    local reach=nav.index<#nav.path and not nav.strict and 0.8 or 0.35
    if distance(from,at(nav.path[nav.index]))>reach then break end
    nav.index=nav.index+1
    passed=true
  end
  local w=nav.path[nav.index]
  if not w then return passed,nil,nil end
  local p=at(w)
  local after=nav.path[nav.index+1]
  local aim=(after and not nav.strict and distance(from,p)<1.2) and at(after) or p
  return passed,p,aim
end

return Policy
