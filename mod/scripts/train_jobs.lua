-- Carrier-owned native train controls. This module moves no inventory, changes
-- no train speed, and never treats the train engine's station access as a treaty.
local Routes=require("scripts.rail_trade_projects")
local TrainJobs={}
local LIMIT=128
local function identifier(value)
 return type(value)=="string" and #value>=1 and #value<=96 and value:match("^[%w_-]+$")~=nil
end
local function key(job) return job.route_id..":"..job.journey_id end
local function journeys()
 storage.rail_worker_journeys=storage.rail_worker_journeys or {entries={},order={}}
 return storage.rail_worker_journeys
end
local function route(npc,job)
 if not identifier(job.route_id) then return nil,"invalid_rail_route_id" end
 if not Routes.worker_route then return nil,"rail_route_controller_unavailable" end
 local r,e=Routes.worker_route(npc,job)
 if not r then return nil,e or "rail_route_unavailable" end
 if not e or not r.station_names then return nil,"invalid_saved_rail_route" end
 for _,role in ipairs({"source","destination"}) do
  local stop=e[role.."_stop"];local name=r.station_names[role]
  if not stop or not stop.valid or stop.type~="train-stop" or stop.force~=npc.force
      or stop.surface~=npc.character.surface or type(name)~="string" or #name<1 or #name>200 then
   return nil,"invalid_owned_rail_stop"
  end
 end
 if r.station_names.source==r.station_names.destination then return nil,"ambiguous_rail_station_names" end
 return r,e
end
local function unique_stop(npc,stop,name)
 local found=game.train_manager.get_train_stops{station_name=name,force=npc.force,surface=stop.surface}
 return #found==1 and found[1]==stop
end
local function train_for(npc,e)
 local wagon=e.wagon;local front,rear=e.front_locomotive,e.rear_locomotive
 if not wagon or not wagon.valid or wagon.type~="cargo-wagon" or wagon.force~=npc.force
     or wagon.surface~=npc.character.surface or not front or not front.valid or not rear or not rear.valid
     or front.type~="locomotive" or rear.type~="locomotive" or front.force~=npc.force or rear.force~=npc.force then
  return nil,"owned_three_car_train_required"
 end
 local train=wagon.train
 if not train or not train.valid or front.train~=train or rear.train~=train or #train.carriages~=3 then
  return nil,"exact_owned_three_car_train_required"
 end
 local permitted={[wagon.unit_number]=true,[front.unit_number]=true,[rear.unit_number]=true}
 for _,entity in ipairs(train.carriages) do
  if not permitted[entity.unit_number] or entity.force~=npc.force or entity.surface~=wagon.surface then
   return nil,"unexpected_rail_carriage"
  end
 end
 return train
end
local function inventory(inv)
 local out={}
 if inv and inv.valid then for i=1,#inv do local stack=inv[i]
  if stack.valid_for_read then
   local name=stack.name.."@"..stack.quality.name
   out[name]=(out[name] or 0)+stack.count
  end
 end end
 return out
