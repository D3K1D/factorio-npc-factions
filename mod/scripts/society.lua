-- Saved, finite faction diplomacy. This module changes force relations only
-- after validated decisions; physical exchanges and hostile acts are intents
-- for Worker, never scripted item movement or free damage.
local Society = {}
local Personality = require("scripts.personality")
local Strategy = require("scripts.strategy_policy")
local Cooperation = require("scripts.cooperation")
local SharedProjects = require("scripts.shared_projects")
local Relations = require("scripts.relations")
local Character = require("scripts.character")
local TeamWorld = require("scripts.team_world")
local CONTACT_RADIUS = 1024
local CONTACT_MEMORY = 60 * 60 * 10
local OFFER_LIFETIME = 60 * 60 * 3
local RELATION_GRACE = 72000
local PAIR_COOLDOWN = 60 * 30
local DECISION_PACE = 60 * 30
local MAX_LOG = 128
local MAX_CLAIMS = 128

local function state()
  if not storage.society then Society.init() end
  return storage.society
end

local function live(id)
  local npc=storage.npcs and storage.npcs[id]
  return npc and npc.character and npc.character.valid and npc.force and npc.force.valid
      and npc.character.force==npc.force and npc or nil
end

local function distance(a,b)
  local dx,dy=a.x-b.x,a.y-b.y
  return math.sqrt(dx*dx+dy*dy)
end

local function pair(a,b)
  if a>b then a,b=b,a end
  return a..":"..b
end

local function clamp(x,lo,hi) return math.max(lo,math.min(hi,x)) end
local function profile(npc,key,default)
  local x=npc.profile and npc.profile[key]
  if type(x)~="number" or x~=x then return default end
  return clamp(x,0,1)
end
local function axis(npc,key,default)
  local axes=Personality.axes(npc)
  local value=axes and axes[key]
  if type(value)~="number" or value~=value then return default end
  return clamp(value,0,1)
end

-- The full record stays in the society log; what engineers say about it in
-- chat goes through the character layer (memory, relationships, voice).
local function log_event(actor,text,kind,details)
  local s=state()
  s.events[#s.events+1]={tick=game.tick,actor=actor and actor.id or nil,
    kind=kind,text=text,details=details}
  while #s.events>MAX_LOG do table.remove(s.events,1) end
  if log then log("[npc-factions] "..(actor and actor.name..": " or "")..text) end
  Character.on_society(actor,kind,details)
end

local function relation(a,b)
  local s=state()
  local key=pair(a,b)
  local r=s.relations[key]
  if not r then r={trade=false,coalition_id=nil,changed_tick=-RELATION_GRACE}; s.relations[key]=r end
  local na,nb=live(a),live(b)
  if na and nb then
    local fa,fb=na.force,nb.force
    local current=fa.get_friend(fb) and fb.get_friend(fa) and "allied"
        or fa.get_cease_fire(fb) and fb.get_cease_fire(fa) and "peace" or "war"
    if r.last_force_relation and r.last_force_relation~=current then r.changed_tick=game.tick end
    r.last_force_relation=current
  end
  return r
end

-- Candidate and status reads must not create a relation or advance its grace
-- clock. Society.tick synchronizes observed force flags before decisions.
local function saved_relation(a,b)
  local s=storage.society
  return s and s.relations and s.relations[pair(a,b)]
      or {trade=false,coalition_id=nil,changed_tick=-RELATION_GRACE}
end

local function force_relation(a,b)
  local fa,fb=a.force,b.force
  if fa.get_friend(fb) and fb.get_friend(fa) then return "allied" end
  if fa.get_cease_fire(fb) and fb.get_cease_fire(fa) then return "peace" end
  return "war"
end

local function set_flags(a,b,friend,cease)
  local fa,fb=a.force,b.force
  local old={fa.get_friend(fb),fb.get_friend(fa),fa.get_cease_fire(fb),fb.get_cease_fire(fa)}
  local ok,reason=pcall(function()
    fa.set_friend(fb,friend); fb.set_friend(fa,friend)
    fa.set_cease_fire(fb,cease); fb.set_cease_fire(fa,cease)
  end)
  if not ok then
    pcall(function()
      fa.set_friend(fb,old[1]); fb.set_friend(fa,old[2])
      fa.set_cease_fire(fb,old[3]); fb.set_cease_fire(fa,old[4])
    end)
    return false,tostring(reason)
  end
  return true
end

-- How much engineer a trusts engineer b, from the shared relationship model
-- (chronicle events such as treaties, deliveries and thefts move it).
local function trust(a,b)
  return Relations.get(a,"npc:"..b,game.tick).trust
end
local saved_trust=trust

local function stock(npc,item)
  local inv=npc.character.get_main_inventory()
  return inv and inv.valid and inv.get_item_count{name=item,quality="normal"} or 0
end

