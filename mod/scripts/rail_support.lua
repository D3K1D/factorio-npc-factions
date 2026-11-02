-- Factorio 2.0 rail planners can place several entities at different item costs.
-- These helpers validate placement; they never create entities or move items.
local Rail = {}
local TYPES = { ["straight-rail"]=true, ["half-diagonal-rail"]=true,
  ["curved-rail-a"]=true, ["curved-rail-b"]=true,
  ["train-stop"]=true, ["rail-signal"]=true, ["rail-chain-signal"]=true,
  locomotive=true, ["cargo-wagon"]=true, ["fluid-wagon"]=true }
local ROLLING = {locomotive=true,["cargo-wagon"]=true,["fluid-wagon"]=true}
local TRACK = {"straight-rail","half-diagonal-rail","curved-rail-a","curved-rail-b"}

function Rail.supports(entity_name)
  local entity=prototypes.entity[entity_name or ""]
  return entity and TYPES[entity.type] == true or false
end

-- Fresh native rolling stock has an item_number too, so it is not a metadata
-- discriminator. This strict whitelist matches qualified 2.0.77 default data.
-- Unsupported/customized data stays on the item for future native adapters.
function Rail.plain_item_stack(stack)
  if not stack or not stack.valid_for_read or stack.quality.name~="normal" or stack.health~=1 then return false end
  if not stack.is_item_with_entity_data then
    return not stack.is_item_with_tags and not stack.is_item_with_inventory and not stack.is_item_with_label
  end
  local prototype=stack.prototype.place_result
  if not prototype or not ROLLING[prototype.type] then return false end
  local okay,plain=pcall(function()
    if stack.entity_label~=nil or stack.entity_color~=nil then return false end
    local grid=stack.grid
    if grid and #grid.equipment>0 then return false end
    if stack.entity_logistics_enabled~=true or stack.entity_request_from_buffers~=true
        or stack.entity_enable_logistics_while_moving~=true or stack.entity_driver_is_gunner~=false
        or stack.entity_auto_target_with_gunner~=false or stack.entity_auto_target_without_gunner~=true then return false end
    local logistics=stack.entity_logistic_sections
    if logistics and logistics.trash_not_requested then return false end
    local sections=logistics and logistics.sections or {}
    if #sections>1 then return false end
    for _,section in ipairs(sections) do
      if section.index~=1 or (section.group and section.group~="") or section.active==false
          or (section.multiplier and section.multiplier~=1) or #(section.filters or {})>0 then return false end
    end
    return true
  end)
  return okay and plain or false
end

-- Returns a plain saved-safe {name,count,quality} descriptor, or nil/reason.
-- count comes from the target entity, not the planner's single place_result.
function Rail.item_for_entity(npc,entity_name)
  local entity=prototypes.entity[entity_name or ""]
  if not entity or not TYPES[entity.type] then return nil,"unsupported_rail_entity" end
  for _,entry in ipairs(entity.items_to_place_this or {}) do
    local item=prototypes.item[entry.name]
    local allowed=item and item.place_result and item.place_result.name==entity_name
    if item and item.type=="rail-planner" then
      allowed=false
      for _,rail in ipairs(item.rails or {}) do
        if rail.name==entity_name then allowed=true;break end
      end
    end
    if allowed and type(entry.count)=="number" and entry.count>=1 then
      return {name=entry.name,count=entry.count,quality="normal"}
    end
  end
  return nil,"unsupported_rail_placement_item"
end

-- Run after normal walking brings the body within build distance. This limits
-- rolling stock to the actor's tracks; native placement checks are authoritative.
function Rail.valid_owned_placement(npc,placement)
  local c=npc and npc.character
  if not c or not c.valid then return false,"body_missing" end
  if type(placement)~="table" or not placement.position then return false,"invalid_build" end
  local item,reason=Rail.item_for_entity(npc,placement.name)
  if not item then return false,reason end
  if placement.force and (type(placement.force)=="string" and placement.force or placement.force.name)~=c.force.name then
    return false,"rail_owner_mismatch"
  end
  local check={name=placement.name,position=placement.position,
    direction=placement.direction or defines.direction.north,
    force=c.force,build_check_type=defines.build_check_type.manual}
  if not c.can_place_entity(check) then return false,"placement_blocked_or_out_of_reach" end
  if not c.surface.can_place_entity(check) then return false,"placement_collision" end
  local entity=prototypes.entity[placement.name]
  if ROLLING[entity.type] then
    local p=placement.position;local x,y=p.x or p[1],p.y or p[2]
    local found=false
    for _,rail in ipairs(c.surface.find_entities_filtered{area={{x-3,y-3},{x+3,y+3}},type=TRACK}) do
      if rail.force~=c.force then return false,"foreign_track_requires_carrier_force" end
      found=true
    end
    if not found then return false,"owned_track_missing" end
  end
  return true,item
end

return Rail
