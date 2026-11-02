-- Recipes by what they make. Mods name recipes as they like (Bob's Warfare makes the empty magazine
-- with "empty-recipe"), so an item's recipe is found from the recipes' products, never from its name.
local Recipes={}

-- item -> names of the recipes that make it, best first. Read from the prototypes, which are the
-- same on every machine and never change while the game runs: a cache of game data, not game state.
local cache={}

local function try(f) local ok,value=pcall(f); if ok then return value end end

local function amount(product)
  local n=product.amount or ((product.amount_min or 0)+(product.amount_max or product.amount_min or 0))/2
  return n*(product.independent_probability or product.probability or 1)   -- (2.1: independent_probability)
end

-- How many of `item` one craft of `recipe` adds (less what it uses of it: a loop nets less).
function Recipes.yield(recipe,item)
  local made=0
  for _,p in ipairs(recipe.products or {}) do
    if p.name==item and p.type~="fluid" then made=made+amount(p) end
  end
  for _,i in ipairs(recipe.ingredients or {}) do
    if i.name==item and i.type~="fluid" then made=made-(i.amount or 0) end
  end
  return made
end

-- A recipe's crafting categories. Factorio 2.1 gives a list (a hand-craftable recipe also names
-- "hand-crafting"); a table without one, as in the unit tests, has its single category.
function Recipes.categories(recipe)
  return recipe.categories or {recipe.category}
end

-- Whether something with these crafting categories ({name=true}: a machine's, a character's) makes it.
function Recipes.made_in(recipe,set)
  if not (recipe and set) then return false end
  for _,c in ipairs(Recipes.categories(recipe)) do if set[c] then return true end end
  return false
end

-- The category a machine crafts it in (for the companion's ratios): the first that is not hand crafting.
function Recipes.category(recipe)
  local list=Recipes.categories(recipe)
  for _,c in ipairs(list) do if c~="hand-crafting" then return c end end
  return list[1]
end

-- A parameter recipe (a placeholder for blueprints): 2.1 marks its prototype `parameter`.
local function parameter(recipe)
  return try(function() return (recipe.object_name=="LuaRecipe" and recipe.prototype or recipe).parameter end)
end

-- Recycling, barrel and parameter recipes are no way to make a thing: they are skipped.
local function usable(recipe,item)
  return not recipe.hidden and not parameter(recipe) and not Recipes.made_in(recipe,{recycling=true})
    and Recipes.yield(recipe,item)>0
end

local function main_product(recipe,item)
  local main=try(function() return recipe.main_product end)
  if main then return main.name==item end
  return #(recipe.products or {})==1
end

-- Best first: the recipe named after the item, then one whose main product it is, then the
-- fewest ingredients (a by-product of oil cracking is not how to get it), then by name.
local function rank(list,item)
  table.sort(list,function(a,b)
    local an,bn=a.name==item,b.name==item
    if an~=bn then return an end
    local am,bm=main_product(a,item),main_product(b,item)
    if am~=bm then return am end
    local ai,bi=#(a.ingredients or {}),#(b.ingredients or {})
    if ai~=bi then return ai<bi end
    return a.name<b.name
  end)
  local names={}
  for i,r in ipairs(list) do names[i]=r.name end
  return names
end

local function from_prototypes(item)
  local found=try(function()
    return prototypes.get_recipe_filtered{{filter="has-product-item",elem_filters={{filter="name",name=item}}}}
  end)
  if not found then return nil end
  local list={}
  for _,r in pairs(found) do if usable(r,item) then list[#list+1]=r end end
  return rank(list,item)
end

-- (Without the prototype filter, as in the unit tests, the force's own recipes are read.)
local function from_force(force,item)
  local list={}
  for name,r in pairs(force.recipes or {}) do
    if usable(r,item) then list[#list+1]={name=name,hidden=r.hidden,categories=Recipes.categories(r),
      products=r.products,ingredients=r.ingredients,main_product=try(function() return r.main_product end)} end
  end
  return rank(list,item)
end

function Recipes.candidates(force,item)
  local names=cache[item]
  if names then return names end
  names=from_prototypes(item)
  if names then cache[item]=names return names end
  return from_force(force,item)
end

-- The force's best unlocked recipe for `item`. Given the crafting categories a character has,
-- one it can craft by hand comes first; else any unlocked one (made by machines). With
-- `include_locked`, a recipe not researched yet is the last resort (for budgets of future work).
function Recipes.for_item(force,item,hand,include_locked)
  local best,best_score
  for _,name in ipairs(Recipes.candidates(force,item)) do
    local r=force.recipes[name]
    if r and (r.enabled or include_locked) then
      local score=(r.enabled and 0 or 2)+((not hand or Recipes.made_in(r,hand)) and 0 or 1)
      if score==0 then return r end
      if not best or score<best_score then best,best_score=r,score end
    end
  end
  return best
end

-- Can the force make `item` at all yet?
function Recipes.ready(force,item)
  return Recipes.for_item(force,item)~=nil
end

-- Can the force make something that places the entity `name`?
function Recipes.ready_entity(force,name)
  local p=prototypes.entity[name]
  for _,item in ipairs(p and p.items_to_place_this or {}) do
    if Recipes.ready(force,item.name or item[1]) then return true end
  end
  return false
end

function Recipes.hand(character)
  return try(function() return character.prototype.crafting_categories end) or {}
end

-- The hand crafts, deepest first, that turn what is held (`have(item)`) into `n` of `item`, or nil
-- when that takes something not held (ore, plates it has not got) or a recipe not craftable by hand.
-- Steps for the same thing may repeat; the first one is the craft to start now.
function Recipes.hand_steps(force,hand,have,item,n)
  local held,steps={},{}
  local function get(it) if held[it]==nil then held[it]=have(it) end return held[it] end
  local function make(it,need,depth)
    local h=get(it)
    if h>=need then held[it]=h-need return true end
    if depth>4 then return false end
    local r=Recipes.for_item(force,it,hand)
    if not (r and Recipes.made_in(r,hand)) then return false end
    local per=Recipes.yield(r,it)
    local batches=math.ceil((need-h)/per)
    for _,ingredient in ipairs(r.ingredients or {}) do
      if ingredient.type=="fluid" or not make(ingredient.name,ingredient.amount*batches,depth+1) then return false end
    end
    held[it]=h+batches*per-need
    steps[#steps+1]={recipe=r.name,count=batches}
    return true
  end
  held[item]=0   -- (n more than it holds)
  if make(item,n,0) then return steps end
end

return Recipes