local function own_demand(npc)
  local coal=stock(npc,"coal")
  local iron=stock(npc,"iron-plate")
  local copper=stock(npc,"copper-plate")
  local wants={}
  if coal<12 then wants[#wants+1]={item="coal",count=12-coal} end
  if iron<20 then wants[#wants+1]={item="iron-plate",count=20-iron} end
  if npc.factory_project and npc.factory_project.status=="active" and copper<10 then
    wants[#wants+1]={item="copper-plate",count=10-copper}
  end
  local role=Society.ensure(npc).role
  if role=="defender" then
    local ammo=stock(npc,"firearm-magazine")
    if ammo<6 then wants[#wants+1]={item="firearm-magazine",count=6-ammo,purpose="defense"} end
  elseif role=="researcher" then
    local science=stock(npc,"automation-science-pack")
    if science<5 then wants[#wants+1]={item="automation-science-pack",count=5-science,purpose="research"} end
  end
  for _,demand in ipairs(SharedProjects.demands(npc)) do
    for index=#wants,1,-1 do
      if wants[index].item==demand.item then table.remove(wants,index) end
    end
    wants[#wants+1]=demand
  end
  return wants
end

local function own_capacity(npc)
  return {coal=math.max(0,stock(npc,"coal")-12),
    ["iron-plate"]=math.max(0,stock(npc,"iron-plate")-20),
    ["copper-plate"]=math.max(0,stock(npc,"copper-plate")-10)}
end

local function choose_role(npc)
  local options={{"defender",axis(npc,"defense",0.5)},
    {"researcher",axis(npc,"science",0.5)},
    {"hauler",axis(npc,"logistics",0.5)},
    {"power_technician",axis(npc,"power",0.5)},
    {"merchant",axis(npc,"trade",0.5)},
    {"builder",axis(npc,"work_rate",0.5)}}
  table.sort(options,function(a,b) return a[2]>b[2] or a[2]==b[2] and a[1]<b[1] end)
  return options[1][1]
end

function Society.init()
  storage.society={version=1,actors={},contacts={},trust={},relations={},offers={},
    coalitions={},claims={},events={},next_offer=1,next_coalition=1,
    pair_cooldown={},policy_mode="bandit",last_discovery_tick=-300,
    requests={},next_request=1}
end

function Society.ensure(npc)
  local s=state()
  local a=s.actors[npc.id]
  if not a then
    a={id=npc.id,role=choose_role(npc),
      last_decision_tick=game.tick-DECISION_PACE,known={},demands={},reputation=0}
    s.actors[npc.id]=a
  end
  a.role=a.role or "builder"
  a.known=a.known or {};a.demands=a.demands or {};a.capacity=a.capacity or {}
  a.reputation=a.reputation or 0
  return a
end

local function discover(tick,force)
  local s=state()
  if not force and tick-s.last_discovery_tick<300 then return end
  s.last_discovery_tick=tick
  for id=1,10 do
    local a=live(id)
    if a then
      local own=Society.ensure(a)
      own.demands=own_demand(a)
      own.capacity=own_capacity(a)
      Cooperation.refresh(s,a,own.demands)
      local coalition=s.coalitions[own.coalition_id]
      if coalition then coalition.demands[id]=own.demands; coalition.capacity[id]=own.capacity end
      for other_id=id+1,10 do
        local b=live(other_id)
        if b and a.character.surface==b.character.surface then
          local d=distance(a.character.position,b.character.position)
          if d<=CONTACT_RADIUS then
            Society.ensure(b).known[id]={last_seen_tick=tick,distance=d,
              last_seen_position={x=a.character.position.x,y=a.character.position.y},
              surface_index=a.character.surface.index,
              reported_demands=own.demands,reported_capacity=own.capacity}
            own.known[other_id]={last_seen_tick=tick,distance=d,
              last_seen_position={x=b.character.position.x,y=b.character.position.y},
              surface_index=b.character.surface.index,
              reported_demands=own_demand(b),reported_capacity=own_capacity(b)}
          end
        end
      end
    end
  end
end

local function known(npc,other_id)
  local s=storage.society
  local actor=s and s.actors and s.actors[npc.id]
  local seen=actor and actor.known and actor.known[other_id]
  return seen and game.tick-seen.last_seen_tick<=CONTACT_MEMORY and seen or nil
end

local function armed(npc)
  local character=npc.character
  if not character.health or character.health<math.max(50,(character.max_health or 250)*.4) then
    return false
  end
  local guns=character.get_inventory(defines.inventory.character_guns)
  local ammo=character.get_inventory(defines.inventory.character_ammo)
  local main=character.get_main_inventory()
  local has_gun,has_ammo=false,false
  for _,inventory in ipairs({guns,main}) do
    if inventory and inventory.valid then
      for i=1,#inventory do
        local stack=inventory[i]
        if stack.valid_for_read and stack.prototype.type=="gun" then has_gun=true;break end
      end
    end
  end
  for _,inventory in ipairs({ammo,main}) do
    if inventory and inventory.valid then
      for i=1,#inventory do
        local stack=inventory[i]
        if stack.valid_for_read and stack.prototype.type=="ammo"
            and stack.ammo+(stack.count-1)*(stack.prototype.magazine_size or 1)>0 then
          has_ammo=true;break
        end
      end
    end
  end
  return has_gun and has_ammo
end

-- Hostile travel uses a saved observation, never a remote live position or
-- inventory. The other faction's current force is checked only for diplomacy.
function Society.hostile_mission_allowed(npc,other_id,intent)
  local other=live(other_id)
  if not live(npc.id) or not other then
    return false,"hostile_party_unavailable"
  end
  local s=storage.society
  local actor=s and s.actors and s.actors[npc.id] or {}
  if game.tick<(actor.hostile_cooldown_until or 0) and not intent then
    return false,"hostile_cooldown"
  end
  local seen=intent and {last_seen_tick=intent.last_seen_tick,
    last_seen_position=intent.last_seen_position,surface_index=intent.surface_index}
      or known(npc,other_id)
  if not seen or not seen.last_seen_position or not seen.surface_index
      or game.tick-seen.last_seen_tick>CONTACT_MEMORY
      or seen.surface_index~=npc.character.surface.index
      or distance(npc.character.position,seen.last_seen_position)>CONTACT_RADIUS then
    return false,"contact_stale_or_out_of_range"
  end
  local r=saved_relation(npc.id,other_id)
  if r.coalition_id or npc.force.get_friend(other.force)
      or other.force.get_friend(npc.force)
      or npc.force.get_cease_fire(other.force)
      or other.force.get_cease_fire(npc.force)
      or game.tick<math.max(npc.created_tick or 0,other.created_tick or 0,
        r.changed_tick or 0)+RELATION_GRACE then
    return false,"hostility_or_grace_withdrawn"
  end
  if intent and (game.tick>=intent.expires_tick or intent.target_id~=other_id) then
    return false,"hostile_intent_expired"
  end
  if not armed(npc) then return false,"hostile_actor_unarmed_or_hurt" end
  return true,seen
end

local function can_supply(a,b)
  local r=saved_relation(a.id,b.id)
  return force_relation(a,b)~="war" and (r.trade==true or r.coalition_id~=nil)
end

function Society.delivery_allowed(a,b)
  return live(a.id) and live(b.id) and can_supply(a,b) or false
end

local function pending_for(id)
  local out={}
  for _,offer in ipairs(state().offers or {}) do
    if offer.status=="pending" and offer.expires_tick>game.tick
        and offer.parties[id] and not offer.accepted[id] then out[#out+1]=offer end
  end
  table.sort(out,function(a,b) return a.id<b.id end)
  return out
end

local function active_pair_offer(a,b,kind)
  for _,offer in ipairs(state().offers or {}) do
    if offer.status=="pending" and offer.expires_tick>game.tick and offer.parties[a]
        and offer.parties[b] and (not kind or offer.kind==kind) then return true end
  end
  return false
end

local function pair_ready(a,b)
  local s=state()
  local key=pair(a,b)
  local r=saved_relation(a,b)
  return game.tick>=((s.pair_cooldown or {})[key] or 0)
      and game.tick>=(r.changed_tick or 0)+RELATION_GRACE
end

local function offer_parties(a,b,kind)
  local parties={[a.id]=true,[b.id]=true}
  if kind:sub(1,9)=="coalition" then
    local s=state()
    for _,member in ipairs({a,b}) do
      local social=Society.ensure(member)
      local coalition=s.coalitions[social.coalition_id]
      if coalition then for id in pairs(coalition.members) do parties[id]=true end end
    end
  end
  return parties
end

local function create_offer(a,b,kind,scope,token)
  local s=state()
  local offer={id=s.next_offer,proposer=a.id,target=b.id,kind=kind,scope=scope,
    parties=offer_parties(a,b,kind),accepted={[a.id]=true},status="pending",
    created_tick=game.tick,expires_tick=game.tick+OFFER_LIFETIME,
    decision_tokens=token and {[a.id]={token=token,role="proposer"}} or {}}
  s.next_offer=s.next_offer+1
  s.offers[#s.offers+1]=offer
  while #s.offers>MAX_LOG do table.remove(s.offers,1) end
  s.pair_cooldown[pair(a.id,b.id)]=game.tick+PAIR_COOLDOWN
  log_event(a,"offers "..kind.." to "..b.name.."; every participating faction must accept.",
    "offer",{offer_id=offer.id,target=b.id,scope=scope,offer_kind=kind})
  return offer
end

local function coalition_commit(offer)
  local s=state()
  local coalition={id=s.next_coalition,mode=offer.scope,members={},roles={},
    created_tick=game.tick,shared_site=nil,demands={},capacity={}}
  s.next_coalition=s.next_coalition+1
  for id in pairs(offer.parties) do
    local npc=live(id)
    if npc then
      coalition.members[id]=true
      local actor=Society.ensure(npc)
      actor.coalition_id=coalition.id
      coalition.roles[id]=actor.role
    end
  end
  -- A shared base is an agreement to build one, not a free factory or teleport.
  if offer.scope=="shared" then
    local proposer,target=live(offer.proposer),live(offer.target)
    if proposer and target then
      coalition.shared_site={surface_index=proposer.character.surface.index,
        x=math.floor((proposer.home.x+target.home.x)/2),
        y=math.floor((proposer.home.y+target.home.y)/2),status="proposed"}
    end
  end
  s.coalitions[coalition.id]=coalition
  return coalition
end

local function finish_offer(offer)
  for id in pairs(offer.parties) do
    if not offer.accepted[id] then return false,"awaiting_other_faction" end
    if not live(id) then return false,"participant_missing" end
  end
  local a,b=live(offer.proposer),live(offer.target)
  if not a or not b then return false,"participant_missing" end
  local r=relation(a.id,b.id)
  local previous=force_relation(a,b)
  if offer.kind=="alliance" or offer.kind:sub(1,9)=="coalition" then
    local changed={}
    for ida in pairs(offer.parties) do for idb in pairs(offer.parties) do
      if ida<idb then
        local pa,pb=live(ida),live(idb)
        if not pa or not pb then return false,"participant_missing" end
        local fa,fb=pa.force,pb.force
        local old={fa.get_friend(fb),fb.get_friend(fa),fa.get_cease_fire(fb),fb.get_cease_fire(fa)}
        local ok,reason=set_flags(pa,pb,true,true)
        if not ok then
          for _,previous in ipairs(changed) do
            local x,y=previous[1],previous[2]
            pcall(function()
              x.set_friend(y,previous[3]); y.set_friend(x,previous[4])
              x.set_cease_fire(y,previous[5]); y.set_cease_fire(x,previous[6])
            end)
          end
          return false,reason
        end
        changed[#changed+1]={fa,fb,old[1],old[2],old[3],old[4],ida,idb}
      end
    end end
    for _,entry in ipairs(changed) do relation(entry[7],entry[8]).changed_tick=game.tick end
  elseif offer.kind=="peace" then
    local ok,reason=set_flags(a,b,false,true)
    if not ok then return false,reason end
  elseif offer.kind~="trade" then return false,"unknown_offer_kind" end
  if offer.kind=="trade" then r.trade=true end
  if offer.kind:sub(1,9)=="coalition" then
    local coalition=coalition_commit(offer)
    for ida in pairs(offer.parties) do for idb in pairs(offer.parties) do
      if ida<idb then relation(ida,idb).coalition_id=coalition.id end
    end end
  end
  r.changed_tick=game.tick
  offer.status="accepted"; offer.resolved_tick=game.tick
  log_event(a,offer.kind.." with "..b.name.." took effect after independent acceptance.",
    "treaty",{offer_id=offer.id,previous=previous,current=force_relation(a,b),target=b.id,offer_kind=offer.kind})
  return true
end

function Society.observe(npc)
  if not live(npc.id) then return nil,"actor_missing" end
  local s=storage.society
  if not s then return nil,"society_uninitialized" end
  local actor=(s.actors or {})[npc.id]
      or {role="builder",reputation=0,known={},demands={},capacity={}}
  local contacts={}
  for id,seen in pairs(actor.known or {}) do
    local other=live(id)
    if other and game.tick-seen.last_seen_tick<=CONTACT_MEMORY then
      local r=saved_relation(npc.id,id)
      contacts[#contacts+1]={id=id,name=other.name,last_seen_tick=seen.last_seen_tick,
        last_seen_distance=seen.distance,trust=saved_trust(npc.id,id),
        last_seen_position=seen.last_seen_position,
        surface_index=seen.surface_index,
        force_relation=force_relation(npc,other),trade=r.trade==true,
        coalition_id=r.coalition_id,reported_demands=seen.reported_demands,
        reported_capacity=seen.reported_capacity}
    end
  end
  table.sort(contacts,function(a,b) return a.id<b.id end)
  local offers={}
  for _,o in ipairs(pending_for(npc.id)) do
    offers[#offers+1]={id=o.id,kind=o.kind,proposer=o.proposer,target=o.target,
      scope=o.scope,expires_tick=o.expires_tick}
  end
  local coalition=(s.coalitions or {})[actor.coalition_id]
  local shared_site
  if coalition and coalition.shared_site then
    local site=coalition.shared_site
    shared_site={surface_index=site.surface_index,x=site.x,y=site.y,
      status=site.status,completed_tick=site.completed_tick,nodes={}}
    for id,node in pairs(site.nodes or {}) do
      shared_site.nodes[id]={status=node.status,position=node.position,
        completed_tick=node.completed_tick,last_failure=node.last_failure}
    end
  end
  local requests={}
  for _,request in ipairs(s.requests or {}) do
    if request.requester_id==npc.id and
        (request.status=="open" or request.status=="accepted") then
      requests[#requests+1]={id=request.id,item=request.item,count=request.count,
        status=request.status,supplier_id=request.supplier_id,
        accepted_count=request.accepted_count,expires_tick=request.expires_tick,
        purpose=request.purpose}
    end
  end
  return {tick=game.tick,actor_id=npc.id,role=actor.role or "builder",
    reputation=actor.reputation or 0,
    contacts=contacts,incoming_offers=offers,own_demands=actor.demands or {},
    own_capacity=actor.capacity or {},own_requests=requests,
    own_stock={coal=stock(npc,"coal"),["iron-plate"]=stock(npc,"iron-plate"),
      ["copper-plate"]=stock(npc,"copper-plate")},
    coalition=coalition and {id=coalition.id,mode=coalition.mode,roles=coalition.roles,
      demands=coalition.demands,capacity=coalition.capacity,
      shared_site=shared_site} or nil}
end

local function add(candidates,id,action,target,description,extra)
  local c={id=id,action=action,target_id=target,description=description}
  for k,v in pairs(extra or {}) do c[k]=v end
  candidates[#candidates+1]=c
end

local function nearby_resource(npc)
  local entities=npc.character.surface.find_entities_filtered{position=npc.character.position,
    radius=48,type="resource",limit=32}
  table.sort(entities,function(a,b)
    local da=distance(a.position,npc.character.position)
    local db=distance(b.position,npc.character.position)
    return da<db or (da==db and a.position.x<b.position.x)
  end)
  for _,e in ipairs(entities) do
    if e.valid and e.force.name=="neutral" and
        (e.name=="coal" or e.name=="iron-ore" or e.name=="copper-ore" or e.name=="stone") then
      local cell=e.name..":"..math.floor(e.position.x/16)..":"..math.floor(e.position.y/16)
      local claim=(state().claims or {})[cell]
      if not claim or claim.expires_tick<=game.tick then return cell,e end
    end
  end
end

function Society.candidates(npc)
  local view=Society.observe(npc)
  if not view then return {} end
  local candidates={}
  for _,offer in ipairs(view.incoming_offers) do
    add(candidates,"accept:"..offer.id,"accept",offer.proposer,
      "Accept "..offer.kind.." offer #"..offer.id.." after this faction's own decision.",
      {offer_id=offer.id})
    add(candidates,"reject:"..offer.id,"reject",offer.proposer,
      "Decline "..offer.kind.." offer #"..offer.id..".",{offer_id=offer.id})
  end
  for _,contact in ipairs(view.contacts) do
    local b=live(contact.id)
    if b then
      local relation_status=contact.force_relation
      if pair_ready(npc.id,b.id) and not active_pair_offer(npc.id,b.id) then
        if relation_status=="war" then
          add(candidates,"offer-peace:"..b.id,"offer_peace",b.id,
            "Propose reciprocal cease-fire to "..b.name..".")
        else
          if not contact.trade then
            add(candidates,"offer-trade:"..b.id,"offer_trade",b.id,
              "Propose trade access with "..b.name.."; forces remain separate.")
          end
          if relation_status~="allied" then
            add(candidates,"offer-alliance:"..b.id,"offer_alliance",b.id,
              "Propose alliance without automatic trade or merged research to "..b.name..".")
          end
          if relation_status=="allied" and not contact.coalition_id then
            add(candidates,"coalition-separate:"..b.id,"offer_coalition_separate",b.id,
              "Propose a coalition with separate bases and assigned roles.")
            add(candidates,"coalition-shared:"..b.id,"offer_coalition_shared",b.id,
              "Propose a shared-base coalition; construction still requires physical work.")
          end
          if not contact.coalition_id
              and game.tick>=math.max(npc.created_tick or 0,b.created_tick or 0,
                saved_relation(npc.id,b.id).changed_tick or 0)+RELATION_GRACE then
            add(candidates,"war:"..b.id,"declare_war",b.id,
              "Unilaterally end peace with "..b.name.."; reciprocal force flags become hostile.")
          end
        end
      end
      if can_supply(npc,b) then
        for _,request in ipairs(Cooperation.available(state(),npc,b.id,true)) do
          local amount=math.min(request.remaining,Cooperation.capacity(npc,request.item,state()))
          add(candidates,"supply:"..b.id..":"..request.item,"supply",b.id,
            "Accept request #"..request.id.." and hand-deliver "..amount.." "..request.item..
              " from carried stock to "..b.name..".",
            {item=request.item,count=amount,request_id=request.id,purpose=request.purpose})
        end
      end
      local hostile_ready=Society.hostile_mission_allowed(npc,b.id)
      if hostile_ready then
        add(candidates,"theft:"..b.id,"theft",b.id,
          "Travel to remembered contact with "..b.name.." and attempt a local physical theft.")
        add(candidates,"sabotage:"..b.id,"sabotage",b.id,
          "Travel to remembered contact with "..b.name.." and attempt local fuel sabotage.")
      end
    end
  end
  local cell,resource=nearby_resource(npc)
  if cell then
    add(candidates,"claim:"..cell,"claim_resource",nil,
      "Announce a temporary claim to a nearby "..resource.name.." patch.",
      {resource_cell=cell,resource_name=resource.name,position={x=resource.position.x,y=resource.position.y}})
  end
  if stock(npc,"coal")<4 then
    add(candidates,"belief:coal","belief_claim",nil,
      "State this faction's belief about near-term coal supply; a claim is not verified world truth.",
      {item="coal",claimed_count=12})
  end
  add(candidates,"idle","idle",nil,"Make no society change this cycle.")
  if npc.warlord then   -- at war with everyone by its own choice: no peace, alliance, trade or truce
    local PEACEFUL={offer_peace=true,offer_alliance=true,offer_trade=true,offer_coalition_separate=true,
      offer_coalition_shared=true,accept=true,supply=true}
    for i=#candidates,1,-1 do
      if PEACEFUL[candidates[i].action] then table.remove(candidates,i) end
    end
  end
  return candidates
end

local function find_offer(id)
  for _,offer in ipairs(state().offers) do if offer.id==id then return offer end end
end

local function settle_offer(offer,outcome)
  for id,entry in pairs(offer.decision_tokens or {}) do
    local npc=live(id)
    if npc and entry.token then
      local reward=outcome=="accepted" and (entry.role=="proposer" and 1 or .5)
          or outcome=="expired" and -.2 or -.4
      Strategy.feedback(npc,entry.token,reward,"offer_"..outcome)
    end
  end
  offer.decision_tokens={}
end

function Society.apply(npc,decision,token)
  if not live(npc.id) then return false,"actor_missing" end
  if TeamWorld.active() then return false,"one_team" end   -- no offers, treaties, claims or wars on one team
  if type(decision)~="table" or type(decision.id)~="string" then return false,"invalid_decision" end
  local choice
  for _,candidate in ipairs(Society.candidates(npc)) do
    if candidate.id==decision.id then choice=candidate; break end
  end
  if not choice then return false,"candidate_not_legal" end
  local action=choice.action
  local b=choice.target_id and live(choice.target_id) or nil
  if action=="idle" then
    if token then Strategy.close(npc,token,"idle_no_observed_outcome") end
    return true,{kind="idle"}
  end
  if action=="accept" or action=="reject" then
    local offer=find_offer(choice.offer_id)
    if not offer or offer.status~="pending" or not offer.parties[npc.id]
        or offer.accepted[npc.id] or offer.expires_tick<=game.tick then return false,"offer_not_pending" end
    if action=="reject" then
      offer.status="rejected"; offer.rejected_by=npc.id; offer.resolved_tick=game.tick
      settle_offer(offer,"rejected")
      if token then Strategy.close(npc,token,"rejected_offer_no_later_outcome") end
      log_event(npc,"declines offer #"..offer.id.." ("..offer.kind..").","rejection",
        {offer_id=offer.id,proposer=offer.proposer,offer_kind=offer.kind})
      return true,{kind="offer_rejected",offer_id=offer.id}
    end
    offer.accepted[npc.id]=true
    if token then
      offer.decision_tokens=offer.decision_tokens or {}
      offer.decision_tokens[npc.id]={token=token,role="acceptor"}
    end
    log_event(npc,"accepts offer #"..offer.id.." ("..offer.kind..").","acceptance",{offer_id=offer.id})
    local committed,reason=finish_offer(offer)
    if committed then
      settle_offer(offer,"accepted")
      return true,{kind="treaty",offer_id=offer.id}
    end
    if reason~="awaiting_other_faction" then
      offer.status="failed"; offer.reason=reason
      settle_offer(offer,"failed")
      return false,reason
    end
    return true,{kind="offer_accepted_pending",offer_id=offer.id}
  end
  if action:sub(1,6)=="offer_" then
    if not b then return false,"target_missing" end
    local kind,scope
    if action=="offer_trade" then kind="trade"
    elseif action=="offer_peace" then kind="peace"
    elseif action=="offer_alliance" then kind="alliance"
    elseif action=="offer_coalition_separate" then kind,scope="coalition","separate"
    elseif action=="offer_coalition_shared" then kind,scope="coalition","shared"
    else return false,"unknown_offer_action" end
    local offer=create_offer(npc,b,kind,scope,token)
    return true,{kind="offer_created",offer_id=offer.id}
  end
  if action=="declare_war" then
    if not b then return false,"target_missing" end
    local ok,reason=set_flags(npc,b,false,false)
    if not ok then return false,reason end
    local r=relation(npc.id,b.id)
    r.changed_tick=game.tick; r.trade=false; r.coalition_id=nil
    if token then Strategy.close(npc,token,"war_declared_outcome_unobserved") end
    log_event(npc,"declares war on "..b.name.."; both force directions are hostile.","war",{target=b.id})
    return true,{kind="war",target_id=b.id}
  end
  if action=="claim_resource" then
    if not choice.resource_cell then return false,"resource_missing" end
    local s=state()
    s.claims[choice.resource_cell]={owner=npc.id,resource=choice.resource_name,
      position=choice.position,created_tick=game.tick,expires_tick=game.tick+60*60*5}
    local count=0; for _ in pairs(s.claims) do count=count+1 end
    if count>MAX_CLAIMS then
      local oldest_key,oldest_tick
      for key,claim in pairs(s.claims) do
        if not oldest_tick or claim.created_tick<oldest_tick then oldest_key,oldest_tick=key,claim.created_tick end
      end
      if oldest_key then s.claims[oldest_key]=nil end
    end
    log_event(npc,"claims a nearby "..choice.resource_name.." patch for five minutes; mining rights remain physical.",
      "claim",{cell=choice.resource_cell})
    if token then Strategy.close(npc,token,"claim_announced_outcome_unobserved") end
    return true,{kind="claim",cell=choice.resource_cell}
  end
  if action=="belief_claim" then
    local actual=stock(npc,choice.item)
    local claim={actor=npc.id,item=choice.item,claimed_count=choice.claimed_count,
      observed_carried_at_claim=actual,tick=game.tick,source="npc_belief"}
    Society.ensure(npc).last_belief_claim=claim
    log_event(npc,"claims it believes it can supply "..choice.claimed_count.." "..choice.item..
      "; this is its claim, not a verified stock statement.","belief_claim",claim)
    if token then Strategy.close(npc,token,"belief_claim_outcome_unobserved") end
    return true,{kind="belief_claim",claim=claim}
  end
  if action=="supply" or action=="theft" or action=="sabotage" then
    if not b then return false,"target_missing" end
    local actor=Society.ensure(npc)
    if actor.pending_intent or npc.social_job then return false,"physical_intent_pending" end
    local seen
    if action=="theft" or action=="sabotage" then
      local ready,observation=Society.hostile_mission_allowed(npc,b.id)
      if not ready then return false,observation end
      seen=observation
      actor.hostile_cooldown_until=game.tick+60*60*5
    end
    local request
    if action=="supply" then
      request=Cooperation.accept(state(),choice.request_id,npc,b.id,can_supply(npc,b))
      if not request then return false,"request_not_available" end
      local coalition=state().coalitions[actor.coalition_id]
      request.coalition_id=coalition and coalition.members[b.id] and coalition.id or nil
      request.coalition_mode=request.coalition_id and coalition.mode or nil
      request.supplier_role=actor.role
      request.requester_role=Society.ensure(b).role
    end
    local intent={kind=action,actor_id=npc.id,target_id=b.id,item=choice.item,
      count=request and request.accepted_count or choice.count,
      request_id=request and request.id or nil,
      created_tick=game.tick,requires_worker_receipt=true,
      last_seen_position=seen and {x=seen.last_seen_position.x,
        y=seen.last_seen_position.y} or nil,
      last_seen_tick=seen and seen.last_seen_tick or nil,
      surface_index=seen and seen.surface_index or nil,
      expires_tick=seen and math.min(game.tick+36000,
        seen.last_seen_tick+CONTACT_MEMORY) or nil}
    actor.pending_intent={intent=intent,token=token}
    log_event(npc,"commits to a "..action.." attempt involving "..b.name..
      "; physical outcome is pending Worker verification.","physical_intent",intent)
    return true,intent
  end
  return false,"unknown_action"
end

function Society.on_worker_receipt(npc,intent,receipt)
  if type(intent)~="table" or intent.actor_id~=npc.id or not receipt
      or type(receipt.status)~="string" then return false,"invalid_social_receipt" end
  if intent.kind~="supply" and intent.kind~="theft" and intent.kind~="sabotage" then
    return false,"unknown_social_intent"
  end
  local other=live(intent.target_id)
  if not other then return false,"target_missing" end
  local contract
  if intent.kind=="supply" then
    contract=Cooperation.receipt(state(),intent,receipt)
    if not contract then return false,"contract_receipt_mismatch" end
  end
  if receipt.status~="complete" then
    if intent.kind=="supply" then
      Cooperation.remember_route_failure(state(),npc,other,receipt.reason,contract)
    end
    log_event(npc,intent.kind.." attempt involving "..other.name.." failed: "..
      tostring(receipt.reason or "worker_failed")..".","physical_failure",
      {intent=intent,job_id=receipt.job_id,count=receipt.count or 0})
    return true,{kind="physical_failure"}
  end
  local actor=Society.ensure(npc)
  if intent.kind=="supply" then
    actor.reputation=clamp(actor.reputation+2,-100,100)
    log_event(npc,"completed a physical delivery to "..other.name.." ("..
      tostring(receipt.count or 0).." "..tostring(intent.item)..").","trade_delivery",
      {intent=intent,job_id=receipt.job_id,count=receipt.count,request_id=intent.request_id})
  else
    actor.reputation=clamp(actor.reputation+(intent.kind=="theft" and -8 or -15),-100,100)
    log_event(npc,"completed a physical "..intent.kind.." against "..other.name..".",
      "hostile_action",{intent=intent,job_id=receipt.job_id})
  end
  return true,{kind=intent.kind,job_id=receipt.job_id}
end

function Society.teacher(npc,candidates)
  local cooperation=profile(npc,"cooperation",0.5)
  local risk=profile(npc,"risk_tolerance",0.5)
  local trade_drive=axis(npc,"trade",0.5)
  local aggression=axis(npc,"aggression",0.5)
  local territoriality=axis(npc,"territoriality",0.5)
  local honesty=axis(npc,"honesty",0.5)
  for _,c in ipairs(candidates) do
    if c.action=="accept" then
      local offer=find_offer(c.offer_id)
      local proposer=offer and live(offer.proposer)
      local score=cooperation*100+trust(npc.id,c.target_id or (proposer and proposer.id or npc.id))
      local threshold=offer and offer.kind=="alliance" and 48 or 28
      if offer and offer.kind=="coalition" then threshold=offer.scope=="shared" and 60 or 42 end
      if score>=threshold then return c end
    end
  end
  for _,c in ipairs(candidates) do
    if c.action=="reject" then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="offer_trade" and cooperation>=0.35 and trade_drive>=0.25 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="offer_alliance" and cooperation>=0.60 and trust(npc.id,c.target_id)>=0 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="offer_coalition_separate" and cooperation>=0.55 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="offer_coalition_shared" and cooperation>=0.8 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="declare_war" and aggression>=0.75 and territoriality>=0.60
        and trust(npc.id,c.target_id)<=0 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="supply" and cooperation>=0.45 and trade_drive>=0.3 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="belief_claim" and honesty<0.25 and risk>=0.65 then return c end
  end
  for _,c in ipairs(candidates) do
    if c.action=="claim_resource" and risk>=0.30 then return c end
  end
  return candidates[#candidates]
end

function Society.tick(tick)
  local s=state()
  -- Migrate sparse historical state on the synchronized tick, never on load
  -- or during a status/candidate read.
  s.actors=s.actors or {};s.contacts=s.contacts or {};s.trust=s.trust or {}
  s.relations=s.relations or {};s.offers=s.offers or {};s.coalitions=s.coalitions or {}
  s.claims=s.claims or {};s.pair_cooldown=s.pair_cooldown or {}
  s.last_discovery_tick=s.last_discovery_tick or -300
  discover(tick)
  -- Force relations can also change outside Society.apply. Record those
  -- changes on the scheduled simulation tick, never during an observation.
  for id=1,10 do
    local actor=s.actors[id]
    if actor and actor.known then
      for other_id=id+1,10 do
        local other=s.actors[other_id]
        if (actor.known[other_id] or other and other.known and other.known[id])
            and live(id) and live(other_id) then relation(id,other_id) end
      end
    end
  end
  for _,offer in ipairs(s.offers) do
    if offer.status=="pending" and tick>=offer.expires_tick then
      offer.status="expired"; offer.resolved_tick=tick
      settle_offer(offer,"expired")
    end
  end
  for key,claim in pairs(s.claims) do if tick>=claim.expires_tick then s.claims[key]=nil end end
  Cooperation.expire(s,tick)
  if TeamWorld.active() then return end   -- one team: nobody weighs up deals or wars with teammates
  if (s.policy_mode~="teacher" and s.policy_mode~="bandit") or tick%60~=0 then return end
  for id=1,10 do
    local npc=live(id)
    if npc and (npc.step or 0)>8 then
      local actor=Society.ensure(npc)
      if tick-actor.last_decision_tick>=DECISION_PACE
          and not actor.pending_intent and not npc.social_job then
        actor.last_decision_tick=tick
        local candidates=Society.candidates(npc)
        local choice,token
        if s.policy_mode=="teacher" then choice=Society.teacher(npc,candidates)
        else choice,token=Strategy.choose(npc,candidates,Society.observe(npc)) end
        if choice and (s.policy_mode=="bandit" or (choice.action~="idle"
            and choice.action~="supply" and choice.action~="theft"
            and choice.action~="sabotage")) then
          local ok,reason=Society.apply(npc,{id=choice.id},token)
          if not ok and token then Strategy.feedback(npc,token,-.4,
            "apply_failed:"..tostring(reason)) end
        end
      end
    end
  end
end

-- War declared by an engineer's own decision (its strategist; perhaps talked into it by a player it
-- joined): both force directions hostile, remembered and announced like any declaration.
function Society.declare_war(npc,other)
  if not other then return false,"target_missing" end
  if TeamWorld.active() then return false,"one_team" end
  local ok,reason=set_flags(npc,other,false,false)
  if not ok then return false,reason end
  local r=relation(npc.id,other.id)
  r.changed_tick=game.tick; r.trade=false; r.coalition_id=nil
  log_event(npc,"declares war on "..other.name.."; both force directions are hostile.","war",{target=other.id})
  return true
end

return Society