end
local function snapshot(train)
 local result={train_id=train.id,state=train.state,speed=train.speed,cargo={},fuel={},carriage_units={}}
 for _,entity in ipairs(train.carriages) do
  result.carriage_units[#result.carriage_units+1]=entity.unit_number
  if entity.type=="cargo-wagon" then
   result.cargo=inventory(entity.get_inventory(defines.inventory.cargo_wagon))
  elseif entity.type=="locomotive" then
   result.fuel[tostring(entity.unit_number)]={stock=inventory(entity.get_fuel_inventory()),
    burning_joules=entity.burner and entity.burner.remaining_burning_fuel or 0,
    burning=entity.burner and entity.burner.currently_burning and entity.burner.currently_burning.name or nil}
  end
 end
 if train.station then result.station_unit_number=train.station.unit_number end
 local first=train.carriages[1]
 result.position={x=first.position.x,y=first.position.y}
 return result
end
local function fuel_ready(e)
 for _,locomotive in ipairs({e.front_locomotive,e.rear_locomotive}) do
  local inv=locomotive.get_fuel_inventory()
  if (not inv or inv.is_empty()) and (not locomotive.burner or locomotive.burner.remaining_burning_fuel<=0) then return false end
 end
 return true
end
-- Clear the line physically before releasing a train. Native interaction
-- reach also allows standing on the tracks, where a departing train can kill
-- its loader. This initial controller only accepts a stopped vertical train.
function TrainJobs.dispatch_goal(npc,job)
 local r,e=route(npc,job);if not r then return nil,e end
 if job.target~=e.wagon then return nil,"rail_wagon_target_mismatch" end
 local train,reason=train_for(npc,e);if not train then return nil,reason end
 if math.abs(train.speed)>.000001 then return nil,"rail_train_must_be_stopped" end
 local orientation=e.wagon.orientation%.5
 if orientation>.000001 and orientation<.499999 then return nil,"rail_dispatch_requires_vertical_train" end
 local p=e.wagon.position
 local goal=job.dispatch_goal
 if goal and math.abs(goal.x-p.x)>=3.5 and math.abs(goal.x-p.x)<=7
     and math.abs(goal.y-p.y)<=2 then return goal end
 local selected,best
 for _,side in ipairs({-1,1}) do
  local candidate=npc.character.surface.find_non_colliding_position("character",{x=p.x+side*5,y=p.y},1,.25)
  if candidate and math.abs(candidate.x-p.x)>=3.5 and math.abs(candidate.y-p.y)<=2 then
   local dx,dy=candidate.x-npc.character.position.x,candidate.y-npc.character.position.y
   local d=dx*dx+dy*dy
   if not best or d<best then selected,best=candidate,d end
  end
 end
 if not selected then return nil,"safe_rail_dispatch_side_unavailable" end
 job.dispatch_goal={x=selected.x,y=selected.y}
 return job.dispatch_goal
end
function TrainJobs.configure(npc,job)
 if job.station_role~="source" and job.station_role~="destination" then return false,"invalid_rail_station_role" end
 local r,e=route(npc,job);if not r then return false,e end
 local stop=e[job.station_role.."_stop"];local name=r.station_names[job.station_role]
 if job.target~=stop or not npc.character.can_reach_entity(stop) then return false,"rail_stop_target_or_reach_mismatch" end
 local existing=game.train_manager.get_train_stops{station_name=name,force=npc.force,surface=stop.surface}
 for _,other in ipairs(existing) do if other~=stop then return false,"duplicate_rail_station_name" end end
 stop.backer_name=name
 if not unique_stop(npc,stop,name) then return false,"rail_stop_name_verification_failed" end
 return true,{route_id=job.route_id,station_role=job.station_role,station_unit_number=stop.unit_number,station_name=name}
end
function TrainJobs.step(npc,job,tick)
 if job.destination_role~="source" and job.destination_role~="destination" then return "failed","invalid_rail_destination_role" end
 if not identifier(job.journey_id) then return "failed","invalid_rail_journey_id" end
 local r,e=route(npc,job);if not r then return "failed",e end
 if job.target~=e.wagon then return "failed","rail_wagon_target_mismatch" end
 local train,reason=train_for(npc,e);if not train then return "failed",reason end
 local stop=e[job.destination_role.."_stop"];local name=r.station_names[job.destination_role]
 if stop.backer_name~=name or not unique_stop(npc,stop,name) then return "failed","rail_destination_changed" end
 if not job.journey_started then
  if not npc.character.can_reach_entity(e.wagon) then return "failed","rail_dispatch_out_of_reach" end
  if math.abs(npc.character.position.x-e.wagon.position.x)<3 then return "failed","rail_dispatch_body_on_track" end
  if math.abs(train.speed)>.000001 then return "failed","rail_train_must_be_stopped" end
  if not fuel_ready(e) then return "failed","rail_locomotive_fuel_missing" end
  local sequence=r.expected_journey_number
  if type(sequence)~="number" or sequence~=math.floor(sequence) or sequence<1
      or sequence<=(r.dispatched_highwater or 0) then return "failed","duplicate_rail_journey" end
  if r.active_job_id and r.active_job_id~=job.id then return "failed","rail_route_job_already_active" end
  local board=journeys();local id=key(job)
  if board.entries[id] then return "failed","duplicate_rail_journey" end
  if #board.order>=LIMIT then
   local removed
   for i,oldid in ipairs(board.order) do
    if board.entries[oldid].status~="active" then board.entries[oldid]=nil;table.remove(board.order,i);removed=true;break end
   end
   if not removed then return "failed","rail_journey_board_full" end
  end
  r.dispatched_highwater=sequence;r.active_job_id=job.id
  job.route=r;job.route_sequence=sequence
  job.train,job.train_id,job.journey_started=train,train.id,tick
  job.departure=snapshot(train);job.destination_unit_number=stop.unit_number
  job.native_dispatch={route_id=job.route_id,journey_id=job.journey_id,job_id=job.id,train_id=train.id,
    status="active",dispatch_tick=tick,destination_role=job.destination_role,departure=job.departure}
  board.entries[id]=job.native_dispatch;board.order[#board.order+1]=id
  train.schedule={current=1,records={{station=name,wait_conditions={{type="time",ticks=36000}}}}}
  train.manual_mode=false
  job.state="waiting_for_native_train"
  return "active"
 end
 if r.active_job_id~=job.id or r.dispatched_highwater~=job.route_sequence then return "failed","rail_journey_ownership_changed" end
 if train~=job.train or train.id~=job.train_id or stop.unit_number~=job.destination_unit_number then return "failed","rail_train_or_station_identity_changed" end
 if train.manual_mode then return "failed","rail_train_control_withdrawn" end
 if math.abs(train.speed)>.000001 and not job.departed_tick then
  job.departed_tick=tick;job.native_dispatch.departed_tick=tick
 end
 if train.station==stop and train.state==defines.train_state.wait_station and math.abs(train.speed)<=.000001 then
  job.arrival=snapshot(train);job.arrival_tick=tick
  train.manual_mode=true
  return "complete"
 end
 if train.state==defines.train_state.no_path or train.state==defines.train_state.path_lost
     or train.state==defines.train_state.destination_full or train.state==defines.train_state.no_schedule then
  return "failed","native_rail_route_blocked"
 end
 job.state="waiting_for_native_train"
 return "active"
end
function TrainJobs.finish(npc,job,status,tick)
 if job.kind~="rail_move" then return nil end
 local result={route_id=job.route_id,journey_id=job.journey_id,train_id=job.train_id,
  dispatch_tick=job.journey_started,departure_tick=job.departed_tick,arrival_tick=job.arrival_tick,
  departure=job.departure,arrival=job.arrival,cargo_loss={},cargo_gain={}}
 local train=job.train
 if train and train.valid then
  local own=true
  for _,c in ipairs(train.carriages) do if c.force~=npc.force then own=false;break end end
  if own and train.id==job.train_id then
   result.terminal_snapshot=snapshot(train)
   if status~="complete" then train.manual_mode=true;result.native_automatic_control_withdrawn=true end
  end
 end
 if job.departure and not result.terminal_snapshot then
  if job.target and job.target.valid and job.target.force==npc.force and job.target.type=="cargo-wagon" then
   result.terminal_snapshot={cargo=inventory(job.target.get_inventory(defines.inventory.cargo_wagon)),train_identity_lost=true}
  else result.cargo_loss_unknown=true end
 end
 local last=job.arrival or result.terminal_snapshot
 if job.departure and last then
  for name,count in pairs(job.departure.cargo) do local delta=count-(last.cargo[name] or 0)
   if delta>0 then result.cargo_loss[name]=delta end
  end
  for name,count in pairs(last.cargo) do local delta=count-(job.departure.cargo[name] or 0)
   if delta>0 then result.cargo_gain[name]=delta end
  end
 end
 if job.route and job.route.active_job_id==job.id then job.route.active_job_id=nil end
 if job.native_dispatch then
  job.native_dispatch.status=status;job.native_dispatch.finished_tick=tick
  job.native_dispatch.receipt_id=tostring(npc.id)..":"..tostring(job.id)
  job.native_dispatch.arrival=job.arrival;job.native_dispatch.cargo_loss=result.cargo_loss
 end
 return result
end
return TrainJobs
