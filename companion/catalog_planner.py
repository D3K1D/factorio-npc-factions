"""Choose a layout for an NPC: reuse a saved blueprint or ask the designer.

1. The library lists saved blueprints for this purpose that fit the site and
   are affordable.
2. System One (OpenAI Decisions API) picks one of them or "design new", given
   the NPC's personality and each blueprint's track record. Its probabilities
   are sampled, so curious NPCs try new designs more often than cautious ones.
   Without an API key the best-scoring blueprint is used.
3. The designer LLM is only called for "design new" or when nothing fits; its
   plan is saved to the library. Without a designer the deterministic planner
   is used and its plan is saved too.
4. Outcomes reported by the game (built and producing, or blocked) update the
   blueprint's record, so good layouts win more often over time.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from companion import blueprints as bp
from companion import build_choice, build_history, layout_planner
from companion import ledger, plate_lead, priorities, rates, ratios, team_area
from companion.plan_keeper import PlanKeeper

DESIGN_NEW = "design_new"
CONTINUE_PROJECTS = "continue_projects"

# What an NPC can choose to build next. Each purpose names the resource it
# needs nearby, how the deterministic planner builds it, and the designer goal.
# Without System One, the first purpose not built yet is chosen, in this order.
# (underground belts once researched: until then its belts go round what is in the way, smelting.undergrounds)
ELECTRIC_SMELTING = ["electric-mining-drill", "transport-belt", "inserter", "long-handed-inserter",
                     "small-electric-pole", "stone-furnace", "iron-chest"]
ELECTRIC_SUPPLY = ["electric-mining-drill", "transport-belt", "inserter", "small-electric-pole", "iron-chest"]
PURPOSES = {
    # What the robot network uses up, made for it (Ledger's L-5, companion/robo_grid.supply_plan): repair packs.
    "robot-supply": {"robot_supply": True, "requires_recipes": ["requester-chest", "passive-provider-chest"],
        "label": "Robot supply: a maker cell in the robot network for what its robots use up (repair packs): its "
                 "inputs asked of the network, its output into a provider chest its robots take from.",
        "goal": "Keep the robot network stocked with repair packs so its robots repair the base."},
    # The robot network (Ledger's L-3, companion/robo_grid.py): roboports over the base's parts, grown as they spread.
    "robot-network": {"robot_network": True, "requires_recipes": ["roboport", "construction-robot"],
        "label": "Robot network: roboports over the base, their areas overlapping, powered from its poles, with "
                 "construction robots and a storage chest; grown wherever the base spreads past their reach.",
        "goal": "Cover the base with a roboport network so robots build, repair and take down for it."},
    # An ally asked for help: turrets by their base.
    "ally-defense": {"ally_help": True,
        "requires_recipes": ["gun-turret", "firearm-magazine"],
        "label": "Help an ally: gun turrets and a wall by an allied player's base, facing the nearest nests, "
                 "kept loaded with the engineer's magazines.",
        "goal": "Defend the base of the ally who asked for help."},
    # First: when the engineer's buildings report low power, more power comes before anything else.
    # Nuclear first once it is researched and uranium is known (companion/nuclear_plant.py: two reactors, sized from
    # the game's numbers); the steam plant carries the base while the reactors come up. One plant at a time.
    "nuclear-power": {"nuclear": True, "near_resource": "uranium-ore",
        "requires_recipes": ["nuclear-reactor", "heat-exchanger", "heat-pipe", "steam-turbine", "offshore-pump",
                             "small-electric-pole", "inserter", "iron-chest", "pipe", "pipe-to-ground",
                             "uranium-processing", "uranium-fuel-cell"],
        "label": "Nuclear power: the engineer's buildings report low power; two reactors by the nearest shore, heat "
                 "exchangers and turbines at the game's ratio, fuel cells handed in by inserter.",
        "goal": "Build a nuclear power plant: two reactors side by side, heat exchangers and steam turbines at "
                "their ratio, water from offshore pumps, fuel cells inserted and spent cells taken out."},
    # Its fuel, right after it (companion/uranium_plant.py): uranium drills on acid, the library's fuel cell and acid
    # blocks, the cells belted to the reactors' fuel chests. Once.
    "nuclear-fuel": {"nuclear_fuel": True, "near_resource": "uranium-ore",
        "requires_built": ["nuclear-power", "oil-processing"],   # (its acid's sulfur by belt from the oil plant)
        "requires_recipes": ["electric-mining-drill", "centrifuge", "uranium-processing", "uranium-fuel-cell",
                             "chemical-plant", "sulfuric-acid", "transport-belt", "inserter", "iron-chest", "pipe",
                             "pipe-to-ground", "offshore-pump", "small-electric-pole", "splitter"],
        "label": "Nuclear fuel: uranium drills on sulfuric acid, centrifuges and a fuel cell assembler, the cells "
                 "belted to the reactors' fuel chests.",
        "goal": "Fuel the nuclear plant: electric drills mining uranium ore with sulfuric acid, a sulfuric acid "
                "plant, centrifuges processing the ore and an assembler making fuel cells, belted to the reactors."},
    "power-plant": {"power_plant": True,
        "requires_recipes": ["boiler", "steam-engine", "offshore-pump", "pipe", "small-electric-pole",
                             "burner-inserter", "electric-mining-drill"],
        "label": "More power: the engineer's buildings report low power; a steam plant by the nearest shore, "
                 "sized for the whole base at full work, fed coal by belt.",
        "goal": "Build a power plant big enough for the base."},
    "copper-smelting-start": {"resource": "copper-ore", "output": "furnace", "pairs": 4,
        "label": "Copper plates: burner drill and stone furnace pairs on copper ore.",
        "goal": "Start copper plate production: burner mining drills on copper ore dropping into stone "
                "furnaces, fueled by hand with coal. Pay with iron plates from the existing furnaces."},
    "coal-loop": {"resource": "coal", "output": "loop", "pairs": 4, "site_specific": True, "scout": True,
        "label": "Coal loop: four burner drills on coal feeding each other's fuel, so they never need "
                 "refueling and the spare coal waits in them: no more mining fuel by hand. Cheap (four drills) "
                 "and quick; efficient engineers build one early, simple or slow ones keep mining coal by hand.",
        "goal": "Burner mining drills on coal, each dropping its coal into the next one's fuel slot."},
    "boiler-coal": {"boiler_coal": True, "replaces": ["steam-power"], "replaces_machines": ["boiler"],
        "requires_recipes": ["electric-mining-drill", "transport-belt", "burner-inserter", "small-electric-pole"],
        "requires_built": ["steam-power"],
        "label": "Coal to the boilers by belt: electric drills on coal, a belt along the back of the boilers and "
                 "burner inserters feeding them, so power never stops for want of hand-fed coal.",
        "goal": "Feed the boilers with coal by belt from electric drills on the nearest coal."},
    "steam-power": {"water": True, "suggested": True,
        "requires_recipes": ["offshore-pump", "boiler", "steam-engine", "pipe", "small-electric-pole"],
        "requires_built": ["copper-smelting-start"],   # nothing to power before copper (labs, circuits)
        "label": "Electric power: an offshore pump, boiler and steam engine on the nearest shore.",
        "goal": "Build steam power on the nearest shore: an offshore pump on water piped to a boiler, the "
                "boiler's steam output piped to a steam engine, and a small electric pole beside the engine. "
                "The boiler is fueled by hand with coal."},
    "research-lab": {"suggested": True, "requires_recipes": ["lab", "small-electric-pole"],
        "requires_built": ["steam-power", "copper-smelting-start"],
        "label": "Research: a lab powered from the steam engine, fed red science by hand.",
        "goal": "Build a research lab connected to the existing electric network with small electric poles. "
                "The engineer hand-crafts automation science packs and puts them in the lab. The lab is the "
                "job even while red science is still locked: in Factorio 2 crafting the first lab unlocks it."},
    "red-science-automation": {"suggested": True,
        "requires_recipes": ["assembling-machine-1", "inserter", "small-electric-pole", "lab",
                             "automation-science-pack"],
        "requires_built": ["research-lab", "copper-smelting-start"],
        "label": "Automated red science: assemblers make gears and science packs and an inserter feeds a lab.",
        "goal": "Automate red science next to the electric network: an assembler making iron gear wheels, an "
                "inserter moving gears into an assembler making automation science packs, and an inserter "
                "feeding the packs into a lab. Poles power everything. The engineer carries iron plates to "
                "the gear assembler and copper plates to the science assembler."},
    "plate-belts": {"suggested": True, "site_specific": True,
        "requires_recipes": ["transport-belt", "burner-inserter"],
        "requires_built": ["red-science-automation"],
        "label": "Belts: carry iron and copper plates from the furnaces to the science assemblers automatically.",
        "goal": "Connect the existing furnaces to the science assemblers with belts and inserters."},
    "green-science-automation": {"suggested": True, "site_specific": True,
        "requires_recipes": ["assembling-machine-1", "inserter", "small-electric-pole", "logistic-science-pack"],
        "requires_built": ["red-science-automation"],
        "label": "Green science: an assembler making logistic science packs for the labs.",
        "goal": "Add an assembler making logistic science packs that feeds the existing science lab."},
    "steel-smelting": {"suggested": True, "site_specific": True,
        "requires_recipes": ["steel-plate", "burner-inserter", "stone-furnace"],
        "label": "Steel: an inserter moves iron plates from an iron furnace into a second furnace that makes steel.",
        "goal": "Make steel: a burner inserter takes iron plates out of an existing iron furnace and puts them "
                "into a second stone furnace, which smelts them into steel plates."},
    "electric-iron-mining": {"suggested": True, "site_specific": True, "resource": "iron-ore",
        "requires_recipes": ["electric-mining-drill", "small-electric-pole", "stone-furnace"],
        "requires_built": ["steam-power"],
        "label": "Electric iron mining: electric drills on iron ore feeding furnaces, powered from the network.",
        "goal": "Put electric mining drills on iron ore, each dropping into a stone furnace, and connect them "
                "to the existing electric network with small electric poles."},
    "power-expansion": {"suggested": True, "site_specific": True,
        "requires_recipes": ["steam-engine", "small-electric-pole"], "requires_built": ["steam-power"],
        "label": "More power: a second steam engine on the existing boiler.",
        "goal": "Add a second steam engine end to end with the existing one, so the boiler drives two engines."},
    "oil-gathering": {"suggested": True, "site_specific": True, "standalone": True, "resource": "crude-oil",
        "min_tiles": 1,   # (a well is one entity: a patch of a few is worth the trip)
        "requires_recipes": ["pumpjack", "small-electric-pole"], "requires_built": ["steam-power"],
        "label": "Oil: a pumpjack on an oil well, powered from the network. Mining crude oil unlocks oil processing.",
        "goal": "Place a pumpjack on the nearest oil well and connect it to the electric network."},
    "oil-processing": {"suggested": True, "site_specific": True, "standalone": True,
        "grows": True,   # (the game offers it again when a recipe unlocked since adds a plant: sulfur after plastic)
        "builder": "oil-plant",   # (companion/oil_plant.py: built to its ratios; the game's small block if not)
        "requires_recipes": ["oil-refinery", "chemical-plant", "basic-oil-processing"],
        "requires_any": ["plastic-bar", "sulfur"],   # (plastic is often unlocked before sulfur)
        "requires_built": ["oil-gathering"],
        "label": "Oil processing: a refinery on the pumpjack, chemical plants making plastic and sulfur, water piped in.",
        "goal": "Build an oil refinery on the existing pumpjack's output and chemical plants making plastic and "
                "sulfur, as far as their recipes are unlocked; water piped in for sulfur."},
    "engine-assembly": {"suggested": True, "site_specific": True,
        "requires_recipes": ["engine-unit", "assembling-machine-1"], "requires_built": ["steel-smelting"],
        "label": "Engines: an assembler making engine units from steel, gears and pipes.",
        "goal": "Build an assembler making engine units, fed by hand with steel, gears and pipes."},
    "blue-science-automation": {"suggested": True, "site_specific": True,
        "requires_recipes": ["chemical-science-pack", "assembling-machine-1", "inserter"],
        "requires_built": ["red-science-automation", "oil-processing", "engine-assembly"],
        "label": "Blue science: an assembler making chemical science packs that feeds the lab.",
        "goal": "Add an assembler making chemical science packs that feeds the existing lab, hand-fed with "
                "engine units, advanced circuits and sulfur."},
    "blue-supply-automation": {"suggested": True, "site_specific": True,
        "requires_recipes": ["engine-unit", "advanced-circuit", "inserter"],
        "requires_built": ["blue-science-automation", "engine-assembly"],
        "label": "Blue science supply: assemblers making engines and advanced circuits feed the blue assembler.",
        "goal": "Put an engine assembler and an advanced circuit assembler beside the blue science assembler, "
                "each with an inserter into it."},
    # The team world: grey science once its recipe is in reach, before any bus makes it (team world only).
    "military-science-automation": {"military": True,
        "requires_recipes": ["military-science-pack", "piercing-rounds-magazine", "grenade", "stone-wall",
                             "assembling-machine-1", "inserter", "small-electric-pole"],
        "requires_built": ["red-science-automation", "green-science-automation"],
        "label": "Military science: assemblers making piercing rounds, grenades, stone walls and military "
                 "science packs from chests the engineer fills, for the team's weapons research.",
        "goal": "Automate military science packs in the engineer's own plot."},
    # Defense items made by machines at the rate the waves ask for (companion/defense_production.py): turrets, their
    # ammunition, and walls (through the wall machinery), where defense will be needed.
    "defense-production": {"defense_production": True,
        "requires_recipes": ["assembling-machine-1", "inserter", "small-electric-pole"],
        "requires_built": ["steam-power"],
        "label": "Defense supply: assemblers making turrets and ammunition from plates, at the rate the waves "
                 "coming will take them, in chest-fed rows near home.",
        "goal": "Build a machine supply of defense items."},
    # What the science path says is due (companion/tier_path.py, the research chat's tier signals): a supply the next
    # tier takes, a pack line, more of a pack, labs; built at the rate it is due (companion/tier_builds.py).
    # (it also gives lines fed by hand their belts, F-20: pace presses it while packs stand on bridge chests)
    "science-tier": {"tier": True, "connects": True,
        "label": "The science path's next need: a supply, a pack line, more of a pack or more labs, at the rate "
                 "the research ahead wants, fed by belt from the bus and the rows that make its inputs; or belts "
                 "for lines still fed by hand.",
        "goal": "Build what the science path says is due now, at its rate."},
    # The strategist asks for the factory's large sections to be planned again (the architect, rarely).
    "factory-plan": {"architect": True,
        "label": "Plan the factory again: the architect revises the plan of its large sections.",
        "goal": "Revise the factory plan."},
    "upgrade": {"upgrade": True,
        "label": "Upgrade in place: a bus module is flat out while the one it feeds is starved; better "
                 "machines on the same spots instead of a second module.",
        "goal": "Upgrade the bottleneck module."},
    # Ground cleared ahead of time where the factory plan keeps room for later sections, or where someone
    # asked (companion/clearing.py). Scheduled by rule (clearing_due, and when nothing else is to be
    # built), never offered to System One beside the builds that make the factory faster.
    "clear-area": {"clearing": True,
        "label": "Clear ground for a later section: mine the trees and rocks in a zone kept for it (keeping the "
                 "wood and stone) and blast its cliffs once cliff explosives can be made.",
        "goal": "Clear the next part of a zone kept for a later section."},
    # The team world: each engineer holds a wall ring round its factory, moved out as the factory grows, at its plot's
    # outer edges (its stretch of the team's ring) in the end.
    "plot-turrets": {"perimeter": True,
        "label": "Hold the wall ring: turrets behind it, spread out (side by side only where the biters keep "
                 "attacking), the side facing the nearest nests first, kept loaded with the best ammunition it can "
                 "make; more each time until every stretch of the ring has them.",
        "goal": "Put turrets along the wall ring where the biters will come from."},
    "plot-wall": {"perimeter": True,
        "label": "The wall ring round the factory (with room to grow; the next one farther out once the factory "
                 "outgrows it, at the plot's outer edges in the end): one layer of the best wall it can make, a gate "
                 "in the middle of each side, put up from the walls its wall row has stocked (by hand only when the "
                 "plot is attacked).",
        "goal": "Wall the ring round the factory."},
    "reinforce": {"brain": True,
        "label": "Defense where the base is pressed: turrets on the side the biters come from, for the part of "
                 "the base attacked lately or polluting most, as many as it lacks; again as attacks move.",
        "goal": "Put turrets where the base is under pressure from the biters."},
    "ammo-line": {"ammo_line": True,
        "label": "The ammo loop: one belt round the whole wall ring just inside its turrets, closed, an inserter from "
                 "it into each turret, poles along it, fed from the magazine row (or one chest filled by hand); "
                 "turrets added to the ring later get their inserters.",
        "goal": "Run an ammunition belt round the perimeter with an inserter into each wall turret."},
    "starter-turrets": {"starter": True, "team": True, "requires_recipes": ["gun-turret", "firearm-magazine"],
        "label": "A few turrets close to the factory's vital buildings, facing the nests, spread along that side, "
                 "loaded by hand: the first defense, moved to the wall ring later.",
        "goal": "Place a few gun turrets just outside the factory, on the side the biters will come from."},
    # (8 pairs: about 1 brick a second, a wall row's 12 walls a minute, so 200 in under 20 minutes; 4 took 33)
    "brick-smelting": {"team": True, "resource": "stone", "output": "furnace", "pairs": 8,
        "label": "Stone bricks: burner drills on stone dropping into stone furnaces, for the wall row.",
        "goal": "Smelt stone bricks, about one a second: eight burner mining drills on stone, each dropping "
                "into its own stone furnace, fueled by hand with coal."},
    "wall-automation": {"wall_block": True, "team": True,
        "requires_recipes": ["assembling-machine-1", "inserter", "small-electric-pole", "stone-wall"],
        "requires_built": ["brick-smelting", "steam-power"],
        "label": "Walls by machine: an assembler row making walls from bricks fed from a chest, the walls into "
                 "a chest, stocked for the wall line.",
        "goal": "Make walls automatically: an assembling machine making walls from stone bricks, fed from a "
                "chest by an inserter, an inserter putting the walls into a chest."},
    "breach-repair": {"perimeter": True,
        "label": "Biters broke through: one more wall layer behind the others there (up to four) and more turrets "
                 "there, side by side where they keep hitting.",
        "goal": "Thicken the wall where the biters broke through."},
    "outpost-defense": {"outposts": True,
        "requires_recipes": ["gun-turret", "firearm-magazine"],
        "label": "Defense: turret clusters where the base (an outpost or the main base's edge) faces biter "
                 "nests, more for bigger groups of nests, kept loaded with magazines; a wall once researched "
                 "(a double one with gates), and a radar watching beyond.",
        "goal": "Defend the block of the base nearest to a group of nests."},
    "bus-base": {"bus": True,
        "requires_recipes": ["electric-mining-drill", "splitter", "underground-belt", "assembling-machine-1",
                             "inserter", "long-handed-inserter", "small-electric-pole", "stone-furnace"],
        "requires_built": ["electric-iron-mining"],
        "label": "Main bus: mining fields and smelting columns feeding a bus, with gear, circuit, science "
                 "and lab modules sized for the science goal; built one stage at a time.",
        "goal": "Build the next stage of the main bus base."},
    "electric-copper-mining": {"suggested": True, "site_specific": True, "resource": "copper-ore",
        "requires_recipes": ["electric-mining-drill", "small-electric-pole", "stone-furnace"],
        "requires_built": ["steam-power"],
        "label": "Electric copper mining: electric drills on copper ore feeding furnaces, powered from the network.",
        "goal": "Put electric mining drills on copper ore, each dropping into a stone furnace, and connect "
                "them to the existing electric network with small electric poles."},
    "basic-defense": {"suggested": True, "site_specific": True, "repeat_when_threatened": True,
        "requires_recipes": ["gun-turret", "firearm-magazine"],
        "label": "Defense: two gun turrets with ammunition between the factory and recent attackers.",
        "goal": "Place gun turrets between the factory and whoever attacked it, loaded with firearm magazines "
                "the engineer crafts and carries."},
    "coal-supply": {"resource": "coal", "output": "chest", "pairs": 2,
        "label": "Coal supply: burner drills on coal filling chests, so fuel is not mined by hand.",
        "goal": "Set up a coal supply: burner mining drills on coal dropping into wooden chests the "
                "engineer empties, or drills feeding each other so they refuel themselves."},
    "stone-supply": {"resource": "stone", "output": "chest", "pairs": 1,
        "label": "Stone supply: a burner drill on stone filling a chest, for furnaces and walls.",
        "goal": "Set up a stone supply: a burner mining drill on stone dropping into a wooden chest."},
    "iron-smelting-expansion": {"resource": "iron-ore", "output": "furnace", "pairs": 4,
        "label": "More iron plates: add burner drill and stone furnace pairs on iron ore.",
        "goal": "Add more iron plate production: burner mining drills on iron ore dropping into stone "
                "furnaces, fueled by hand with coal, near the existing factory."},
    # Electric as soon as there is power (the user, 2026-10-25: furnaces and boilers fed coal by belt early, mining
    # electric as soon as possible, burner drills gone; hand-carried coal took 39% of an engineer's time by game
    # minute 192 in run 34). Each replaces the burner builds making the same: they are no longer chosen once it
    # can be built, and are retired once it runs (companion/electric_start.py).
    "electric-iron-smelting": {"electric_smelting": "iron-plate", "rate": 7.5, "resource": "iron-ore",
        "requires_recipes": ELECTRIC_SMELTING, "requires_built": ["steam-power"],
        "replaces": ["iron-smelting-start", "iron-smelting-expansion", "electric-iron-mining"],
        "label": "Electric iron smelting: an array of 24 stone furnaces fed iron ore and coal on one belt by electric "
                 "drills, the plates into a chest, built in parts and copied as more plates are wanted; the burner "
                 "pairs then go.",
        "goal": "Smelt iron with electric mining and belts: electric drills on iron ore, a belt to stone furnaces "
                "fed coal by belt from electric drills on coal, the plates into a chest."},
    "electric-copper-smelting": {"electric_smelting": "copper-plate", "rate": 7.5, "resource": "copper-ore",
        "requires_recipes": ELECTRIC_SMELTING, "requires_built": ["steam-power"],
        "replaces": ["copper-smelting-start", "electric-copper-mining"],
        "label": "Electric copper smelting: an array of 24 stone furnaces fed copper ore and coal on one belt by "
                 "electric drills, the plates into a chest, built in parts and copied as more plates are wanted; the "
                 "burner pairs then go.",
        "goal": "Smelt copper with electric mining and belts: electric drills on copper ore, a belt to stone "
                "furnaces fed coal by belt from electric drills on coal, the plates into a chest."},
    "electric-brick-smelting": {"team": True, "electric_smelting": "stone-brick", "rate": 1.25, "resource": "stone",
        "requires_recipes": ELECTRIC_SMELTING, "requires_built": ["steam-power"],
        "replaces": ["brick-smelting"],
        "label": "Electric brick smelting: electric drills on stone belting it to 4 stone furnaces fed coal by "
                 "belt, about a brick a second into a chest for the wall row; the burner pairs then go.",
        "goal": "Smelt stone bricks with electric mining and belts: electric drills on stone, a belt to stone "
                "furnaces fed coal by belt, the bricks into a chest."},
    "electric-coal-supply": {"electric_supply": "coal", "drills": 2, "resource": "coal",
        "requires_recipes": ELECTRIC_SUPPLY, "requires_built": ["steam-power"],
        "replaces": ["coal-supply", "coal-loop"],
        "label": "Electric coal: two electric drills on coal belting it into a chest, for what still burns coal; "
                 "the burner drills on coal then go.",
        "goal": "Mine coal with electric drills into a chest."},
    "electric-stone-supply": {"electric_supply": "stone", "drills": 1, "resource": "stone",
        "requires_recipes": ELECTRIC_SUPPLY, "requires_built": ["steam-power"],
        "replaces": ["stone-supply"],
        "label": "Electric stone: an electric drill on stone belting it into a chest, for furnaces and walls; the "
                 "burner drill on stone then goes.",
        "goal": "Mine stone with an electric drill into a chest."},
}


TROUBLE = {"starved": "short of inputs", "backed_up": "backed up", "no_power": "without power",
           "low_power": "on low power", "no_fuel": "out of fuel", "depleted": "out of ore"}


def base_context(site: Mapping[str, Any]) -> str:
    """The engineer's base in a sentence or two for System One: how big, and which blocks struggle."""
    blocks = [b for b in bp._as_list(site.get("base")) if isinstance(b, Mapping)]
    if not blocks:
        return ""
    trouble = []
    for b in blocks:
        status = b.get("status") or {}
        machines = sum(int(v) for v in status.values()) if isinstance(status, Mapping) else 0
        if not machines:
            continue
        worst, count = max(((k, int(v)) for k, v in status.items() if k in TROUBLE), key=lambda kv: kv[1],
                           default=(None, 0))
        if worst and count * 2 >= machines:
            trouble.append(f"{b.get('stage') or b.get('purpose')} {TROUBLE[worst]} ({count} of {machines})")
    text = f"Base: {len(blocks)} blocks built."
    if trouble:
        text += " Struggling: " + "; ".join(trouble[:6]) + "."
    if site.get("power_short"):
        text += " Power is short."
    return text


def social_context(site: Mapping[str, Any]) -> str:
    """The NPC's relationships, recent attacks and mood, as sentences for System One."""
    social = site.get("social") or {}
    if not isinstance(social, Mapping):
        return ""
    parts = []
    described = []
    for r in bp._as_list(social.get("relations"))[:4]:
        if not isinstance(r, Mapping):
            continue
        memories = "; ".join(str(m) for m in bp._as_list(r.get("memories"))[:2])
        described.append(f"{r.get('name', 'someone')} ({r.get('label', 'neutral')}"
                         + (f": {memories}" if memories else "") + ")")
    if described:
        parts.append("Relationships: " + ", ".join(described) + ".")
    attacks = social.get("recent_attacks") or 0
    if isinstance(attacks, (int, float)) and attacks > 0:
        who = ", ".join(str(a) for a in bp._as_list(social.get("attackers"))) or "someone"
        parts.append(f"Attacked or robbed {int(attacks)} time(s) in the last 10 minutes by {who}.")
    mood = social.get("mood") or {}
    if isinstance(mood, Mapping):
        if (mood.get("anger") or 0) >= 40:
            parts.append("Feeling angry.")
        if (mood.get("stress") or 0) >= 40:
            parts.append("Under stress.")
        if (mood.get("morale") or 50) <= 25:
            parts.append("Morale is low.")
    return " ".join(parts)


def defense_production_ready(site: Mapping[str, Any], purpose: str = "defense-production") -> bool:
    """The defense supply could be built here, whatever its pick: its recipes, power built, not failed lately, and
    defense will be needed (the death world, a threat, or a survivor's nature)."""
    spec = PURPOSES[purpose]
    recipes = ratios.unlocked(site)
    done = built_purposes(site)
    return (all(n in recipes for n in spec["requires_recipes"]) and all(has_built(n, done) for n in spec["requires_built"])
            and purpose not in set(bp._as_list(site.get("failed_purposes")))
            and bool(team_area.team_of(site) or threatened(site) or goal_militant(site)))


def held_for_plates(purpose: str, site: Mapping[str, Any]) -> bool:
    """A science line held while the plates are short of what the tiers will take and an array for them can be
    built (companion/plate_lead.py: plates first, science sized after)."""
    if not plate_lead.science_line(purpose):
        return False
    if purpose == "science-tier":
        from companion import tier_builds
        if tier_builds.retrofit_due(site):
            return False   # (belts for a line fed by hand are never held: F-20)
        build = tier_builds.cached_pick(site)
        if build is None:
            return False
        if tier_builds.lane_first(site, build):
            return True   # (its plates' bus lane first, F-20 B; belts for a line fed by hand are never held)
        labs = sum(int((b.get("machines") or {}).get("lab", 0) or 0) for b in bp._as_list(site.get("base"))
                   if isinstance(b, Mapping))
        # (a row of labs alone past the first few is science scale: held while a plate is under the draw and something
        # offered grows it (never on a plate nothing will grow, F-19); a pack row, its labs with it, is held as the pack
        # rows are, so research is never stalled for want of a pack: a red row with 9 labs went up at minute 22 of
        # fresh run 62, F-15)
        if build.item == "lab" and labs + int(build.rate) > plate_lead.MIN_LABS and plate_lead.holds(site):
            return True
        if not plate_lead.is_pack(build.item):
            return False   # (steel, the first labs, what is not science scale: not held)
    return plate_lead.holds(site)


def goal_militant(site: Mapping[str, Any]) -> bool:
    """Survival first by nature (a death world's survivor: goal_brain.militant_of)."""
    from companion import goal_brain
    return goal_brain.militant_of(site)


def threatened(site: Mapping[str, Any]) -> bool:
    """Its base under threat (companion/threat.py): attacks lately by biters (the game's threat board) or by
    others (its relations) against a part of its base its turrets do not hold, the waves coming often enough,
    or a breach open. (Biter attacks were not counted at first: three on a one-engineer base, 46 biters in
    all, brought no turrets, and it died, 2026-10-24. Then any attack was a threat for 10 minutes, however
    well its turrets held.)"""
    from companion import threat
    return threat.estimate(site).threatened


class DecisionsChooser:
    """System One choice over layout options via the Decisions API."""

    def __init__(self, provider: Any = None):
        if provider is None:
            from companion.decisions_api import DecisionsProvider
            provider = DecisionsProvider()
        self.provider = provider

    def choose(self, site: Mapping[str, Any], options: list[dict],
               question: str = "layout") -> tuple[str, dict[str, float]]:
        persona = site.get("personality") or {}
        traits = ", ".join(bp._as_list(persona.get("traits"))) or "unknown"
        axes = persona.get("axes") or {}
        name = site.get("npc_name", "An NPC engineer")
        social = " ".join(s for s in (base_context(site), social_context(site)) if s)
        social = f" {social}" if social else ""
        if question == "research":
            done = ", ".join(sorted(built_purposes(site))) or "a small starting factory"
            context = (f"{name} has research labs and must pick the next technology to research on the way to "
                       f"building a rocket. Factory so far: {done}. Traits: {traits}. Cheap technologies that "
                       "unlock machines for automation usually help most early; ambitious engineers aim further, "
                       "careful ones pick what makes their factory reliable; threatened ones may want weapons."
                       + (" This is a death world and the team arms first: weapons, turrets, walls, ammunition "
                          "and military science before factory techs; the players lead the research queue."
                          if site.get("team") else "")
                       + social)
        elif question == "goal":
            inventory = ", ".join(f"{n} {k}" for k, n in sorted((site.get("inventory") or {}).items()))
            done = ", ".join(sorted(built_purposes(site))) or "only the starting iron setup"
            context = (f"{name} is building a factory in Factorio toward a rocket and must pick what to build "
                       f"next. Already built: {done}. Carrying: {inventory}. Traits: {traits}. "
                       "A good opening: iron plates from burner drills deep in the iron patch, a coal loop so fuel is not mined by hand, copper plates, then steam power and a lab only once there is something to power, then automate red science and research toward assemblers and electric drills. Power is pointless with nothing to power. Ambitious "
                       "engineers expand, careful ones secure fuel and supplies first, curious ones try new "
                       "resources, and engineers under attack protect what they have."
                       + (" This is a death world and the engineer is one of a team: it holds a wall ring round "
                          "its factory, moved out as the factory grows, at its plot's outer edges (its stretch of the "
                          "team's ring) in the end. The nests start far off, so steam power and a lab come first; "
                          "then the wall ring (plot-wall) as its science grows, turrets spread along it, the side "
                          "facing the nests first (plot-turrets), and at once whenever it is attacked: turrets, and "
                          "thicker walls where biters broke through (breach-repair)." if team_area.team_of(site) else "")
                       + social)
        elif question == "factory-plan":
            context = (f"{name} must pick a plan for the large sections of its Factorio factory: what each makes, "
                       f"where it goes and how it grows. Traits: {traits}. The engineers race to reach space; a plan "
                       "that gets there fast and automates the many buildings robots will place later is good; "
                       "ambitious engineers aim bigger, careful ones keep more room and supply in hand."
                       + (" This is a death world: survival comes first." if team_area.team_of(site) else "")
                       + social)
        elif question == "habit":
            context = (f"{name} has to decide what to do: {site.get('summary', '')} Traits: {traits}. Some past "
                       "decisions worked in situations like this one (how often they were carried out is shown). "
                       "Repeating one is quick; thinking it over takes longer but can fit new circumstances. "
                       "Cautious and traditional engineers repeat what worked; curious, adaptable ones think "
                       "again more often, especially when a lot is at stake." + social)
        else:
            context = (f"{name} must pick a factory layout. "
                       f"Goal: {site.get('goal', '')} Traits: {traits}. "
                       "Each saved layout comes with a report: whether it fits this ground, makes what is needed, "
                       "is the right size and has its parts, and what its record and its notes from earlier "
                       "games say. Reusing one that fits and works is fast and free; adapting one has the "
                       "designer change only what its gaps need (slower, costs money, kept as a variant); a new "
                       "design is for when nothing comes close. Curious, innovative engineers like to try new "
                       "designs; cautious, frugal, traditional ones prefer proven layouts.")
        result = self.provider.decide({"context": context, "profile": dict(axes), "features": {},
                                       "candidates": options, "purpose": question})
        return result["selected_id"], dict(result["probabilities"])


PENDING_TICKS = 1800   # nothing else to build while a plan is worked out in the background: asked again this soon


class Defer(Exception):
    """The NPC should not build a new layout now; retry after `ticks`."""

    def __init__(self, reason: str, ticks: int = 36000):
        super().__init__(reason)
        self.reason, self.ticks = reason, ticks
        self.by_planner = False

    @classmethod
    def declined(cls, reason: str, ticks: int) -> "Defer":
        """Its planner declined the goal (nothing to build there now): held then, however it was chosen (D-20)."""
        out = cls(reason, ticks)
        out.by_planner = True
        return out


class MoveSite(Exception):
    """No room here, but the NPC knows a patch elsewhere: plan around (dx, dy) from home."""

    def __init__(self, purpose: str, dx: int, dy: int, goal: str = ""):
        super().__init__(f"{purpose} at {dx},{dy}")
        self.purpose, self.dx, self.dy, self.goal = purpose, dx, dy, goal


class Scout(Exception):
    """The NPC knows no usable patch of `resource`: it should go and look for one."""

    def __init__(self, purpose: str, resource: str):
        super().__init__(f"scout for {resource}")
        self.purpose, self.resource = purpose, resource


def known_resources(site: Mapping[str, Any]) -> set[str]:
    """Resources in the planning window plus patches the NPC has seen elsewhere."""
    present = {r.get("resource") for r in bp._as_list(site.get("resources")) if isinstance(r, Mapping)}
    elsewhere = {p.get("resource") for p in bp._as_list(site.get("known_patches")) if isinstance(p, Mapping)}
    return present | elsewhere


def built_purposes(site: Mapping[str, Any]) -> set[str]:
    return set(bp._as_list(site.get("completed_purposes")))


def has_built(name: str, done: set) -> bool:
    """`name` stands, or a build that replaces it does (electric smelting for the burner pairs it retired: the
    bus waits on electric iron mining, the wall row on brick smelting)."""
    return name in done or any(name in (PURPOSES.get(p) or {}).get("replaces", ()) for p in done)


def electric_of(purpose: str) -> list[str]:
    """The electric goals that replace a burner goal (companion/electric_start.py)."""
    return [p for p, s in PURPOSES.items() if (s.get("electric_smelting") or s.get("electric_supply"))
            and purpose in (s.get("replaces") or ())]


def burner_closed(purpose: str, site: Mapping[str, Any], *, designer: bool = False) -> bool:
    """A burner goal is not built once an electric goal replacing it can be, stands, or waits only on research under
    way and well along (power runs: its burners would only add coal to carry, and be retired minutes later: eight burner pairs on
    stone went up while the electric mining drill was being researched, run 37, 2026-10-25). One that fails to plan
    opens the burner goal again for a while."""
    done = built_purposes(site)
    # (research under way and well along only, DesignedStart.near: not what could merely be researched next, which
    # closed burner iron at power while the electric mining drill was still to research, to minute 23, F-21)
    coming = [{"name": str(n)} for n in bp._as_list(site.get("research_near"))]
    # (not the burners' own fuel: waiting for its research, the coal loop stayed small and the engineer mined coal by
    # hand a third of its time, run 39, 2026-10-25)
    fuel = (PURPOSES.get(purpose) or {}).get("resource") == rates.FUEL
    soon = {**site, "recipes": list(bp._as_list(site.get("recipes"))) + coming} if coming and not fuel else None
    # (open while its electric smelting cannot be afforded from the plates' income yet: the income grows with burner
    # pairs meanwhile, companion/plate_lead.py; fresh run 61 had 30 iron a minute for 35 minutes, 2026-10-28)
    def closes(p: str) -> bool:
        if not PURPOSES[p].get("electric_smelting"):
            return (p in done or available(p, site, designer=designer)
                    or (soon is not None and available(p, soon, designer=designer)))
        # (smelting: only an array that stands or can be built now and is bought soon from the income; one waiting on
        # research under way leaves the burner pairs open while its plates are under the draw: fresh run 62 closed
        # them at minute 19 for an array still researching at 27, and iron stayed at 105 a minute to 60, F-15)
        now = p in done or plate_lead.available_ungated(site, p, designer=designer)
        if not now and soon is not None and available(p, soon, designer=designer):
            now = not plate_lead.under(site, str(PURPOSES[p]["electric_smelting"]))
        return now and plate_lead.affordable(site, p)
    return any(closes(p) for p in electric_of(purpose))


POWERED = ("steam-power", "power-plant")   # its own electric power built: from then on the designer's turn
# How long the engineer waits on a design when a layout it could build at once is at hand. Past this it builds
# that layout, and the design, when it comes, goes into the library for next time. (Codex took 26 to over 120 s
# on the same small lab design in the one-engineer hour, 2026-10-24, the engineer standing by all the while.)
DESIGN_WAIT_S = 45


FAILURES_KEPT = 5   # failed plans kept to replay (per kind)


def keep_failure(kind: str, site: Mapping[str, Any], world: Any, why: str) -> None:
    """A plan that failed, with the site and the map it searched, saved to replay offline
    (data/failures/<kind>-<request>.json; the oldest beyond FAILURES_KEPT go)."""
    import os
    from pathlib import Path
    data = os.environ.get("NPC_FACTIONS_DATA")
    if not data:
        return
    folder = Path(data) / "failures"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_-]", "-", f"{kind}-{site.get('request_id')}")
        clean = {k: v for k, v in world.items() if not str(k).startswith("_")} if isinstance(world, Mapping) else world
        (folder / f"{name}.json").write_text(json.dumps({"why": why, "site": site, "world": clean}, default=str),
                                             encoding="utf-8")   # (without the planners' own caches)
        kept = sorted(folder.glob(f"{kind}-*.json"), key=lambda p: p.stat().st_mtime)
        for old in kept[:-FAILURES_KEPT]:
            old.unlink()
    except (OSError, TypeError, ValueError):
        pass   # (evidence, never in the way)


def has_power(site: Mapping[str, Any]) -> bool:
    return bool(built_purposes(site) & set(POWERED))


def perimeter_blocks(site: Mapping[str, Any], purpose: str) -> list[Mapping[str, Any]]:
    """The engineer's built plans for one of the plot-edge goals (from the base the game exported)."""
    return [b for b in bp._as_list(site.get("base")) if isinstance(b, Mapping) and b.get("purpose") == purpose]


def open_breach(site: Mapping[str, Any]) -> bool:
    """A breach with fewer than four layers standing that has not had all its repairs yet."""
    from companion import threat
    return threat.open_breaches(site) > 0


def emergency(site: Mapping[str, Any]) -> bool:
    """Under threat, or a breach open: defenses by hand, now, as much as the time to the next wave allows
    (emergency_kit)."""
    return threatened(site) or open_breach(site)


MIN_HAND_WALLS = 8       # fewer walls than this, made by hand in an emergency, hold nothing: none then
AMMO_PER_TURRET = 10     # magazines a new turret is first loaded with


@dataclass(frozen=True)
class Kit:
    """What an emergency build may make by hand (companion/handcraft.py) in its time (companion/threat.py):
    the hand seconds, turrets with their first ammunition, and walls (beyond those it has)."""
    seconds: float = 0.0
    turrets: int = 0
    walls: int = 0


def emergency_kit(site: Mapping[str, Any]) -> Kit:
    """The hand work an emergency build may take now: a share of the time to the next wave, as much as it
    is an emergency; turrets first, walls from what is left after a pair."""
    from companion import handcraft, perimeter, threat
    t = threat.estimate(site)
    seconds = threat.hand_seconds(t)
    if open_breach(site) or threat.unwalled_hit(site):   # (a breach, or hit with no wall: an emergency in full)
        seconds = max(seconds, min(threat.MAX_BUDGET_S, max(threat.MIN_BUDGET_S, t.next_wave_s)) * threat.HAND_SHARE)
    if seconds <= 0:
        return Kit()
    arms = perimeter.arms(site)
    try:
        book = handcraft.book_for(site)
        turrets = 0
        guns = [g for g in arms["turrets"] if g.get("kind") == "ammo"]
        pair_cost = 0.0
        if guns:
            # (the best ammunition it can have now: piercing rounds need steel, which it may not make yet)
            options = [a for c in guns[0].get("ammo_categories") or () for a in arms["ammo"].get(c) or ()]
            ammo = next((a for a in options if handcraft.obtainable(site, a, book)), None)
            kit = {guns[0]["name"]: 1, **({ammo: AMMO_PER_TURRET} if ammo else {})}
            turrets = handcraft.most(site, kit, seconds, book=book)
            pair_cost = handcraft.cost(site, {k: v * 2 for k, v in kit.items()}, book) + 2 * handcraft.PLACE_S
        walls = 0
        if arms["walls"]:
            wall = arms["walls"][0]
            have = handcraft.held(site)
            have[wall] = 0.0   # (those it has count anyway: these are the ones made by hand)
            walls = handcraft.most(site, {wall: 1}, max(0.0, seconds - min(pair_cost, seconds)), per_unit_s=0.5,
                                   cap=400, book=book, have=have)
    except Exception:   # (a judgment, never in the way: the least an emergency gets)
        return Kit(seconds=seconds)
    return Kit(seconds=round(seconds, 1), turrets=turrets, walls=walls)


def hand_walls(site: Mapping[str, Any]) -> int:
    """The walls an emergency build may use: those it has, and those it can make by hand in the time (none by
    hand unless at least MIN_HAND_WALLS)."""
    from companion import perimeter, threat
    arms = perimeter.arms(site)
    if not arms["walls"]:
        return 0
    stock = perimeter.stocked(site, arms["walls"][0])
    made = emergency_kit(site).walls if emergency(site) or threat.unwalled_hit(site) else 0
    return stock + (made if stock + made >= MIN_HAND_WALLS else 0)


def perimeter_available(purpose: str, site: Mapping[str, Any]) -> bool:
    """The wall ring's goals: in the team world, with what it takes made. The wall line goes up from walls made by
    machine once enough are stocked for a long stretch, and the turrets along it once it is started; in an emergency
    both at once, by hand (the user, 2026-10-24)."""
    from companion import defense_brain, perimeter, threat
    if not team_area.team_of(site) or purpose in set(bp._as_list(site.get("failed_purposes"))):
        return False
    if defense_brain.declined(site, purpose):   # (declined, and nothing changed since: D-16)
        return False
    edge_list = perimeter.edges(site)
    if not edge_list:
        return False
    kit = perimeter.arms(site)
    if purpose == perimeter.TURRETS:
        return (bool(kit["turrets"]) and perimeter.turrets_wanted(site) > 0
                and (bool(perimeter_blocks(site, perimeter.WALL)) or emergency(site)))
    if purpose == perimeter.WALL:   # (in an emergency, only as many as it has or can make in time)
        return (bool(kit["walls"]) and (not perimeter.walled(site) or perimeter.thicken_due(site)
                                        or perimeter.holes_due(site))
                and (perimeter.stocked(site, kit["walls"][0]) >= perimeter.wall_stock(site)
                     or (emergency(site) or threat.unwalled_hit(site)) and hand_walls(site) >= MIN_HAND_WALLS))
    return bool(kit["walls"] or kit["turrets"]) and open_breach(site)


def brain_available(site: Mapping[str, Any]) -> bool:
    """Defense where the base is pressed (companion/defense_brain.py): a turret it can make that needs no
    power, and a part of its base the biters press harder than its turrets hold."""
    from companion import defense_brain, perimeter
    if defense_brain.PURPOSE in set(bp._as_list(site.get("failed_purposes"))):
        return False
    if defense_brain.declined(site, defense_brain.PURPOSE):   # (declined, and nothing changed since: D-16)
        return False
    if not any(t.get("kind") == "ammo" for t in perimeter.arms(site)["turrets"]):
        return False
    try:
        return defense_brain.most_needed(site) is not None
    except Exception:   # (a judgment, never in the way of a plan)
        return False


def starter_available(site: Mapping[str, Any]) -> bool:
    """The starter turrets: once, in the team world, before any turrets stand on the wall line, with a
    factory to guard and a turret it can make that needs no power."""
    from companion import perimeter
    purpose = perimeter.STARTER
    if (not team_area.team_of(site) or purpose in built_purposes(site)
            or purpose in set(bp._as_list(site.get("failed_purposes")))
            or perimeter_blocks(site, perimeter.TURRETS)):
        return False
    recipes = ratios.unlocked(site)
    return (all(n in recipes for n in PURPOSES[purpose]["requires_recipes"])
            and any(t.get("kind") == "ammo" for t in perimeter.arms(site)["turrets"])
            and perimeter.core_box(site) is not None)


def available(purpose: str, site: Mapping[str, Any], *, designer: bool = False) -> bool:
    """Whether this purpose makes sense here: resources, water, unlocked recipes, prerequisites."""
    if purpose in (site.get("worked_out") or ()):
        return False   # (its plan is being worked out in the background: something else this request)
    from companion import failures
    if failures.held(site, purpose):
        return False   # (failed lately, and nothing changed that could mend it: SO-4)
    if _guards(purpose):
        from companion import defense_brain
        if defense_brain.declined(site, purpose):   # (its planner declined it, nothing changed since: D-16)
            return False
    spec = PURPOSES[purpose]
    if spec.get("team") and not team_area.team_of(site):
        return False
    if held_for_plates(purpose, site):
        return False
    if electric_of(purpose) and burner_closed(purpose, site, designer=designer):
        return False
    if spec.get("electric_smelting") or spec.get("electric_supply"):
        from companion import growth
        plate = spec.get("electric_smelting")
        if plate in growth.PLATES and f"smelting-{plate}" in ((site.get("base_plan") or {}).get("done") or {}):
            return False   # (its bus smelts this plate now: the bus's own smelting retires the burner pairs)
        # (not merely because a bus is planned: one whose smelting of a plate is not built, or that takes it from the
        # base's own smelting, starved its circuit and cable rows of copper never electrified, F-21)
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        from companion import electric_start
        # (a smelting array is built in parts, grown in place: again until it is whole; then another array while the
        # plates are short of what the next tier will take, companion/plate_lead.py)
        more = spec.get("electric_smelting") and purpose in done and (
            not electric_start.grown(site, purpose) or plate_lead.wants(site, str(spec["electric_smelting"])))
        if spec.get("electric_smelting") and plate_lead.gated(site, purpose, designer=designer):
            return False   # (its income first: what grows the plates it waits on, F-14)
        return ((purpose not in done or more) and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and spec.get("resource") in known_resources(site)
                and all(n in recipes for n in spec["requires_recipes"])
                and all(has_built(n, done) for n in spec["requires_built"]))
    if spec.get("architect"):   # only when asked for by name, once power runs
        return site.get("purpose") == purpose and has_power(site)
    if spec.get("perimeter"):
        return perimeter_available(purpose, site)
    if spec.get("robot_network"):   # (only while part of the base lies outside every roboport's reach)
        from companion import robo_grid
        return robo_grid.due(site)[0]
    if spec.get("robot_supply"):   # (only while the network is short of what its robots use and makes none)
        from companion import robo_grid
        return robo_grid.supply_due(site)[0]
    if spec.get("starter"):
        return starter_available(site)
    if spec.get("brain"):
        return brain_available(site)
    if spec.get("ammo_line"):
        from companion import ammo_line
        return ammo_line.due(site)
    if spec.get("wall_block"):
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        return (purpose not in done and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"])
                and all(has_built(n, done) for n in spec["requires_built"]))
    if spec.get("clearing"):
        # After the opening (power runs, no opening step to build), with ground waiting to be cleared.
        from companion import clearing
        return (has_power(site) and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and opening_goal(site, designer=designer) is None and clearing.zone_for(site) is not None)
    if spec.get("upgrade"):
        from companion import upgrade_planner
        return (upgrade_planner.upgradable(site) and purpose not in set(bp._as_list(site.get("failed_purposes"))))
    if spec.get("defense_production"):
        from companion import defense_production
        if not defense_production_ready(site, purpose):
            return False
        got = defense_production.cached_pick(site)
        return got is not None and got.goal == defense_production.GOAL
    if spec.get("tier"):
        from companion import tier_builds
        done = built_purposes(site)
        return (has_built("research-lab", done) and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and (tier_builds.cached_pick(site) is not None or tier_builds.retrofit_due(site)))
    if spec.get("military"):
        # Only in the team world, once, and not where the bus or a block makes grey packs already (the science
        # path's goal grows a line that stands: tier_builds).
        saved = site.get("base_plan") or {}
        on_bus = "military-science-pack" in set(saved.get("packs") or ()) | set(saved.get("extra") or ())
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        return (bool(team_area.team_of(site)) and not on_bus and not makes(site, "military-science-pack")
                and purpose not in done
                and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"])
                and all(has_built(n, done) for n in spec["requires_built"]))
    if spec.get("ally_help"):
        if team_area.team_of(site):
            return False   # the team world: nobody builds at a player's base (teammates come and fight instead)
        recipes = ratios.unlocked(site)
        return (isinstance(site.get("help"), Mapping) and site["help"].get("kind") == "defend"
                and all(n in recipes for n in spec["requires_recipes"]))
    if spec.get("outposts"):
        recipes = ratios.unlocked(site)
        # A block the exported threats can reach and no defense covers (checked against the map when planned).
        from companion import defense_planner
        return (bool(defense_planner.frontier(site, {"entities": []}))
                and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"]))
    if spec.get("nuclear"):
        # (once new power is due: the draw ahead past most of what the generators give (companion/power_outlook.py;
        # the user plays ahead, a brownout is too late), or power short; weighed with the other goals then, urgent
        # only when short. Its uranium scouted when unknown, as any goal's resource. One at a time.)
        from companion import power_outlook
        recipes = ratios.unlocked(site)
        return ((bool(site.get("power_short")) or power_outlook.due(site))
                and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and not has_built(purpose, built_purposes(site))
                and all(n in recipes for n in spec["requires_recipes"]))
    if spec.get("nuclear_fuel"):   # (as soon as the plant stands: without fuel it makes nothing)
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        return (purpose not in set(bp._as_list(site.get("failed_purposes"))) and not has_built(purpose, done)
                and all(has_built(n, done) for n in spec["requires_built"])
                and all(n in recipes for n in spec["requires_recipes"]))   # (its uranium scouted when unknown)
    if spec.get("power_plant"):
        recipes = ratios.unlocked(site)
        return (bool(site.get("power_short")) and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"]))
    if spec.get("boiler_coal"):
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        return (purpose not in done and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"])
                and all(has_built(n, done) for n in spec["requires_built"]))
    if spec.get("bus"):
        from companion import bus_planner
        recipes = ratios.unlocked(site)
        done = built_purposes(site)
        return (bus_planner.wants_a_bus(site) and purpose not in set(bp._as_list(site.get("failed_purposes")))
                and all(n in recipes for n in spec["requires_recipes"])
                and all(has_built(n, done) for n in spec["requires_built"])
                and (not (site.get("base_plan") or {}).get("stages") or bool(bus_planner.remaining(site))
                     or bool(bus_planner.extendable(site))))
    present = known_resources(site)
    recipes = ratios.unlocked(site)
    done = built_purposes(site)
    if purpose in set(bp._as_list(site.get("failed_purposes"))):
        return False  # failed recently; the game says when to try again
    if spec.get("resource") and spec["resource"] not in present and not spec.get("suggested")             and not spec.get("scout"):
        return False   # (a goal marked "scout" sends the engineer looking for its resource)
    if spec.get("water") and not bp._as_list(site.get("water_rows")) and purpose != "steam-power":
        return False
    if any(name not in recipes for name in spec.get("requires_recipes", [])):
        return False
    if spec.get("requires_any") and not any(name in recipes for name in spec["requires_any"]):
        return False
    if any(not has_built(name, done) for name in spec.get("requires_built", [])):
        return False
    if (spec.get("suggested") and purpose in done and not (spec.get("repeat_when_threatened") and threatened(site))
            and not (spec.get("resource") and running_out(site, spec["resource"]))
            and not (spec.get("grows") and purpose in (site.get("suggestions") or {}))
            and not (spec.get("builder") == "oil-plant" and _oil_missing(site))):
        return False  # one of each at a time, unless its ore is running out (then again, on fresh ore)
    if purpose == "steam-power" and purpose not in done and purpose not in (site.get("suggestions") or {}):
        # No shore in the site: a power plant at the nearest shore on the map, with a pole line home.
        recipes = ratios.unlocked(site)
        return all(n in recipes for n in spec["requires_recipes"])
    if (spec.get("suggested") and (not designer or spec.get("site_specific")) and not spec.get("builder")
            and purpose not in (site.get("suggestions") or {})):   # (a goal with a builder of its own needs none)
        # No built-in layout here (and a site-specific one is never designed: a second steam engine with
        # no boiler free for it was chosen by the factory plan, and failed, three times running in a
        # one-engineer run, 2026-10-24). A resource nobody has seen yet is still a goal: choosing it sends
        # the engineer scouting.
        # A block of its own ("standalone") whose built-in layout found no room may still be had from a saved
        # build or the designer (CatalogPlanner._layout), when there is a designer.
        if (not spec.get("resource") or spec["resource"] in known_resources(site))                 and not (spec.get("standalone") and designer):
            return False
    return True


def _oil_missing(site: Mapping[str, Any]) -> list[str]:
    from companion import oil_plant
    return oil_plant.missing_products(site)


def suggested_plan(site: Mapping[str, Any], purpose: str) -> dict:
    """The game's own native layout for this purpose (proven templates checked against the map)."""
    plan = (site.get("suggestions") or {}).get(purpose)
    if not plan:
        raise layout_planner.PlanError(f"no native layout for {purpose} near home")
    safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(site.get("request_id", "x")))
    stages = [{**stage, "depends_on": bp._as_list(stage.get("depends_on")),
               "manual_supplies": bp._as_list(stage.get("manual_supplies")),
               "witnesses": bp._as_list(stage.get("witnesses"))} for stage in bp._as_list(plan["stages"])]
    return {"plan_id": f"{purpose}-{safe}", "source": "native-suggestion",
            "entities": bp._as_list(plan["entities"]), "stages": stages}


def unfinished(purpose: str, site: Mapping[str, Any]) -> bool:
    """A purpose built in stages (the main bus) that has stages left, or power while it is short."""
    if PURPOSES[purpose].get("power_plant"):
        return bool(site.get("power_short"))
    if PURPOSES[purpose].get("perimeter"):
        return perimeter_available(purpose, site)   # (stretches without turrets, edges without a wall)
    if PURPOSES[purpose].get("ally_help"):
        return isinstance(site.get("help"), Mapping)   # until it is built (the game then clears the request)
    if PURPOSES[purpose].get("outposts"):
        from companion import defense_planner
        return bool(defense_planner.frontier(site, {"entities": []}))   # a threatened block still undefended
    if PURPOSES[purpose].get("upgrade"):
        from companion import upgrade_planner
        return upgrade_planner.upgradable(site)
    if not PURPOSES[purpose].get("bus"):
        return False
    from companion import bus_planner
    return bool(bus_planner.remaining(site)) or bool(bus_planner.extendable(site))


RUNNING_OUT = 15   # minutes of ore left under a block's drills (or all its blocks on an ore): time for new ground


def spent(block: Mapping[str, Any]) -> bool:
    """A block whose drills will have mined their ground out within RUNNING_OUT minutes."""
    ore = block.get("ore")
    return isinstance(ore, Mapping) and ore.get("minutes") is not None and float(ore["minutes"]) < RUNNING_OUT


def running_out(site: Mapping[str, Any], resource: str | None) -> bool:
    """The engineer's drills on `resource`, all its blocks together, have under RUNNING_OUT minutes of it left."""
    left = rate = 0.0
    for block in bp._as_list(site.get("base")):
        ore = block.get("ore") if isinstance(block, Mapping) else None
        if isinstance(ore, Mapping) and ore.get("name") == resource:
            left += float(ore.get("left") or 0)
            rate += float(ore.get("rate") or 0)
    return rate > 0 and left / rate / 60 < RUNNING_OUT


# The first source of an intermediate, as soon as its research is done and nothing makes it yet, ahead of the weighing (a
# small build that opens a whole line). Steel: military science, steel furnaces, medium poles; the bus makes it only
# with blue science's extension, and in run 57 steel was researched by minute 111 and weighed below the bus (0.75
# against 4.45), so none was made. NPC Research's "steel due" signal may decide this later (R-1).
FIRST_SOURCES = {"steel-plate": "steel-smelting"}


def first_source_due(site: Mapping[str, Any], *, designer: bool = False) -> str | None:
    """The goal that makes a newly researched intermediate for the first time (FIRST_SOURCES), when it is due: the
    science path's goal when the path says it is due (sized to what the tier ahead takes: tier_builds), else its own
    small first build."""
    unlocked = ratios.unlocked(site)
    for item, goal in FIRST_SOURCES.items():
        if item in unlocked and not makes(site, item) and (goal not in built_purposes(site)):
            from companion import tier_builds
            build = tier_builds.cached_pick(site) if available("science-tier", site, designer=designer) else None
            if build and (build.item == item or item in [w for w, _ in build.waits]):
                return "science-tier"
            if available(goal, site, designer=designer):
                return goal
    return None


def first_source_item(goal: str) -> str:
    """What a first source makes (for the log): the item it was chosen for."""
    return next((i for i, g in FIRST_SOURCES.items() if g == goal), "steel-plate" if goal == "science-tier" else goal)


def makes(site: Mapping[str, Any], item: str) -> bool:
    """Whether some block of the engineer's base makes `item` (its recipes, or what its capacity says it makes), its
    ore not nearly mined out."""
    for block in bp._as_list(site.get("base")):
        if not isinstance(block, Mapping) or spent(block):
            continue
        recipes = block.get("recipes")
        made = (block.get("capacity") or {}).get("makes") if isinstance(block.get("capacity"), Mapping) else None
        if (isinstance(recipes, Mapping) and item in recipes) or (isinstance(recipes, list) and item in recipes)                 or (isinstance(made, Mapping) and item in made):
            return True
    return False


def smelters(site: Mapping[str, Any], plate: str) -> int:
    """Furnaces the engineer has making `plate` (from its base's blocks), not counting those whose ore is
    nearly mined out: they will stop, so the opening builds more on fresh ore."""
    total = 0
    for block in bp._as_list(site.get("base")):
        if not isinstance(block, Mapping) or spent(block):
            continue
        recipes = set(bp._as_list(block.get("recipes")))
        purpose = str(block.get("purpose") or "")
        kind = ("iron-plate" if "iron-plate" in recipes or purpose.startswith("iron-smelting") else
                "copper-plate" if "copper-plate" in recipes or purpose.startswith("copper-smelting") else None)
        if kind == plate:
            machines = block.get("machines") or {}
            total += sum(int(machines.get(f, 0)) for f in ("stone-furnace", "steel-furnace", "electric-furnace"))
    return total


# Coal the engineer carries by hand to its burner buildings (coal/s; coal holds 4 MJ), and what a looped
# burner drill spares (it mines 0.25/s and burns 0.0375/s). A boiler is counted at half its full burn.
COAL_DRAW = {"burner-mining-drill": 0.15 / 4, "stone-furnace": 0.09 / 4, "steel-furnace": 0.09 / 4,
             "boiler": 1.8 / 4 / 2}
LOOP_NET = 0.25 - 0.15 / 4
LOOP_MAX = 8            # looped drills: past this, belts and electric drills are the better answer
BURNER_BUDGET = 30      # burner drills in all: more pollute far enough to bring biters early
SPENT_PER_TILE = 50     # a known patch with less ore than this a tile is nearly mined out: skipped
HELD_SCOUTS = 2         # trips round home for a resource it knows only on others' ground, before it waits
# Blocks fed by belt (or that feed themselves): nothing the engineer carries to them.
BELT_FED = ("coal-loop", "bus-base", "power-plant", "boiler-coal", "electric-iron-smelting",
            "electric-copper-smelting", "electric-brick-smelting", "electric-coal-supply", "electric-stone-supply")


def _machines(site: Mapping[str, Any]):
    for block in bp._as_list(site.get("base")):
        if isinstance(block, Mapping):
            yield str(block.get("purpose") or ""), block.get("machines") or {}


def loop_drills(site: Mapping[str, Any]) -> int:
    """Burner drills in the engineer's coal loops (the first loop's four if its block is not listed), not
    counting loops whose coal is nearly mined out: the loop grows again on fresh coal."""
    loops = [b for b in bp._as_list(site.get("base")) if isinstance(b, Mapping) and b.get("purpose") == "coal-loop"]
    if not loops:
        return 4 if "coal-loop" in built_purposes(site) else 0
    return sum(int((b.get("machines") or {}).get("burner-mining-drill", 0)) for b in loops if not spent(b))


def coal_draw(site: Mapping[str, Any]) -> float:
    """Coal per second the hand-fed burner buildings burn (boilers only until they get coal by belt)."""
    belted = "boiler-coal" in built_purposes(site)
    total = 0.0
    for purpose, machines in _machines(site):
        if purpose in BELT_FED or purpose.startswith("growth"):
            continue
        for name, rate in COAL_DRAW.items():
            if name == "boiler" and belted:
                continue
            total += rate * int(machines.get(name, 0))
    return total


# The coal loop spares this much more than its burners burn, so coal waits in it for every trip. Sized to the
# burners exactly, the engineer drained it each trip and mined the rest by hand: a third of its time at minute 16,
# with the loop sparing 0.85 coal a second against 0.72 burnt (run 38, 2026-10-25; the user: grow the loop, take its
# coal and spread it over the furnaces and boilers).
COAL_MARGIN = 1.5


def coal_enough(site: Mapping[str, Any]) -> bool:
    """A coal loop, grown with what the burner buildings burn (players add drills to it as they add
    furnaces and boilers), with COAL_MARGIN to spare, within the loop cap and the pollution budget."""
    if "coal-loop" not in built_purposes(site):
        return False
    drills = loop_drills(site)
    burners = sum(int(m.get("burner-mining-drill", 0)) for _, m in _machines(site))
    # (the budget holds back more burners, not the loop that fuels those standing: capped by it, the engineer mined
    # coal by hand a quarter of its time, run 44, 2026-10-26)
    return drills * LOOP_NET >= COAL_MARGIN * coal_draw(site) or drills >= LOOP_MAX or burners - drills >= BURNER_BUDGET


# A competent opening, as a speedrunner builds it: fuel that needs no hand-mining (grown as the burners
# grow), stone for furnaces, copper for circuits, then power and a lab at once, fed red science by hand
# while the smelting fills up (an ugly first lab, taken down later); iron grown well past the starter
# pair meanwhile, then science made by machines, coal by belt to the boilers once electric drills are
# researched, and more iron. The user (2026-10-24, after a one-engineer hour that researched only at
# minute 43): research running by about minute 15, red science semi-automated by about 45.
OPENING_IRON = 8   # iron furnaces (with their drills) the opening grows to while its first research runs
OPENING = [
    ("coal-loop", coal_enough),
    ("stone-supply", lambda s: "stone-supply" in built_purposes(s)),
    ("copper-smelting-start", lambda s: smelters(s, "copper-plate") >= 4),
    ("steam-power", lambda s: "steam-power" in built_purposes(s)),
    ("research-lab", lambda s: "research-lab" in built_purposes(s)),
    # Red science by machine as soon as automation is researched, as a speedrunner does right after the first lab:
    # two assemblers and a few inserters, while everything else is still burner work. After the electric smelting,
    # red science waited until minute 67 (run 46, 2026-10-26).
    ("red-science-automation", lambda s: "red-science-automation" in built_purposes(s)),
    # Power runs: coal by belt to the boilers and coal mined electric at once, then electric smelting once its
    # research allows (the user, 2026-10-25: furnaces and boilers fed by belt early, mining electric as soon as
    # possible, burner drills gone). Each retires the burner builds it replaces; a burner goal is not built once
    # its electric one can be (burner_closed), so the burner iron below runs only until then.
    ("boiler-coal", lambda s: "boiler-coal" in built_purposes(s)),
    ("electric-coal-supply", lambda s: "electric-coal-supply" in built_purposes(s)),
    # Iron, many furnaces at once: a single pair gave a quarter plate a second and everything waited on it
    # (the user's call, 2026-10-24). The coal loop grows with the burners; upkeep spreads its coal over them.
    # One burst here; what it leaves short of OPENING_IRON comes after the science lines (a second burst
    # here, the first one's layout a furnace short, held red science to minute 53 to 57 in two hours).
    ("iron-smelting-expansion", lambda s: smelters(s, "iron-plate") >= OPENING_IRON
     or bool(perimeter_blocks(s, "iron-smelting-expansion"))),
    # Out of the burner phase as soon as research allows (a step not researched yet is skipped and
    # taken up when it is): science made by machines, drills that need no coal carried to them.
    # (built in parts until they cover the burner pairs, which are then retired: a first part alone left the burners
    # standing, and carrying their coal took a third of the engineer's time, run 46, 2026-10-26)
    ("electric-iron-smelting", lambda s: _covers(s, "electric-iron-smelting")),
    ("electric-copper-smelting", lambda s: _covers(s, "electric-copper-smelting")),
    ("green-science-automation", lambda s: "green-science-automation" in built_purposes(s)),
    # Steel as soon as it is researched, a furnace of it beside an iron furnace: the bus makes steel only with blue
    # science's extension, and steel and military science were not reached in 250 game minutes (run 51; R-1).
    ("steel-smelting", lambda s: "steel-smelting" in built_purposes(s) or makes(s, "steel-plate")),
    ("electric-stone-supply", lambda s: "electric-stone-supply" in built_purposes(s)),
    # The rest of the electric smelting blocks, grown in place, once science is made by machines (their first parts
    # came early, small, while everything was made by hand: the user, 2026-10-26).
    ("electric-iron-smelting", lambda s: _grown(s, "electric-iron-smelting")),
    ("electric-copper-smelting", lambda s: _grown(s, "electric-copper-smelting")),
    ("iron-smelting-expansion", lambda s: smelters(s, "iron-plate") >= OPENING_IRON),
    ("electric-iron-mining", lambda s: has_built("electric-iron-mining", built_purposes(s))),
    ("electric-copper-mining", lambda s: has_built("electric-copper-mining", built_purposes(s))),
    ("iron-smelting-expansion", lambda s: smelters(s, "iron-plate") >= 10),
    # then the base laid out ahead: smelting columns, a main bus and room to grow
    ("bus-base", lambda s: "bus-base" in built_purposes(s)),
]


def _covers(site: Mapping[str, Any], purpose: str) -> bool:
    from companion import electric_start
    return purpose in built_purposes(site) and electric_start.covers(site, purpose)


def _grown(site: Mapping[str, Any], purpose: str) -> bool:
    from companion import electric_start
    return purpose in built_purposes(site) and electric_start.grown(site, purpose)


# The ore of a plan not in PURPOSES (the first plan's spec is made where it is planned).
FIRST_RESOURCE = {"iron-smelting-start": "iron-ore"}

# What adds supply of an item once the opening is over, besides an upgrade in place.
# The goal that gives more of an item the ledger finds short (fuel included: its burners' draw is a claim).
SUPPLY_GOALS = {"iron-plate": "iron-smelting-expansion", "copper-plate": "copper-smelting-start",
                "stone-brick": "brick-smelting", "coal": "coal-supply"}
ELECTRIC_SUPPLY_GOALS = {"iron-plate": "electric-iron-smelting", "copper-plate": "electric-copper-smelting",
                         "stone-brick": "electric-brick-smelting", "coal": "electric-coal-supply",
                         "stone": "electric-stone-supply"}


def upgrade_helps(site: Mapping[str, Any], item: str) -> bool:
    """Whether upgrading a block in place answers a shortage of `item`: a block making it runs flat out while one taking
    it starves (what the upgrade planner looks for). A shortage of what building takes is answered by more smelting,
    not this: offered for it anyway, the upgrade found no bottleneck and was asked again (run 34, 2026-10-25)."""
    try:
        book = ledger.Ledger.from_site(site)
    except Exception:   # (no ledger: nothing to say it helps)
        return False
    busy = any(p.share("working") >= ledger.BUSY for p in book.producers(item))
    starved = any(c.share("starved") >= ledger.STARVED for c in book.consumers(item))
    return busy and starved


# Needs that cannot wait for the plan: an ally asked for help, the base is short of power, a part of
# the base faces nests without defenses.
URGENT = ("ally-defense", "nuclear-power", "nuclear-fuel", "power-plant", "outpost-defense")


# Personality changes the opening: aggressive engineers research weapons first, turtles defenses (and
# build them right after their first power); the opening stays a default the strategist can leave.
AGGRESSIVE = {"aggressive", "evil", "pirate", "vengeful", "saboteur"}
TURTLE = {"fortifier", "protective", "cautious"}
# Survival first (a death world gives every team engineer this trait, mod/scripts/team_world.lua WORLD_TRAITS): it
# arms early like the aggressive, and its military share of research starts higher; the threat still decides when.
MILITANT = {"survivor"}
MILITANT_SHARE = 0.25           # added to the military share of research while it carries such a trait
MILITARY_FIRST = ("military", "gun-turret", "heavy-armor", "military-2")
# At war (the game says so while it is early in its war research): weapons and defenses first.
VEHICLE_FIRST = ("automobilism", "tank", "artillery")
WAR_FIRST = ("military", "gun-turret", "stone-wall", "steel-processing", "heavy-armor", "military-2",
             "physical-projectile-damage-1", "weapon-shooting-speed-1", "military-3")
DEFENSE_FIRST = ("gun-turret", "stone-wall")
# The team world (a death world): the team arms first, in this order, then any other weapon, turret,
# wall or ammunition research (the game marks those options `military`), then the rest.
# Automation first all the same: ten red packs for the assembling machine that makes red science without
# hand crafting (the user, 2026-10-24: "semi automated by 45 minutes"); the big starting area keeps the nests
# off that long, and turrets come right after it.
TEAM_RESEARCH = ("automation", "military", "gun-turret", "stone-wall", "military-2", "logistic-science-pack", "gate",
                 "military-science-pack", "physical-projectile-damage-1", "weapon-shooting-speed-1", "heavy-armor")
# The team's first research, before what its goals wait on: red science by machine, the first arms and walls,
# then green science; the rest of TEAM_RESEARCH is its military order, taken up as its military share comes due.
TEAM_BASICS = ("automation", "military", "gun-turret", "stone-wall", "logistic-science-pack")
TEAM_DEFENSE = ("gun-turret", "military", "stone-wall")   # attacked lately: these before anything else


_READY: dict[Any, list[str]] = {}   # npc id: the goals its planners could build when they last planned
_READY_LOCK = threading.Lock()


def note_ready(site: Mapping[str, Any], *, designer: bool) -> None:
    """Keep what the engineer could build now, for its strategist (who otherwise names goals not ready)."""
    try:
        ready = [p for p, spec in PURPOSES.items() if not spec.get("architect") and available(p, site, designer=designer)]
    except Exception:   # (never in the way of a plan)
        return
    with _READY_LOCK:
        _READY[site.get("npc_id")] = ready


def ready_for(npc_id: Any) -> list[str] | None:
    with _READY_LOCK:
        ready = _READY.get(npc_id)
    return list(ready) if ready is not None else None


def wanted_recipes(site: Mapping[str, Any] | None) -> set[str]:
    """Recipes its next goals wait on research for: what the opening's steps not done yet need that is still
    locked. (Empty before it has asked for a plan: nothing seen yet.)"""
    return set(wanted_ranks(site))


def wanted_order(site: Mapping[str, Any] | None) -> list[str]:
    """wanted_recipes, the soonest needed first."""
    ranks = wanted_ranks(site)
    return sorted(ranks, key=lambda n: ranks[n])


def wanted_ranks(site: Mapping[str, Any] | None) -> dict[str, int]:
    """wanted_recipes, each with the place of the first goal waiting on it among the opening's steps not done (0:
    the next): research goes to what the next goal waits on, not to any goal's (the electric mining drill waited
    behind arms and green science in run 35, 2026-10-25)."""
    if not isinstance(site, Mapping):
        return {}
    unlocked = ratios.unlocked(site)
    out: dict[str, int] = {}
    step = 0
    for purpose, finished in opening_order(site):
        spec = PURPOSES.get(purpose)
        if not spec:
            continue
        try:
            if finished(site):
                continue
        except Exception:   # (a step's check needs what this site does not carry: weigh it as not done)
            pass
        for n in spec.get("requires_recipes", ()):
            if n not in unlocked:
                out.setdefault(n, step)
        step += 1
    # (the science path waiting for a plate's bus lane waits on the bus: its research is the next goal's; logistics
    # came at minute 33 behind green science and steel, the bus at 56, opening-pace F-21)
    try:
        from companion import bus_planner, tier_builds
        if bus_planner.due_now(site) or tier_builds.lane_first(site, tier_builds.cached_pick(site)):
            for n in PURPOSES["bus-base"].get("requires_recipes", ()):
                if n not in unlocked:
                    out[n] = 0
    except Exception:   # (a judgment, never in the way of research)
        pass
    return out


# After the team's list: military research for a share of what it researches, the share growing as the biters
# evolve, and first while its base is being attacked (up to the evolved share); the rest goes on to the rocket.
# (Military first always, as the team world had it, would never reach space: military upgrades do not run out.)
MILITARY_SHARE = 0.25           # with the biters unevolved
MILITARY_SHARE_EVOLVED = 0.6    # with them fully evolved (and the most an attack lately raises it to)


# How much a pick is needed, for the team's research queue (team_research.lua sorts the engineers' picks by it, never
# past a player's): a research that finishes after the build that waits on it held the bus up (logistics at minute 33,
# queued behind green science and steel, normal-7d; R-9). Kept per request until the answer goes to the game.
RESEARCH_NEEDS: dict[str, dict[str, Any]] = {}
NEED_CLASSES = ("a", "b", "c", "d")
NEEDS_KEPT = 64


def turret_bonuses(options: Any) -> list[str]:
    """The offered military techs that unlock no recipe (bonuses only: damage, fire rate), least lab time first."""
    rows = [o for o in bp._as_list(options) if isinstance(o, Mapping) and o.get("military")
            and not bp._as_list(o.get("unlocks"))]
    rows.sort(key=lambda o: (float(o.get("lab") or 0) or float(o.get("units") or 0), str(o.get("name"))))
    return [str(o["name"]) for o in rows]


def research_need(tech: str, request: Mapping[str, Any], site: Mapping[str, Any] | None) -> dict[str, Any]:
    """The class of a pick: "a" a build waits on it now (what the next goal waits on, or arms when attacked lately),
    "b" a later goal waits on it (`step`: how many goals ahead), "c" the science tier and the rocket path, "d" the
    rest."""
    option = next((o for o in bp._as_list(request.get("options")) if isinstance(o, Mapping) and o.get("name") == tech),
                  {})
    # (arms under attack, a way through when walled off, at war: urgent whatever goal also waits on them)
    attacked = bool(request.get("team")) and military_due(request)[1] == "attacked lately"
    arms = tech in TEAM_DEFENSE or tech in turret_bonuses(request.get("options"))
    if (attacked and arms) or request.get("walled") or request.get("war"):
        return {"class": "a"}
    rank = wanted_ranks(site)
    steps = [rank[n] for n in bp._as_list(option.get("unlocks")) if n in rank]
    if steps:
        step = min(steps)
        return {"class": "a" if step == 0 else "b", "step": step}
    unlocks = bp._as_list(option.get("unlocks"))
    if option.get("rocket_path") or any(str(u).endswith("science-pack") for u in unlocks):
        return {"class": "c"}
    return {"class": "d"}


# What a pick must serve, or the labs wait (R-10: with green science late, red packs went on Bob's inserters, lamps, a
# flare stack, the steel axe and Factorissimo's buildings in normal-7e, none of which the engineers ever use). A tech
# serves when it is on the road to space, the opening's, military or survival, unlocks a science pack, leads to a recipe
# a goal, the mall or a wanted step needs (its own unlocks or those of the techs after it), or gives a bonus to what the
# factory runs. (Bonuses to the engineer's own body, and the empty effects of cosmetic upgrades, serve nothing.)
IDLE = ""   # the answer when nothing offered serves: no research for now
RESEARCH_UNUSED: dict[str, list[str]] = {}   # request id: the offered techs judged unused, told to the game with IDLE
# What the engineer's own code uses up besides its goals' buildings (obstacles.lua's landfill, the drives' car and tank,
# repairs' packs, its paths' bricks)
ENGINEER_SUPPLIES = ("landfill", "car", "tank", "repair-pack", "stone-brick")
USED_BONUSES = frozenset({
    "turret-attack", "gun-speed", "ammo-damage", "artillery-range", "mining-drill-productivity-bonus",
    "inserter-stack-size-bonus", "bulk-inserter-capacity-bonus", "belt-stack-size-bonus", "laboratory-speed",
    "laboratory-productivity", "change-recipe-productivity", "worker-robot-speed", "worker-robot-storage",
    "worker-robot-battery", "maximum-following-robots-count", "follower-robot-lifetime", "train-braking-force-bonus"})


def catalog_recipes() -> frozenset[str]:
    """Every recipe the engineer's goals or its mall build with: what its research can be for."""
    out = {str(n) for spec in PURPOSES.values() for n in spec.get("requires_recipes", ())}
    try:
        from companion import mall
        out.update(mall.FLAT)
    except Exception:   # (the mall's list, never in the way of research)
        pass
    return frozenset(out | set(ENGINEER_SUPPLIES))


def named_techs() -> frozenset[str]:
    """The techs the chooser's own lists name (the team's order, walled off, at war, by personality)."""
    return frozenset(TEAM_RESEARCH) | set(TEAM_BASICS) | set(TEAM_DEFENSE) | set(VEHICLE_FIRST) | set(WAR_FIRST)         | set(MILITARY_FIRST) | set(DEFENSE_FIRST)


def research_used(option: Mapping[str, Any], site: Mapping[str, Any] | None) -> bool:
    """Whether an offered tech serves anything the engineer builds or runs (IDLE above)."""
    if option.get("rocket_path") or option.get("early") or option.get("military") or option.get("survival")             or option.get("leads_armed") or option.get("leads_cut") or option.get("name") in named_techs():
        return True
    unlocks = {str(u) for u in bp._as_list(option.get("unlocks"))}
    if any(u.endswith("science-pack") for u in unlocks):
        return True
    leads = unlocks | {str(u) for u in bp._as_list(option.get("leads"))}
    if leads & (catalog_recipes() | set(wanted_ranks(site))):
        return True
    bonuses = bp._as_list(option.get("bonuses")) + bp._as_list(option.get("lead_bonuses"))
    return bool({str(b) for b in bonuses} & USED_BONUSES)


def military_due(request: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the team's next research should be military, and why (from what it has researched, how far
    the biters have evolved and whether its base was attacked lately)."""
    tally = request.get("researched") if isinstance(request.get("researched"), Mapping) else {}
    military, onward = int(tally.get("military") or 0), int(tally.get("rocket_path") or 0)
    board = request.get("threat_board") if isinstance(request.get("threat_board"), Mapping) else {}
    evolution = min(1.0, max(0.0, float(board.get("evolution") or 0)))
    share = MILITARY_SHARE + (MILITARY_SHARE_EVOLVED - MILITARY_SHARE) * evolution
    traits = {str(t) for t in bp._as_list((request.get("personality") or {}).get("traits"))}
    if traits & MILITANT:   # (a prior, kept below what the biters at their worst ask for)
        share = min(MILITARY_SHARE_EVOLVED, share + MILITANT_SHARE)
        if military == 0:
            return True, "survival first: no military research yet"
    done = military + onward
    if threatened(request) and military < MILITARY_SHARE_EVOLVED * done:
        return True, "attacked lately"
    if military < share * done:
        return True, f"{military} of {done} researched so far, {share:.0%} due at evolution {evolution:.2f}"
    return False, ""


def team_opening(site: Mapping[str, Any]) -> list:
    """The team world's opening. A death world, but the big starting area keeps the nests far off at first,
    so power and a lab come first, as a player builds them (turrets before power held every engineer in
    the burner phase for the whole first half hour of the live game, 2026-10-22): then a few starter
    turrets by the factory's vital buildings once its iron runs; once its smelting is electric (red science
    runs by machine before that), walls made by machines, the wall line from them once enough are stocked, and
    turrets along it (the starter turrets moved there); more turrets with green science and the rest of the wall once its
    mining is electric. An attack or a breach brings walls and turrets at once, by hand. (Turrets straight
    after the lab waited on the two starter iron furnaces from minute 25 to past 38 in the one-engineer
    hour, 2026-10-24: they cost iron, so the iron comes first.)"""
    from companion import perimeter, wall_block

    def built(purpose: str, n: int):
        return lambda s: len(perimeter_blocks(s, purpose)) >= n

    def once(purpose: str):
        return lambda s: purpose in built_purposes(s)
    out, guarded, walled = [], False, False
    for step in OPENING:
        out.append(step)
        if step[0] == "iron-smelting-expansion" and not guarded:
            # defense where the base is pressed, once the iron runs (turrets cost iron): its pollution and
            # the attacks on it decide where and how many (companion/defense_brain.py); taken up again
            # whenever a part of the base no longer holds
            guarded = True
            out.append(("reinforce", lambda s: not brain_available(s)))
        elif step[0] == "electric-copper-smelting" and not walled:
            # walls by machine, the wall line from them, then turrets along it (the user, 2026-10-24), once the
            # smelting is electric: they cost iron (red science comes earlier now, right after the first lab)
            walled = True
            out.append(("electric-brick-smelting", once("electric-brick-smelting")))
            out.append((wall_block.BRICKS, lambda s: has_built(wall_block.BRICKS, built_purposes(s))))
            out.append((wall_block.PURPOSE, once(wall_block.PURPOSE)))
            out.append((perimeter.WALL, built(perimeter.WALL, 1)))
            out.append((perimeter.TURRETS, built(perimeter.TURRETS, 1)))
        elif step[0] == "green-science-automation":
            out.append((perimeter.TURRETS, built(perimeter.TURRETS, 2)))
        elif step[0] == "electric-copper-mining":
            out.append((perimeter.WALL, perimeter.walled))
    return out


def team_urgent(site: Mapping[str, Any]) -> str | None:
    """In the team world: a breach is mended at once, and turrets go up at once whenever the plot is
    attacked (otherwise they come in the opening, after power and a lab)."""
    from companion import perimeter
    if not team_area.team_of(site):
        return None
    from companion import threat
    if perimeter_available(perimeter.BREACH, site):
        return perimeter.BREACH
    # (turrets on its ring with no arm from its ammo loop, and no attack now: their arms first, a few inserters; under
    # steady waves the hand loads took near a quarter of its time and held every other goal, D-19's proving runs)
    from companion import ammo_line
    if not site.get("under_attack") and available("ammo-line", site) and ammo_line.what_is_due(site) == "arms":
        return "ammo-line"
    # (and the loop itself, no attack on now, before more reinforce: begun and carried on round its ring, or due (its
    # turrets drawn on, or iron to spare for it); what the turrets reinforce adds is fed by it. Behind reinforce and the
    # economy's weighed goals it never came: late-combat's turrets were fed by hand for 20 minutes, its first side never
    # chosen under waves (D-24, the coordinator; Codex's review)
    if not site.get("under_attack") and available("ammo-line", site) and ammo_line.what_is_due(site) == "loop":
        return "ammo-line"
    # (attacked with no wall standing: at once, the user's rule, 2026-10-26; between waves too, not only while the
    # threat is high: the turrets the pressure wants, then walls by hand on the side they hit, D-11)
    pressed = threatened(site) or threat.unwalled_hit(site)
    if pressed and brain_available(site):   # (where it is pressed: the part they hit, on their side)
        return "reinforce"
    if threat.unwalled_hit(site) and perimeter_available(perimeter.WALL, site):
        return perimeter.WALL
    if perimeter_available(perimeter.TURRETS, site) and threatened(site):
        return perimeter.TURRETS
    return None


# What clearing ground never goes ahead of, once the opening is over: defending (its plot's edge, outposts,
# an ally), the team's arms and short power.
HOLDS_CLEARING = ("perimeter", "starter", "outposts", "ally_help", "military", "power_plant", "defense_production")


def clearing_due(site: Mapping[str, Any], *, designer: bool = False) -> Mapping[str, Any] | None:
    """A zone to clear before any new goal (companion/clearing.py: its section comes next), or None: never
    while something defensive or military can be built (survival first in the team world), nor after an
    attack lately."""
    from companion import clearing
    if threatened(site) or not available(clearing.PURPOSE, site, designer=designer):
        return None
    if any(available(p, site, designer=designer) for p, spec in PURPOSES.items()
           if any(spec.get(k) for k in HOLDS_CLEARING)):
        return None
    return clearing.due(site)


def opening_order(site: Mapping[str, Any]) -> list:
    if team_area.team_of(site):
        return team_opening(site)
    traits = {str(t) for t in bp._as_list((site.get("personality") or {}).get("traits"))}
    if not traits & TURTLE:
        return OPENING
    at = next(i for i, (p, _) in enumerate(OPENING) if p == "steam-power") + 1
    return OPENING[:at] + [("basic-defense", lambda s: "basic-defense" in built_purposes(s))] + OPENING[at:]


def opening_goal(site: Mapping[str, Any], *, designer: bool = False) -> str | None:
    """The next step of the opening that is not done yet and can be built here, or None once it is over.
    Once the engineer's bus has begun, what the bus replaces (burner smelting, the first science lines)
    is not built again: the bus retires those builds, and counting them as missing would bring them back."""
    from companion import bus_planner
    replaced = set(bus_planner.SUPERSEDED) | set(bus_planner.SCIENCE_LINES.values())         if (site.get("base_plan") or {}).get("stages") else set()
    for purpose, done in opening_order(site):
        if purpose in replaced:
            continue
        if not done(site) and available(purpose, site, designer=designer):
            return purpose
    return None


# Prototype types that do a goal's work (not what links them up: belts, inserters, poles, pipes, chests).
WORKERS = {"assembling-machine", "furnace", "lab", "mining-drill", "boiler", "generator", "offshore-pump",
           "ammo-turret", "electric-turret", "fluid-turret", "rocket-silo", "reactor", "radar", "roboport",
           "solar-panel", "accumulator", "burner-generator"}


def off_the_job(plan: Mapping[str, Any], site: Mapping[str, Any], purpose: str) -> list[str]:
    """The machines a goal's required recipes name (from the site's prototypes) when a plan places none of
    them: a plan for some other job. Empty when it places one, or the goal names none. (One-engineer hour
    2026-10-24: asked for a research lab before red science was unlocked, the designer built one iron
    furnace instead; the lab, never crafted, never unlocked red science, and nothing was researched.)"""
    spec = PURPOSES.get(purpose) or {}
    prototypes = site.get("prototypes") or {}
    workers = [n for n in spec.get("requires_recipes") or ()
               if isinstance(prototypes.get(n), Mapping) and prototypes[n].get("type") in WORKERS]
    placed = {e.get("prototype") for e in bp._as_list(plan.get("entities")) if isinstance(e, Mapping)}
    return [] if not workers or placed & set(workers) else workers


def opening_steps(site: Mapping[str, Any], *, designer: bool = False) -> list[str]:
    """The opening's steps not done yet that can be built now, in its order (the goal weighing's prior)."""
    from companion import bus_planner
    replaced = set(bus_planner.SUPERSEDED) | set(bus_planner.SCIENCE_LINES.values()) \
        if (site.get("base_plan") or {}).get("stages") else set()
    out = []
    for purpose, done in opening_order(site):
        if purpose in replaced or purpose in out:
            continue
        if not done(site) and available(purpose, site, designer=designer):
            out.append(purpose)
    return out


def _guards(purpose: str) -> bool:
    from companion import goal_brain
    return goal_brain.guards(purpose)


def why_unavailable(purpose: str, site: Mapping[str, Any], *, designer: bool = False) -> str | None:
    """Why an explicitly requested goal (e.g. by the strategist) cannot be built now, or None."""
    from companion import failures
    stuck = failures.held(site, purpose) if purpose in PURPOSES else None
    if stuck:
        return f"failed here lately ({stuck['reason'][:80]}); held until something changes that could mend it"
    if purpose not in PURPOSES or purpose == "ally-defense" or available(purpose, site, designer=designer):
        return None
    spec = PURPOSES[purpose]
    recipes = ratios.unlocked(site)
    missing = [n for n in spec.get("requires_recipes", []) if n not in recipes]
    if spec.get("requires_any") and not any(n in recipes for n in spec["requires_any"]):
        missing.append(" or ".join(spec["requires_any"]))
    if missing:
        return "needs research first: " + ", ".join(missing)
    done = built_purposes(site)
    before = [n for n in spec.get("requires_built", []) if not has_built(n, done)]
    if before:
        return "build first: " + ", ".join(before)
    if purpose in set(bp._as_list(site.get("failed_purposes"))):
        return "failed here recently"
    if spec.get("nuclear") or spec.get("nuclear_fuel"):
        if has_built(purpose, done):
            return "built already"
        from companion import power_outlook
        o = power_outlook.outlook(site)
        return ("power has room: " + (f"{(o['draw_kw'] + o['planned_kw']) / 1000:.1f} MW drawn ahead of "
                                      f"{o['capacity_kw'] / 1000:.1f} MW" if o else "no generator counted"))
    if spec.get("power_plant"):
        return "power is not short"
    if spec.get("clearing"):
        from companion import clearing
        if clearing.zone_for(site) is None:
            return "no ground waiting to be cleared"
        return "the opening comes first"
    if spec.get("team") and not team_area.team_of(site):
        return "only in the team world"
    if spec.get("perimeter"):
        if not team_area.team_of(site):
            return "only in the team world"
        if purpose == "plot-wall":
            return "not enough walls stocked yet for the next stretch (they are made by machine), and no attack"
        if purpose == "plot-turrets":
            return "the wall ring is not started yet or has its turrets, and no attack"
        return "nothing to hold or mend on the wall ring now (or no turret or wall it can make yet)"
    if spec.get("robot_network"):
        from companion import robo_grid
        return robo_grid.due(site)[1]
    if spec.get("robot_supply"):
        from companion import robo_grid
        return robo_grid.supply_due(site)[1]
    if spec.get("starter"):
        return "already built, turrets stand on the wall line, or no factory to guard yet"
    if spec.get("brain"):
        return "every part of the base holds against what presses on it (or no turret it can make yet)"
    if spec.get("ammo_line"):
        return "no ring with turrets wanting its ammo loop or their arms, or no power, inserters or belts yet"
    if spec.get("outposts"):
        return "no part of the base faces nests without defenses"
    if spec.get("resource") and spec["resource"] not in known_resources(site):
        return f"no known {spec['resource']}"
    if spec.get("suggested") and purpose in done:
        return "already built"
    return "not useful here now"


def purpose_options(site: Mapping[str, Any], *, designer: bool = False) -> list[dict]:
    done = built_purposes(site)
    options = []
    for purpose, spec in PURPOSES.items():
        if spec.get("clearing"):
            continue   # (cleared by rule: when it is due, or when nothing else is to be built)
        if available(purpose, site, designer=designer):
            if unfinished(purpose, site):
                from companion import bus_planner
                saved = site.get("base_plan") or {}
                left = bus_planner.remaining(site)
                if left:
                    label = (f"Continue the main bus: next stage {left[0]} "
                             f"({len(saved.get('stages') or []) - len(left)} of {len(saved.get('stages') or [])} built).")
                else:
                    label = ("Extend the finished main bus for " + ", ".join(bus_planner.extendable(site))
                             + ": new lanes, modules and labs below it.")
            else:
                label = spec["label"] + (" (already built once)" if purpose in done else "")
            options.append({"id": purpose, "description": label})
    options.append({"id": CONTINUE_PROJECTS, "description": "Nothing new for now: continue the planned "
                    "research and power projects."})
    return options


class BuiltIn:
    """The job's built-in layout for this ground, worked out once: the weighing measures what the job
    needs by it, and it is built when nothing saved serves. Its failure is kept for when it is built."""

    def __init__(self, fallback: Callable, site: Mapping[str, Any]):
        self.fallback, self.site = fallback, site
        self._plan: dict | None = None
        self._error: Exception | None = None
        self._done = False

    def plan(self) -> dict | None:
        if not self._done:
            self._done = True
            try:
                self._plan = self.fallback(self.site)
            except Exception as exc:   # (raised again only if it is the one built)
                self._error = exc
        return self._plan

    def build(self) -> dict:
        plan = self.plan()
        if plan is None:
            raise self._error or layout_planner.PlanError("no built-in layout")
        return plan


class CatalogPlanner(PlanKeeper):
    def __init__(self, catalog: bp.Catalog, *, designer: Callable | None = None,
                 chooser: DecisionsChooser | None = None, fallback: Callable = layout_planner.plan,
                 log: Callable[[str], None] = print, seed: int = 0, architect: Callable | None = None):
        """`architect`: plans the factory's large sections (architect.Architect); None: no factory plan."""
        self.catalog, self.designer, self.chooser, self.fallback = catalog, designer, chooser, fallback
        if catalog is not None:   # (the science path looks in the same library for imported science blocks, R-5b)
            from companion import science_blocks
            science_blocks.use_catalog(catalog)
        self.architect = architect
        self.log, self.seed = log, seed
        self.assignments: dict[str, dict] = {}
        self.tried_sites: dict[tuple, set] = {}  # (npc, purpose) -> offsets already planned around
        self.held_scouts: dict[tuple, int] = {}  # (npc, purpose) -> trips made while all it knew was not its ground
        self.world: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None  # wide map, set by the bridge
        self.modules = None   # module library for the bus (default: the user's, on first use)

    def _sample(self, request_id: str, probabilities: Mapping[str, float], picked: str) -> str:
        # Sample for variety between NPCs, but never a long shot: drop options under 10% of the best.
        best = max(probabilities.values(), default=0)
        probabilities = {k: p for k, p in probabilities.items() if p >= 0.1 * best}
        total = sum(p for p in probabilities.values() if p > 0)
        if total <= 0:
            return picked
        digest = hashlib.sha256(f"{self.seed}:{request_id}".encode()).digest()
        draw = random.Random(int.from_bytes(digest[:8], "big")).random() * total
        for option, p in sorted(probabilities.items()):
            draw -= max(p, 0)
            if draw <= 0:
                return option
        return picked

    def choose_purpose(self, site: Mapping[str, Any]) -> str:
        try:   # (what the science path passes over, and why: said when it changes, never dropped silently)
            from companion import tier_builds
            passed = tier_builds.news(site)
            if passed:
                self.log(f"{site.get('request_id')}: the science path passes over: " + "; ".join(passed[:4]))
            from companion import space_path   # (and what the way to space wants that no goal can be offered for)
            unmet = space_path.news(site, designer=self.designer is not None)
            if unmet:
                self.log(f"{site.get('request_id')}: the way to space waits: " + "; ".join(unmet[:4]))
        except Exception:
            pass
        # (a plan handed back over its hand work: what makes its items by machine first, unless attacked now or a
        # breach is open; F-11)
        from companion import perimeter, plan_hold
        try:
            breach = bool(team_area.team_of(site)) and perimeter_available(perimeter.BREACH, site)
            maker, why = (None, "") if breach else plan_hold.after(site, designer=self.designer is not None)
        except Exception as exc:   # (a judgment, never in the way)
            maker, why = None, f"hand-back failed ({str(exc)[:120]})"
        self._back_to_defense_supply(site, maker, breach)
        if why:
            self.log(f"{site.get('request_id')}: " + (f"next goal {maker} ({why})" if maker else why))
        if maker:
            self._reconsidered(site, maker)
            return maker
        try:   # (what is made by hand over and over: its machine between rounds, before holding the edge; W-23)
            again = None if breach else plan_hold.repeated(site, designer=self.designer is not None)
        except Exception as exc:
            again = None
            self.log(f"{site.get('request_id')}: hand-made check failed ({str(exc)[:120]})")
        if again:
            goal, items, minutes = again
            self.log(f"{site.get('request_id')}: next goal {goal} ({', '.join(items[:3])} made by hand for "
                     f"{minutes:.0f} min across its plans: what makes them by machine, between waves)")
            self._reconsidered(site, goal)
            return goal
        urgent = team_urgent(site)
        if urgent:
            self.log(f"{site.get('request_id')}: next goal {urgent} (holding the plot's edge)")
            self._reconsidered(site, urgent)
            return urgent
        for urgent in URGENT:   # these come before anything planned, the opening too
            if PURPOSES[urgent].get("nuclear") and not site.get("power_short"):
                continue   # (power due but not short: after defense, below; its fuel stays urgent once the plant
                #            stands, power short or not: a plant without fuel gives nothing)
            if available(urgent, site, designer=self.designer is not None):
                self.log(f"{site.get('request_id')}: next goal {urgent} (urgent)")
                self._reconsidered(site, urgent)
                return urgent
        if threatened(site) and available("reinforce", site, designer=self.designer is not None):
            self.log(f"{site.get('request_id')}: next goal reinforce (attacked lately: where it is pressed)")
            self._reconsidered(site, "reinforce")
            return "reinforce"
        supply = self._defense_production_urgent(site)
        if supply:   # (the waves grow and its defense supply is far short: after placing defenses, before the rest)
            return supply
        from companion import ammo_line
        if available("ammo-line", site, designer=self.designer is not None)                 and ammo_line.what_is_due(site, pressed_only=True):
            # (its loop's arms for turrets added since, its chest linked to the row, or the loop itself once the waves
            # draw on the ring's turrets (ammo_line.what_is_due: not before), before the economy: the loop waited behind
            # the weighed goals, which an opening always has, and death-world-waves-ring was overrun at wave 4 with
            # none of its 21 turrets fed (D-24; D-22 had moved it there after stage-silo's 12 minutes of loop sides,
            # laid then for full turrets, before the drawn gate)
            kind = ammo_line.what_is_due(site)
            self.log(f"{site.get('request_id')}: next goal ammo-line ("
                     + {"loop": "the ring's turrets loaded by hand: the ammo loop round it",
                        "arms": "turrets added to the ring: their arms from its loop",
                        "link": "the loop's chest filled by hand, its magazine row standing"}.get(kind, str(kind))
                     + ")")
            self._reconsidered(site, "ammo-line")
            return "ammo-line"
        if site.get("reconsider") and site.get("paused"):
            # (asked because its base was hit while it built: nothing urgent, so back to the build)
            self._reconsidered(site, None)
            raise Defer(f"attacked, but nothing urgent to build; back to {site['paused']}", 60)
        from companion import goal_brain
        designer = self.designer is not None
        # what its strategist asked for: this request's, else one passed over lately and still open
        asked = next((a for a in (site.get("asked"), *goal_brain.asks(site)) if a in PURPOSES
                      and available(a, site, designer=designer)), None)
        if asked:
            guard = goal_brain.guard_of(site)
            first = next((s for s in opening_steps(site, designer=designer) if goal_brain.guards(s)),
                         None) if guard.urgent else None
            if first:   # (attacked with no wall standing, or threatened: its defenses before what was asked)
                self.log(f"{site.get('request_id')}: next goal {first} (asked for {asked}, but its defenses first: "
                         f"guard {guard.value:.2f})")
                return first
        steps = opening_steps(site, designer=designer)
        held = steps and (goal_brain.defense(steps[0]) or goal_brain.guards(steps[0]))
        first = None if held else first_source_due(site, designer=designer)
        if first:   # (defense in its turn first: the opening's defense and guarding steps are not passed over)
            self.log(f"{site.get('request_id')}: next goal {first} (researched, and nothing makes "
                     f"{first_source_item(first)} yet)" + self._tier_note(site, first))
            return first
        # New power before what would draw it: due (the draw ahead, what stands and the plan's next sections, past
        # most of what the generators give: companion/power_outlook.py) though not short yet (short, it was urgent
        # above). Ahead of the weighing and the plan's next section: the user plays ahead, never underproducing.
        from companion import power_outlook
        for purpose, spec in PURPOSES.items():
            if spec.get("nuclear") and not held and available(purpose, site, designer=designer):
                o = power_outlook.outlook(site) or {}
                self.log(f"{site.get('request_id')}: next goal {purpose} (power due: "
                         f"{(o.get('draw_kw', 0) + o.get('planned_kw', 0)) / 1000:.1f} MW drawn ahead of "
                         f"{o.get('capacity_kw', 0) / 1000:.1f} MW)")
                self._reconsidered(site, purpose)
                return purpose
        weighed = self._weighed_goal(site)
        if weighed:
            return weighed
        if asked and not opening_steps(site, designer=designer):   # (the opening over: what its strategist asked for)
            self.log(f"{site.get('request_id')}: next goal {asked} (asked for)")
            return asked
        opening = opening_goal(site, designer=self.designer is not None)   # (the order, when nothing is weighed)
        if opening:
            self.log(f"{site.get('request_id')}: next goal {opening} (opening)")
            return opening
        if available("reinforce", site, designer=self.designer is not None):
            self.log(f"{site.get('request_id')}: next goal reinforce (a part of the base does not hold)")
            return "reinforce"
        if available("ammo-line", site, designer=self.designer is not None):
            self.log(f"{site.get('request_id')}: next goal ammo-line (the ring's turrets loaded by hand: its ammo loop)")
            return "ammo-line"
        if available("military-science-automation", site, designer=self.designer is not None):
            self.log(f"{site.get('request_id')}: next goal military-science-automation (the team arms first)"
                     + self._tier_note(site, "military-science-automation"))
            return "military-science-automation"
        from companion import space_path   # (the way to space's next step, after the opening: R-3)
        ahead = space_path.goals(site, designer=self.designer is not None)
        if ahead:
            goal, item, why = ahead[0]
            self.log(f"{site.get('request_id')}: next goal {goal} (the way to space: {item}, {why})")
            return goal
        supply = self.supply_first(site)
        if supply:
            goal, budget = supply
            self.log(f"{site.get('request_id')}: next goal {goal} (the ledger: {budget.line()})")
            return goal
        robots = self._worth_weighing(site)   # (after the ledger's shortfalls: the robot network once it pays, SO-6)
        if robots:
            return robots
        planned = self.planned_goal(site)   # the factory plan's next section
        if planned:
            return planned
        zone = clearing_due(site, designer=self.designer is not None)
        if zone:
            from companion import clearing
            self.log(f"{site.get('request_id')}: next goal {clearing.PURPOSE} (its section is next: "
                     f"{clearing.describe(zone)})")
            return clearing.PURPOSE
        options = purpose_options(site, designer=self.designer is not None)
        if self.chooser and len(options) > 1:
            try:
                picked, probabilities = self.chooser.choose(site, options, question="goal")
                choice = self._sample(str(site.get("request_id")) + ":goal", probabilities, picked)
                top = sorted(probabilities.items(), key=lambda kv: -kv[1])[:4]
                self.log(f"{site.get('request_id')}: next goal {choice} (system one; "
                         + ", ".join(f"{k} {v:.0%}" for k, v in top) + ")")
                return self._or_clearing(site, choice)
            except Exception as exc:
                self.log(f"{site.get('request_id')}: goal chooser failed ({exc}); using the default order")
        done = built_purposes(site)
        for option in options:
            if option["id"] != CONTINUE_PROJECTS and (option["id"] not in done or unfinished(option["id"], site)):
                return option["id"]
        return self._or_clearing(site, CONTINUE_PROJECTS)

    def _back_to_defense_supply(self, site: Mapping[str, Any], maker: str | None = None,
                                breach: bool | None = None) -> None:
        """What makes its defense items, set aside in a wave (its reconsider, or a defense its strategist asked for
        now): that is its defense, so back to it (Defer) once the wave is over, with no breach open; else the row never
        stood under steady waves, the edge held by hand again, or a second row was planned beside it (W-23, the proving
        ground's death-world-waves-power)."""
        from companion import perimeter, plan_hold
        paused = site.get("paused")
        if not paused or site.get("under_attack"):
            return
        if breach is None:
            breach = bool(team_area.team_of(site)) and perimeter_available(perimeter.BREACH, site)
        if breach:
            return
        try:
            again = plan_hold.repeated(site, designer=self.designer is not None)
            ours = plan_hold.supplies_defense(paused) or paused in (maker, again and again[0])
        except Exception:
            ours = plan_hold.supplies_defense(paused)
        if ours:
            self._reconsidered(site, None)
            raise Defer(f"attacked while it builds {paused}, what makes its defense: back to it", 60)

    def _defense_production_goals(self, site: Mapping[str, Any]) -> list[str]:
        """The defense supply's goal now: its row, or the wall machinery's next goal (walls short), if buildable."""
        from companion import defense_production
        designer = self.designer is not None
        if not (team_area.team_of(site) or threatened(site) or goal_militant(site)):
            return []
        got = defense_production.cached_pick(site, lambda g: available(g, site, designer=designer))
        if got is None or not available(got.goal, site, designer=designer):
            return []
        return [got.goal]

    def _defense_production_urgent(self, site: Mapping[str, Any]) -> str | None:
        """The defense supply's goal when the waves grow and it is far short (defense_production.urgent)."""
        from companion import defense_production
        try:
            if not defense_production.urgent(site):
                return None
            goals = self._defense_production_goals(site)
        except Exception as exc:   # (a judgment, never in the way)
            self.log(f"{site.get('request_id')}: defense supply failed ({str(exc)[:120]})")
            return None
        if not goals:
            # (what the supply waits on first: a build it needs before it, power, when that can be built; a death world
            # held the engineer in hand-made defense for an hour without power, so no machine could make any of it)
            spec = PURPOSES[defense_production.GOAL]
            done = built_purposes(site)
            designer = self.designer is not None
            first = next((n for n in spec["requires_built"] if not has_built(n, done)
                          and available(n, site, designer=designer)), None)
            if first and defense_production.draw(site).short():
                self.log(f"{site.get('request_id')}: next goal {first} (the waves grow and nothing makes its defense "
                         f"items by machine: {first} first)")
                return first
            return None
        got = defense_production.cached_pick(site)
        self.log(f"{site.get('request_id')}: next goal {goals[0]} (the waves grow and its defense supply is short: "
                 f"{got.need.line() if got else '?'})")
        return goals[0]

    def _tier_note(self, site: Mapping[str, Any], goal: str) -> str:
        """What the science path asks of `goal`, for the log: the build and the due items passed over, and why."""
        if goal not in ("science-tier", "military-science-automation"):
            return ""
        from companion import tier_builds
        try:
            build = (tier_builds.cached_pick(site) if goal == "science-tier"
                     else tier_builds.build_for(site, "military-science-pack"))
        except Exception as exc:
            return f" (the science path: {str(exc)[:80]})"
        if build is None:
            return ""
        passed = f"; passed over: {'; '.join(build.skipped[:3])}" if build.skipped else ""
        return f" (the science path: {build.line()}{passed})"

    def _worth_weighing(self, site: Mapping[str, Any]) -> str | None:
        """After the opening, a goal that only the weighing values (goal_brain.ALWAYS_WEIGHED) once its score clears
        goal_brain.WEIGHED_BAR: what it saves the engineer against what it costs."""
        from companion import goal_brain
        designer = self.designer is not None
        ready = [p for p in goal_brain.ALWAYS_WEIGHED if available(p, site, designer=designer)]
        if not ready:
            return None
        try:
            rows = goal_brain.weigh(site, ready)
        except Exception as exc:   # (a judgment, never in the way)
            self.log(f"{site.get('request_id')}: weighing {', '.join(ready)} failed ({str(exc)[:100]})")
            return None
        best = rows[0] if rows else None
        if not best or best["score"] < goal_brain.WEIGHED_BAR:
            return None
        self.log(f"{site.get('request_id')}: next goal {best['goal']} (weighed {best['score']:.2f}: saves "
                 f"{best['hand_saved']:.2f} of hand work)")
        return best["goal"]

    def _reconsidered(self, site: Mapping[str, Any], goal: str | None) -> None:
        """A build set aside because its base was hit: what came of it, written down (decisions/threat.jsonl)."""
        if not site.get("reconsider"):
            return
        try:
            from companion import threat
            threat.record(site, threat.estimate(site), {"reconsider": site.get("paused"), "chose": goal or "back"})
        except Exception:
            pass

    def _weighed_goal(self, site: Mapping[str, Any]) -> str | None:
        """While the opening lasts, the step the weighing picks (companion/goal_brain.py) among its open steps
        (its order the prior) and the ledger's shortfall, rather than its first open step. (After it, the
        factory plan, clearing and System One choose as before, until the weighing has earned that too.)"""
        from companion import goal_brain
        self._lead_goal = None   # (the plates' lead's goal boosted this request: said when it wins)
        designer = self.designer is not None
        try:
            steps = opening_steps(site, designer=designer)
            if not steps:
                return None
            if goal_brain.defense(steps[0]):   # (defense in its turn: the defense judgment decided it)
                self.log(f"{site.get('request_id')}: next goal {steps[0]} (opening: defense)")
                return steps[0]
            candidates = list(steps)
            supply = self.supply_first(site)
            if supply:
                candidates.append(supply[0])
            # (a project under way, its next stage: the bus stood half built while the opening's leftovers came
            # first, run 27, 2026-10-25)
            candidates += [p for p, spec in PURPOSES.items() if spec.get("bus") and p not in candidates
                           and p in built_purposes(site) and unfinished(p, site) and available(p, site, designer=designer)]
            # (what its strategist asked for, weighed with ASK_PRIOR; and coal makers when its burners would be short:
            # coal grows ahead of the burners it feeds)
            asked = [a for a in dict.fromkeys([site.get("asked"), *goal_brain.asks(site)])
                     if a in PURPOSES and available(a, site, designer=designer)]
            # (the plates' lead, income first: weighed as if asked for, F-14; its pairs went unbuilt at 1.5 against
            # the opening's 2-3 while iron stayed at 30 a minute, fresh run 61)
            # (once the array's own prerequisites stand: before power, the opening's power and lab come first, as the
            # player's steam at 6.6; boosted at minute 5 of fresh run 62, a 13-minute burner plan put steam at 18.5)
            if supply and isinstance(supply[1], plate_lead.Lead) and plate_lead.ready_for_arrays(site):
                if supply[0] not in asked:
                    asked.append(supply[0])
                self._lead_goal = (supply[0], supply[1])
            candidates += [a for a in asked if a not in candidates]
            # (its defense supply, a guarding goal: the row, or the wall machinery's next goal when walls are short)
            for g in self._defense_production_goals(site):
                if g not in candidates:
                    candidates.append(g)
            # (what the science path says is due, weighed with the rest: its tier signals, not an override)
            if "science-tier" not in candidates and available("science-tier", site, designer=designer):
                candidates.append("science-tier")
            # (goals no step of the opening names, weighed whenever they can be built: the robot network, SO-6)
            if not supply:   # (never beside a shortfall the ledger raised: its plates first)
                candidates += [p for p in goal_brain.ALWAYS_WEIGHED if p not in candidates
                               and available(p, site, designer=designer)]
            # (and the goals that make what its own goal cannot: oil for plastic and sulfur, a goal's prerequisite;
            # weighed as asked for by the science path, R-3: a stage's oil was never offered against the bus)
            from companion import space_path
            ahead = space_path.goals(site, designer=designer)
            for g, _item, _why in ahead:
                if g not in candidates:
                    candidates.append(g)
                if g not in asked:
                    asked.append(g)
            # (the nearest is the plan's next step, first in the order: a goal with no layout yet has no value the
            # weighing can read, and the bus under way outweighed oil 2.7 to 1.0 in proving stage-oil)
            # (the science path waiting for a plate's bus lane: the bus asked for, its lane first; LIVE-7c, F-20 B)
            from companion import tier_builds
            if tier_builds.lane_first(site, tier_builds.cached_pick(site)) and "bus-base" not in asked \
                    and available("bus-base", site, designer=designer):
                asked.append("bus-base")
                if "bus-base" not in candidates:
                    candidates.append("bus-base")
            order = [ahead[0][0]] + [p for p in steps if p != ahead[0][0]] if ahead else steps
            self._space_note = f" (the way to space: {ahead[0][0]} for {ahead[0][1]})" if ahead else ""
            picked = goal_brain.choose(site, candidates, opening=order, asked=asked,
                                       planned=ahead[0][0] if ahead else None,
                                       fuel=lambda p: available(p, site, designer=designer))
        except Exception as exc:   # (a judgment, never in the way of a plan: the order then)
            self.log(f"{site.get('request_id')}: goal weighing failed ({str(exc)[:120]}); the opening's order")
            return None
        if not picked:
            return None
        goal, rows = picked
        first = " (its coal first)" if rows and goal != rows[0]["goal"] and rows[0].get("coal_mined") else ""
        lead = getattr(self, "_lead_goal", None)
        note = f" ({lead[1].line()})" if lead and lead[0] == goal else ""
        self.log(f"{site.get('request_id')}: next goal {goal}{first} (weighed: "
                 + ", ".join(f"{r['goal']} {r['score']:.2f}" for r in rows[:4]) + ")" + note + self._tier_note(site, goal)
                 + getattr(self, "_space_note", ""))
        return goal

    def _or_clearing(self, site: Mapping[str, Any], choice: str) -> str:
        """Nothing new to build: the time goes to clearing ground ahead, when some waits (never after an attack)."""
        if choice != CONTINUE_PROJECTS or threatened(site) or not available("clear-area", site,
                                                                           designer=self.designer is not None):
            return choice
        from companion import clearing
        zone = clearing.zone_for(site)
        self.log(f"{site.get('request_id')}: next goal {clearing.PURPOSE} (nothing new to build: clearing "
                 f"ahead, {clearing.describe(zone)})")
        return clearing.PURPOSE

    def choose_research(self, request: Mapping[str, Any]) -> str:
        """Pick a technology from the game's offered options (System One, else cheapest on the rocket path), and note
        how much it is needed (research_need: the team's queue sorts the engineers' picks by it, R-9)."""
        tech = self._choose_research(request)
        if tech == IDLE:   # (no pick: nothing for the team's queue to sort)
            while len(RESEARCH_UNUSED) > NEEDS_KEPT:
                RESEARCH_UNUSED.pop(next(iter(RESEARCH_UNUSED)))
            return tech
        RESEARCH_NEEDS[str(request.get("request_id"))] = research_need(tech, request, getattr(self, "_last_site", None))
        while len(RESEARCH_NEEDS) > NEEDS_KEPT:   # (answers never sent, their request abandoned: the oldest go)
            RESEARCH_NEEDS.pop(next(iter(RESEARCH_NEEDS)))
        return tech

    def _choose_research(self, request: Mapping[str, Any]) -> str:
        options = bp._as_list(request.get("options"))
        if not options:
            raise ValueError("no research options")
        # Personality first: aggressive engineers arm early (a military rush), turtles wall in.
        traits = {str(t) for t in bp._as_list((request.get("personality") or {}).get("traits"))}
        offered = [o.get("name") for o in options if isinstance(o, Mapping)]
        if request.get("walled"):   # walled off by biters from those it is at war with: a way through
            for tech in VEHICLE_FIRST:
                if tech in offered:
                    self.log(f"{request.get('request_id')}: research {tech} (walled off by biters)")
                    return tech
        # Attacked lately in the team world: arms first. Otherwise what its next goals wait on comes first, the
        # soonest needed first, and the team's basics after: on a fixed list first, arms and green science held the
        # electric mining drill to minute 22 and logistics to minute 40 (run 35, 2026-10-25). Its starter turrets
        # are a goal of its own, so their research comes in their turn.
        attacked = bool(request.get("team")) and military_due(request)[1] == "attacked lately"
        if attacked:
            for tech in TEAM_DEFENSE:
                if tech in offered:
                    self.log(f"{request.get('request_id')}: research {tech} (attacked lately: arms first)")
                    return tech
            # (then the turrets' damage and fire rate, least lab time first: the user took damage 2.8 minutes after
            # the attacks grew hard, fire rate next; lesson 20)
            bonus = turret_bonuses(options)
            if bonus:
                self.log(f"{request.get('request_id')}: research {bonus[0]} (attacked lately: the turrets' damage "
                         f"and fire rate)")
                return bonus[0]
        # Survival research when the biters press harder than an on-time tier pulls (research_brain.survival_pick):
        # before a death world's fixed military share, the basics and the factory's needs, under attack too once
        # the defense list is done (R-2).
        if not request.get("war"):
            try:
                from companion import research_brain
                pressed = research_brain.survival_pick(options, getattr(self, "_last_site", None), request)
            except Exception as exc:
                pressed = None
                self.log(f"{request.get('request_id')}: survival weighing failed ({str(exc)[:120]})")
            if pressed:
                self.log(f"{request.get('request_id')}: research {pressed[0]} ({pressed[1]})")
                return pressed[0]
        # Survival first by nature (a death world): its military share, while due, comes before the factory's.
        if request.get("team") and traits & MILITANT:
            due, why = military_due(request)
            armed = [o for o in options if isinstance(o, Mapping) and o.get("military")]
            if due and armed:
                self.log(f"{request.get('request_id')}: research {armed[0]['name']} (survival first: {why})")
                return armed[0]["name"]
        # What its own next goals wait on (their recipes still locked), cheapest first: the factory goes on
        # (its electric mining and bus waited on research that never came, one-engineer run 2026-10-25).
        rank = wanted_ranks(getattr(self, "_last_site", None))
        wanted = set(rank)
        helps = [o for o in options if isinstance(o, Mapping) and set(bp._as_list(o.get("unlocks"))) & wanted]
        # (the soonest needed first; between two the next goal waits on both of, the cheaper: its packs are made by
        # hand at first)
        helps.sort(key=lambda o: (min(rank[n] for n in set(bp._as_list(o.get("unlocks"))) & wanted),
                                  float(o.get("units") or 0) * max(1, len(bp._as_list(o.get("packs"))))))
        if request.get("team") and not helps:
            for tech in TEAM_BASICS:
                if tech in offered:
                    self.log(f"{request.get('request_id')}: research {tech} (the team's basics)")
                    return tech
        if helps and not attacked:
            tech = helps[0]["name"]
            self.log(f"{request.get('request_id')}: research {tech} (its next goals wait for "
                     f"{', '.join(sorted(set(bp._as_list(helps[0].get('unlocks'))) & wanted))})")
            return tech
        # Then what the next science tier waits on (companion/tier_path.py), or survival research when the biters press
        # harder than the tier pulls (research_brain.pick), before the team's military share and the weighing: grey
        # science's military-2 waited until minute 230 for that share (run 51). At war, its own order.
        if not request.get("war"):
            try:
                from companion import research_brain
                tiered = research_brain.pick(options, getattr(self, "_last_site", None), request)
            except Exception as exc:
                tiered = None
                self.log(f"{request.get('request_id')}: tier path failed ({str(exc)[:120]})")
            if tiered:
                self.log(f"{request.get('request_id')}: research {tiered[0]} ({tiered[1]})")
                return tiered[0]
        if request.get("team"):
            for tech in TEAM_RESEARCH:
                if tech in offered and military_due(request)[0]:
                    self.log(f"{request.get('request_id')}: research {tech} (the team's order, its military share)")
                    return tech
            armed = [o for o in options if isinstance(o, Mapping) and o.get("military")]
            onward = [o for o in options if isinstance(o, Mapping) and not o.get("military")]
            due, why = military_due(request)
            if armed and (due or not onward):
                self.log(f"{request.get('request_id')}: research {armed[0]['name']} (military, for the team: {why})")
                return armed[0]["name"]
            options = onward   # (the way on: the rocket, the factory; military when its share is due again)
        if request.get("war"):
            for tech in WAR_FIRST:
                if tech in offered:
                    self.log(f"{request.get('request_id')}: research {tech} (at war)")
                    return tech
        # (in the team world the military share above has had its say: what it held back stays held back)
        allowed = {o.get("name") for o in options if isinstance(o, Mapping)}
        for tech in (MILITARY_FIRST if traits & (AGGRESSIVE | MILITANT) else DEFENSE_FIRST if traits & TURTLE else ()):
            if tech in offered and tech in allowed:
                self.log(f"{request.get('request_id')}: research {tech} (personality: {', '.join(sorted(traits))})")
                return tech
        # (from here on only what serves something: the labs wait rather than spend packs on nothing, R-10)
        site = getattr(self, "_last_site", None)
        used = [o for o in options if isinstance(o, Mapping) and research_used(o, site)]
        if not used:
            self.log(f"{request.get('request_id')}: research nothing for now (none of "
                     f"{', '.join(str(o.get('name')) for o in options[:6] if isinstance(o, Mapping))} serves a goal, "
                     f"the mall or the factory)")
            RESEARCH_UNUSED[str(request.get("request_id"))] = [str(o.get("name")) for o in options
                                                               if isinstance(o, Mapping)]
            return IDLE
        options = used
        if isinstance(options[0], Mapping) and options[0].get("early"):
            # What replaces hand work comes first, as a player researches it (the game lists it first).
            self.log(f"{request.get('request_id')}: research {options[0]['name']} (opening)")
            return options[0]["name"]
        try:   # (what each is worth against what it costs: companion/research_brain.py)
            from companion import research_brain
            picked = research_brain.choose(options, getattr(self, "_last_site", None), request)
        except Exception as exc:
            picked = None
            self.log(f"{request.get('request_id')}: research weighing failed ({str(exc)[:120]})")
        if picked:
            tech, rows = picked
            self.log(f"{request.get('request_id')}: research {tech} (weighed: "
                     + ", ".join(f"{r['name']} {r['score']:.3f}" for r in rows[:4]) + ")")
            return tech
        choices = []
        for option in options:
            unlocks = ", ".join(bp._as_list(option.get("unlocks"))[:6]) or "bonuses only"
            packs = " + ".join(bp._as_list(option.get("packs")))
            path = " Needed for the rocket." if option.get("rocket_path") else ""
            choices.append({"id": option["name"], "description": f"{option['name']}: {option.get('units')} units of "
                            f"{packs}; unlocks {unlocks}.{path}"})
        if self.chooser and len(choices) > 1:
            try:
                picked, probabilities = self.chooser.choose(request, choices, question="research")
                choice = self._sample(str(request.get("request_id")), probabilities, picked)
                top = sorted(probabilities.items(), key=lambda kv: -kv[1])[:4]
                self.log(f"{request.get('request_id')}: research {choice} (system one; "
                         + ", ".join(f"{k} {v:.0%}" for k, v in top) + ")")
                return choice
            except Exception as exc:
                self.log(f"{request.get('request_id')}: research chooser failed ({exc}); taking the cheapest")
        self.log(f"{request.get('request_id')}: research {options[0]['name']} (cheapest on the rocket path)")
        return options[0]["name"]

    def __call__(self, site: Mapping[str, Any]) -> dict:
        self._last_site = site   # (what its research is weighed against: the goals it waits on)
        note_ready(site, designer=self.designer is not None)   # (and what its strategist may ask for)
        try:
            ledger.remember(site)   # the engineer's factory as it stands (its strategist reads it too)
            priorities.remember(site)   # (and what it is weighed from)
        except Exception as exc:    # the ledger must never stop a plan
            self.log(f"{site.get('request_id')}: factory ledger failed ({exc})")
        try:   # its blocks built from saved builds: their uses, measurements and notes in the library
            build_history.attribute(self.catalog, site)
        except Exception as exc:    # nor must the history
            self.log(f"{site.get('request_id')}: build history failed ({exc})")
        try:   # the user's blueprint packs, judged once for this mod set and imported (companion/blueprint_packs.py)
            from companion import blueprint_packs
            blueprint_packs.sync(self.catalog, site, ask=getattr(self, "annotator", None), log=self.log)
        except Exception as exc:    # nor the packs
            self.log(f"{site.get('request_id')}: blueprint packs failed ({str(exc)[:160]})")
        site = self._with_plan(site)   # the factory plan, made or revised first when one is due
        from companion import failures
        stuck = failures.stalled(site)   # (its watchdog: getting nowhere on that goal, held as a failure, SO-5)
        if stuck:
            self.log(f"{site.get('request_id')}: {stuck} stalled ({(site.get('stalled') or {}).get('phase')}); held")
        failed = bp._as_list(site.get("failed_purposes"))
        if any(failures.released(site, p) for p in failed):   # (released by a change: the game's cooldown too, SO-4)
            site = {**site, "failed_purposes": [p for p in failed if not failures.released(site, p)]}
        plan = None
        self._purpose_now = None
        try:
            # (its belts cross under what is in the way, and its coal taps the base's coal lines, once researched)
            from companion import belt_lines
            from companion.smelting import undergrounds
            unlocked = ratios.unlocked(site)
            with belt_lines.taps("splitter" in unlocked), undergrounds("underground-belt" in unlocked):
                plan = self._plan(site)
        except (Defer, MoveSite, Scout):
            raise   # (not failures: the goal waits, moves or sends the engineer looking)
        except Exception as failed:
            # every failure carries the goal it was for, for the game and the failure memory to note (a chosen
            # goal's request is "choose": the same goal, never noted, was chosen and failed again and again,
            # one-engineer run 2026-10-24; an urgent power plant so, every 20 seconds for 195 minutes, SO-4)
            if self._purpose_now and not getattr(failed, "purpose", None):
                try:
                    failed.purpose = self._purpose_now
                except AttributeError:   # (an exception that takes no attributes: noted as it is)
                    pass
            if isinstance(failed, layout_planner.PlanError):
                self._failed(site, failed)
            raise
        finally:
            self._upkeep(site, plan)   # its progress and cleared ground; retirements due ride on this plan
        # The team world: the game refuses a plan with anything outside the engineer's plot (or beyond
        # the team area) or near players' builds; a planner that missed it fails here, not in the game.
        bad = team_area.misplaced(plan, site)
        if bad:
            failed = layout_planner.PlanError(f"{len(bad)} buildings outside its plot or near players' builds: "
                                              + ", ".join(bad[:5]))
            # (under its goal: with none, the game noted "choose" and an urgent power plant failing so was asked
            # again every 20 seconds for 195 minutes, SO-4)
            failed.purpose = plan.get("purpose") or self._purpose_now
            self._failed(site, failed)
            raise failed
        from companion import failures
        failures.planned(site, str(plan.get("purpose") or self._purpose_now))
        try:   # its budget of hand work for its own items, when something would make them by machine (F-11)
            from companion import plan_hold
            clear = plan_hold.made_by(site, plan)   # (what it makes of what was made by hand: once it stands, W-23)
            if clear:
                plan["hand_clear"] = clear
            hold = plan_hold.hand_hold(site, plan, designer=self.designer is not None)
            if hold:
                plan["hand_hold"] = hold
                plan["hand_items"] = plan_hold.countable(site, plan, designer=self.designer is not None)
                # (a budget, said when the plan is taken: not a hand-back; the game says that one, "hands back <goal>
                # after N s"; LIVE-7c-a (2) read these 23 budgets as 23 hand-backs where one came)
                self.log(f"{site.get('request_id')}: {plan.get('purpose')}: hand-work budget {hold / 3600:.0f} min on "
                         f"{', '.join(plan['hand_items'][:4])}, then it hands back (a machine could make them)")
        except Exception as exc:   # (never in the way of a plan)
            self.log(f"{site.get('request_id')}: hand-work budget failed ({str(exc)[:120]})")
        # What the plan builds, for the ledger: its kind and expected load (a bus stage brings its ports)
        plan["ledger"] = {**ledger.intent(plan, site), **(plan.get("ledger") or {})}
        try:   # every machine side against its inserters (Ledger's L-4, companion/inserters.py): what falls short is said
            from companion import inserters
            short = inserters.plan(plan.get("entities") or (), site)
            if short:
                plan["ledger"]["inserter_factor"] = min(c.factor for c in short)
                plan.setdefault("notes", []).extend(c.why for c in short[:3])
                self.log(f"{site.get('request_id')}: inserters short at {len(short)} machine sides: {short[0].why}")
        except Exception as exc:   # (never in the way of a plan)
            self.log(f"{site.get('request_id')}: inserter check failed ({exc})")
        if plan.get("blueprint_id"):   # the game keeps it with the block, so its measurements find the build
            plan["ledger"]["build"] = plan["blueprint_id"]
        try:   # the threat it was planned under, written down for learning (decisions/threat.jsonl)
            from companion import threat
            t = threat.estimate(site)
            if t.level >= threat.CALM:
                threat.record(site, t, {"purpose": plan.get("purpose"), "kit": (plan.get("brain") or {}).get("kit")})
        except Exception as exc:   # (a log, never in the way of a plan)
            self.log(f"{site.get('request_id')}: threat log failed ({exc})")
        return plan

    def _failed(self, site: Mapping[str, Any], failed: Exception) -> None:
        """A goal's plan failed: held until something that could mend it changes (companion/failures.py), and its
        strategist told after every failures.ESCALATE_AFTER for the same reason (`escalate` on the error)."""
        from companion import failures
        purpose = getattr(failed, "purpose", None)
        if not purpose:
            return
        count = failures.note(site, purpose, str(failed))
        if failures.escalate(count):
            failed.escalate = count
            self.log(f"{site.get('request_id')}: {purpose} failed {count} times for the same reason; its strategist "
                     "is told")

    def supply_first(self, site: Mapping[str, Any]) -> tuple[str, ledger.Budget] | None:
        """An item the base is short of (the ledger: claimed more than made, or consumers starved while
        every producer runs flat out) gets more supply before anything new is built on it: the busy
        block upgraded in place, else (before a bus: burner pairs do not belong beside one) more smelting
        for its plate. Only shortages that matter now count (companion/priorities.py); the rest are held
        until their priority rises."""
        try:
            short = priorities.pressing(site, log=self.log)
        except Exception as exc:
            self.log(f"{site.get('request_id')}: factory ledger failed ({exc})")
            return None
        designer = self.designer is not None
        on_bus = bool((site.get("base_plan") or {}).get("stages"))
        from companion import goal_brain
        tried = goal_brain.failures(site).get("upgrade", 0)
        for budget in short:
            if available("upgrade", site, designer=designer) and not tried and upgrade_helps(site, budget.item):
                return "upgrade", budget
            for more in (ELECTRIC_SUPPLY_GOALS.get(budget.item), SUPPLY_GOALS.get(budget.item)):
                if more and not on_bus and plate_lead.available_ungated(site, more, designer=designer):
                    short = plate_lead.limiting(site, more) if PURPOSES[more].get("electric_smelting") else None
                    if short:
                        # (an array its income cannot buy soon: what grows the income it waits on first, F-14)
                        grow = plate_lead.income_goal(site, plate_lead.lead_of(site, short), designer=designer)
                        if grow:
                            return grow[0], budget
                    return more, budget
        # (nothing short now: the plates a step ahead of what the science tiers will take, in whole arrays;
        # companion/plate_lead.py; offered where its holds apply, plate_lead.offered)
        if plate_lead.offered(site):
            lead = plate_lead.goal(site, designer=designer)
            if lead:
                return lead
            news = plate_lead.news(site, designer=designer)
            if news and news != getattr(self, "_lead_news", None):
                self.log(f"{site.get('request_id')}: the plates' lead: " + "; ".join(news[:3]))
            self._lead_news = news
        return None

    def _plan(self, site: Mapping[str, Any]) -> dict:
        """The plan for this request's goal. A goal whose plan is still being worked out in the background
        (companion/background.py) gives way, for this request, to the engineer's next goal: it builds meanwhile
        rather than wait, and a later request takes that plan (run 55's bus plans took longer than the game waits)."""
        from companion import background, defense_brain
        while True:
            try:
                return self._plan_goal(site)
            except Defer as exc:
                # (a defense goal it chose itself, declined by its planner: passed over until something changes, and
                # the next goal now, not a minute on; D-16, run normal-7b: reinforce chosen 53 times, declined 48)
                chosen = getattr(self, "_purpose_now", None)
                if not chosen or not _guards(chosen) or defense_brain.declined(site, chosen):
                    raise
                if site.get("purpose") != "choose":
                    # (asked for by name, its planner declining it: held all the same, so none comes back at once; D-20)
                    if exc.by_planner:
                        defense_brain.decline(site, chosen, exc.reason)
                    raise
                defense_brain.decline(site, chosen, exc.reason)
                self.log(f"{site.get('request_id')}: {chosen} declined ({exc.reason}); the next goal meanwhile")
            except background.Pending as wait:
                held = list(site.get("worked_out") or ())
                if wait.purpose in held:
                    raise layout_planner.PlanError(str(wait))   # (never: a goal given way is not chosen again)
                self.log(f"{site.get('request_id')}: {wait}; the next goal meanwhile")
                site = {**site, "purpose": "choose", "worked_out": [*held, wait.purpose]}

    def _plan_goal(self, site: Mapping[str, Any]) -> dict:
        request_id = str(site.get("request_id"))
        purpose = site.get("purpose") or "iron-smelting-start"
        if purpose == "module":
            return self._module(site, request_id)
        if purpose != "choose":
            # Asked for by name (the strategist): only what the game is ready for. What is not ready gives way
            # to the engineer's own next goal at once: a refused request held it two minutes before it asked
            # again (one-engineer hour, 2026-10-24: red-science automation asked for before its research).
            self._purpose_now = purpose
            from companion import plan_hold
            if plan_hold.own_hand_defense(purpose):
                # (its own defense asked for now while its defense row stands set aside: W-23; an ally's call is not)
                self._back_to_defense_supply(site)
            reason = why_unavailable(purpose, site, designer=self.designer is not None)
            if reason and site.get("paused"):
                # a build of its own was set aside for this one: it carries on (the game resumes it on a
                # deferral), rather than its next goal chosen afresh, which was the build set aside: a second
                # wall row went up while the first waited (one-engineer run 2026-10-24)
                raise Defer(f"{purpose} is not ready ({reason}); back to {site['paused']}", 600)
            if reason:
                self.log(f"{request_id}: {purpose} is not ready ({reason}); the engineer's own next goal instead")
                site = {**site, "purpose": "choose"}
                purpose = "choose"
            elif purpose == "factory-plan":   # (planned again before this, in __call__): the next goal shortly
                raise Defer("factory plan revised", 600)
            elif site.get("ask") in ("next", "later") and not _guards(purpose):
                # Asked for by its strategist, not now: weighed with the engineer's own next goals (survival first),
                # not built blindly: a bus asked for took the wall row's turn, no wall by minute 110 (run 52, 2026-10-26)
                self.log(f"{request_id}: {purpose} asked for ({site['ask']}): weighed with its own next goals")
                from companion import goal_brain
                goal_brain.remember_ask(site, purpose)
                site = {**site, "purpose": "choose", "asked": purpose}
                purpose = "choose"
        if purpose == "choose":
            from companion import background
            ready = sorted(g for g in background.ready_goals(site.get("npc_id")) if g in PURPOSES
                           and g not in (site.get("worked_out") or ()) and available(g, site, designer=self.designer is not None))
            if ready:   # (its plan worked out in the background is ready: taken now, never left to expire, F-21)
                purpose = self._purpose_now = ready[0]
                self.log(f"{request_id}: next goal {purpose}: its plan, worked out in the background, is ready")
            else:
                purpose = self._purpose_now = self.choose_purpose(site)
            from companion import failures
            failures.chose(site, purpose)   # (a stall while it is on this goal holds this goal, not "choose": SO-5)
            if purpose == CONTINUE_PROJECTS:
                # Nothing new now: ten minutes when there is nothing it could build, two when System One
                # chose to hold off over goals it could take up (ten minutes idle beside open goals cost a
                # one-engineer run its pace, 2026-10-25).
                waiting = [o for o in purpose_options(site, designer=self.designer is not None)
                           if o["id"] != CONTINUE_PROJECTS]
                if site.get("worked_out"):   # (soon again: the plan being worked out is taken then)
                    raise Defer(f"{CONTINUE_PROJECTS}: the {', '.join(site['worked_out'])} plan is being worked out",
                                PENDING_TICKS)
                raise Defer(CONTINUE_PROJECTS, 7200 if waiting else 36000)
        return self._dispatch(site, request_id, purpose)

    def _dispatch(self, site: Mapping[str, Any], request_id: str, purpose: str) -> dict:
        """The planner for a goal, chosen or asked for by name: by its name, else by its spec."""
        if purpose == "clear-area":
            return self._clear(site, request_id)
        if purpose == "bus-base":
            return self._bus(site, request_id)
        if purpose == "nuclear-power":
            return self._nuclear(site, request_id)
        if purpose == "nuclear-fuel":
            return self._nuclear_fuel(site, request_id)
        if purpose == "power-plant":
            return self._power(site, request_id)
        if purpose == "steam-power" and purpose not in (site.get("suggestions") or {}):
            return self._power(site, request_id, purpose)
        if purpose == "boiler-coal":
            return self._boiler_coal(site, request_id)
        if purpose == "outpost-defense":
            return self._defend(site, request_id)
        if purpose == "robot-network":
            return self._robots(site, request_id)
        if purpose == "robot-supply":
            return self._robot_supply(site, request_id)
        if purpose in PURPOSES and PURPOSES[purpose].get("perimeter"):
            return self._perimeter(site, request_id, purpose)
        if purpose == "ally-defense":
            return self._help_ally(site, request_id)
        if purpose == "upgrade":
            return self._upgrade(site, request_id)
        if purpose == "military-science-automation":
            return self._military(site, request_id)
        if purpose == "science-tier":
            return self._tier(site, request_id)
        if purpose == "defense-production":
            return self._supply_rows(site, request_id)
        if purpose == "starter-turrets":
            return self._starter(site, request_id)
        if purpose == "reinforce":
            return self._reinforce(site, request_id)
        if purpose == "ammo-line":
            return self._ammo_line(site, request_id)
        if purpose == "wall-automation":
            return self._wall_block(site, request_id)
        if purpose in PURPOSES and (PURPOSES[purpose].get("electric_smelting") or PURPOSES[purpose].get("electric_supply")):
            return self._electric(site, request_id, purpose)
        if purpose in PURPOSES:
            # Asked for by name (the strategist), or a goal already chosen re-asked at a moved site: built as when
            # chosen. (Such goals once fell to the starter iron layout below: red science and a defense asked
            # for by name became drills and furnaces on iron under their names, one-engineer hour 2026-10-24.)
            return self._by_spec(site, request_id, purpose)
        if purpose == "iron-smelting-start":
            # The first plan: iron where the engineer starts; with none in its first site (its plot's iron
            # just past the window, a Factorio 2.1 map, 2026-10-23: it failed and never asked again), a move
            # to the iron it knows of, else a look round home, as for any ore.
            spec = {"resource": "iron-ore", "output": "furnace", "pairs": 2,
                    "goal": site.get("goal") or "Start automatic iron plate production."}
            return self._resource_layout(site, request_id, purpose, spec, self.fallback)
        return self._layout(site, request_id, purpose, self.fallback)

    def learn(self, entry: Mapping[str, Any]) -> str | None:
        """Add a blueprint a player taught in game to the library, under the goal it fits."""
        from companion.taught import to_library
        blueprint = to_library(entry)
        if blueprint is None:
            return None
        self.catalog.add(blueprint)
        self.log(f"learned {entry.get('label')!r} from {entry.get('player')} as {blueprint['purpose']} "
                 f"(library now {len(self.catalog)})")
        return blueprint["purpose"]

    def _upgrade(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import upgrade_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan an upgrade")
        try:
            plan = upgrade_planner.plan_upgrade(site, world, library=self.modules)
        except layout_planner.PlanError as exc:
            raise Defer(str(exc), 18000)
        self.log(f"{request_id}: upgrading {plan['upgrades']} in place ({len(plan['entities'])} buildings)")
        return plan

    def _clear(self, site: Mapping[str, Any], request_id: str) -> dict:
        """The next stage of clearing a zone: the one asked for with the request, else the most important
        pending one (companion/clearing.py). The game ends it early when anything urgent comes up."""
        from companion import clearing
        zone = clearing.zone_for(site)
        if zone is None:
            raise Defer("no ground waiting to be cleared", 18000)
        try:
            plan = clearing.plan_clearing(site, zone)
        except ValueError as exc:
            raise Defer(str(exc), 18000)
        self.log(f"{request_id}: clearing {len(plan['stages'])} slices of {clearing.describe(zone)}")
        return plan

    def _defend(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import defense_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a defense")
        open_ = defense_planner.outposts(site, world)
        if not open_:
            raise Defer("no threatened block left undefended", 18000)
        plan = defense_planner.plan_defense(site, world, open_[0])
        guns = sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret")
        self.log(f"{request_id}: defending {open_[0]['block'].get('stage') or open_[0]['block'].get('purpose')} "
                 f"({guns} turrets, threat {open_[0]['distance']:.0f} tiles away)")
        return plan

    def _perimeter(self, site: Mapping[str, Any], request_id: str, purpose: str) -> dict:
        from companion import perimeter
        world = self._world_over(site, perimeter.map_window(site)) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan the plot's edge")
        # An emergency's hand work is bounded by the time to the next wave (a whole edge of walls made by hand
        # held a run for 40 minutes, the review, 2026-10-25): the walls it has and can make in time, turrets as
        # many as it can make (a pair at least).
        from companion import defense_brain
        urgent = emergency(site)
        kit = emergency_kit(site) if urgent else Kit()
        walls = hand_walls(site)
        try:
            if purpose == perimeter.WALL:
                plan = perimeter.plan_wall(site, world, limit=walls)
            elif purpose == perimeter.TURRETS:
                plan = perimeter.plan_turrets(site, world, shield=urgent and walls > 0,
                                              max_turrets=max(defense_brain.MIN_GROUP, kit.turrets) if urgent else None,
                                              wall_limit=walls)
            else:
                plan = perimeter.plan_breach(site, world, wall_limit=walls)
        except perimeter.Done as exc:
            raise Defer.declined(str(exc), 18000)
        if purpose != perimeter.WALL:
            perimeter._retire_moved(site, plan)   # (an old ring's sides that moved, once the new one stands)
        if purpose == perimeter.TURRETS and perimeter_blocks(site, perimeter.STARTER):
            # the starter turrets are taken down once these stand, for the ring's next turrets
            plan["retire"] = list(plan.get("retire") or []) + [perimeter.STARTER]
        kinds: dict[str, int] = {}
        for e in plan["entities"]:
            kinds[e["prototype"]] = kinds.get(e["prototype"], 0) + 1
        self.log(f"{request_id}: {purpose} ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})")
        return plan

    def _world_over(self, site: Mapping[str, Any], window: tuple[Mapping[str, float], int] | None) -> Any:
        """The map over `window` (centre, radius) when the map source can centre one there (the bridge's
        can), else the usual map round home."""
        import inspect
        if window:
            try:
                params = inspect.signature(self.world).parameters
            except (TypeError, ValueError):
                params = {}
            if "center" in params and "radius" in params:
                return self.world(site, center=window[0], radius=window[1])
        return self.world(site)

    def _help_ally(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import defense_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan an ally's defense")
        if not isinstance(site.get("help"), Mapping):
            raise Defer("no ally asked for help", 18000)
        plan = defense_planner.plan_for_ally(site, world)
        guns = sum(1 for e in plan["entities"] if e["prototype"] == "gun-turret")
        self.log(f"{request_id}: helping an ally ({guns} turrets by their base)")
        return plan

    def _power(self, site: Mapping[str, Any], request_id: str, purpose: str = "power-plant") -> dict:
        from companion import power_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a power plant")
        plan = power_planner.plan_power(site, world, purpose)
        boilers = sum(1 for e in plan["entities"] if e["prototype"] == "boiler")
        self.log(f"{request_id}: power plant ({boilers} boilers, {len(plan['entities'])} buildings)")
        return plan

    def _uranium_known(self, site: Mapping[str, Any], world: Mapping[str, Any], purpose: str, request_id: str) -> None:
        """Scouting first when no uranium is known: not among its patches and not on the map it was sent."""
        resource = PURPOSES[purpose]["near_resource"]
        if resource not in known_resources(site) and not (world.get("resources") or {}).get(resource):
            self.log(f"{request_id}: no known {resource} for {purpose}; scouting")
            raise Scout(purpose, resource)

    def _nuclear_fuel(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import uranium_plant
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan the nuclear fuel")
        self._uranium_known(site, world, "nuclear-fuel", request_id)
        try:
            plan = uranium_plant.plan(site, world, list(self.catalog.blueprints.values()))
        except layout_planner.PlanError as failed:
            keep_failure("nuclear-fuel", site, world, str(failed))
            raise
        self.log(f"{request_id}: nuclear fuel ({'; '.join(plan['notes'])}; {len(plan['entities'])} buildings)")
        return plan

    def _nuclear(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import nuclear_plant
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a nuclear plant")
        self._uranium_known(site, world, "nuclear-power", request_id)   # (no plant without its fuel's ore)
        try:
            plan = nuclear_plant.plan(site, world)
        except layout_planner.PlanError as failed:
            keep_failure("nuclear-plant", site, world, str(failed))
            raise
        self.log(f"{request_id}: nuclear plant ({'; '.join(plan['notes'])}; {plan['pumps']} offshore pumps, "
                 f"{len(plan['entities'])} buildings)")
        return plan

    def _robots(self, site: Mapping[str, Any], request_id: str) -> dict:
        """The roboport grid's next cells (Ledger's companion/robo_grid.py)."""
        from companion import robo_grid
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan roboports")
        plan = robo_grid.plan(site, world)
        self.log(f"{request_id}: robot network ({'; '.join(plan.get('notes') or [])})")
        return plan

    def _robot_supply(self, site: Mapping[str, Any], request_id: str) -> dict:
        """A maker cell for what the robot network uses up (Ledger's companion/robo_grid.supply_plan)."""
        from companion import robo_grid
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a robot supply cell")
        plan = robo_grid.supply_plan(site, world)
        self.log(f"{request_id}: robot supply ({'; '.join(plan.get('notes') or [])})")
        return plan

    def _boiler_coal(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import power_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a coal belt to the boilers")
        from companion.smelting import undergrounds
        with undergrounds("underground-belt" in ratios.unlocked(site)):
            plan = power_planner.plan_boiler_coal(site, world)
        drills = sum(1 for e in plan["entities"] if e["prototype"] == "electric-mining-drill")
        self.log(f"{request_id}: coal by belt to {plan['boilers']} boilers ({drills} electric drills, "
                 f"{len(plan['entities'])} buildings)")
        return plan

    def _oil_plant(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import oil_plant
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings for the oil plant")
        missing = oil_plant.missing_products(site)   # (a second plant for what was unlocked since: sulfur after plastic)
        want = {item: oil_plant.WANT[item] for item in missing} if missing else None
        # Ready blocks first (the optimizer's, the user's books): those with ports that make plastic or sulfur, the
        # fewest buildings first.
        ready = sorted((b for b in self.catalog.blueprints.values()
                        if (b.get("import") or {}).get("ports_from") == "sidecar"
                        and set((b.get("import") or {}).get("products") or b.get("outputs") or []) & {"plastic-bar", "sulfur"}),
                       key=lambda b: (len(b.get("entities") or []), str(b.get("id"))))
        try:
            plan = oil_plant.plan(site, world, want, ready=ready)
        except layout_planner.PlanError as failed:
            keep_failure("oil-plant", site, world, str(failed))
            raise
        size = plan["sizing"]
        cracking = ", ".join(f"{n} {r}" for r, n in size.get("cracking", {}).items() if n) or "no cracking"
        gas = (f"gas {size['gas_made']:.1f}/s for {size['gas_drawn']:.1f}/s drawn" if size.get("gas_made") is not None
               else f"{size.get('copies')} ready block(s) making " + ", ".join(f"{v:g}/s {k}" for k, v in (size.get("makes") or {}).items()))
        self.log(f"{request_id}: oil plant on {size['process']}: {size['refineries']} refineries, {cracking}, "
                 + ", ".join(f"{n} {k}" for k, n in size["plants"].items())
                 + f"; {gas}; {plan['pumpjacks']} pumpjacks for {size['crude']:.1f} crude/s; {len(plan['entities'])} "
                 "buildings" + (f" ({'; '.join(plan['notes'])})" if plan.get("notes") else ""))
        return plan

    def _by_spec(self, site: Mapping[str, Any], request_id: str, purpose: str) -> dict:
        """A catalog goal from its spec: a saved layout, the designer's, or its built-in one (the game's own
        suggestion for a suggested goal, else burner drills on its resource)."""
        spec = PURPOSES[purpose]
        site = {**site, "purpose": purpose, "goal": spec["goal"]}

        def fallback(s: Mapping[str, Any]) -> dict:
            if spec.get("builder") == "oil-plant":
                try:   # (the plant built to its ratios; the game's own small block when it cannot be had)
                    return self._oil_plant(s, request_id)
                except layout_planner.PlanError as failed:
                    self.log(f"{request_id}: no oil plant here ({str(failed)[:120]}); the game's own layout instead")
            if spec.get("suggested"):
                return suggested_plan(s, purpose)
            return layout_planner.plan(s, pairs=spec["pairs"], resource=spec["resource"], output=spec["output"],
                                       name=purpose)
        return self._resource_layout(site, request_id, purpose, spec, fallback)

    def _reinforce(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import defense_brain, perimeter
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan its defense")
        area = defense_brain.most_needed(site)
        urgent = emergency(site)
        kit = emergency_kit(site) if urgent else Kit()
        walls = hand_walls(site) if urgent else 0
        try:
            plan = defense_brain.plan_reinforce(site, world, area, shield=walls > 0,
                                                max_turrets=kit.turrets if urgent else None, wall_limit=walls)
        except perimeter.Done as exc:
            raise Defer.declined(str(exc), 7200)
        brain = plan["brain"]
        if urgent:
            brain["kit"] = {"seconds": kit.seconds, "turrets": kit.turrets, "walls": walls}
        self.log(f"{request_id}: reinforce: {brain['turrets']} turrets on the {brain['side']} of "
                 f"{', '.join(sorted(set(area.purposes)))[:80]} (pressure {brain['pressure']}, covered "
                 f"{brain['covered']}; {', '.join(brain['why']) or 'its edge'})")
        defense_brain.record(site, area, {"purpose": defense_brain.PURPOSE, **brain})
        return plan

    def _ammo_line(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import ammo_line, perimeter
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a turret line")
        try:
            plan = ammo_line.plan(site, world)
        except perimeter.Done as exc:
            raise Defer.declined(str(exc), 7200)
        line = plan["ammo_line"]
        self.log(f"{request_id}: ammo {line['kind']}: {line['turrets']} turrets, {line['belts']} belts, "
                 f"{line['poles']} poles, {line['ammo']} from "
                 + {"row": "the magazine row", "chest": "a chest filled by hand"}.get(line["source"], "its loop"))
        return plan

    def _starter(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import perimeter
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan the starter turrets")
        plan = perimeter.plan_starter(site, world)
        self.log(f"{request_id}: starter turrets ({len(plan['entities'])} by the factory, "
                 f"{plan['stages'][0]['id'].split('-', 1)[1]} side)")
        return plan

    def _electric(self, site: Mapping[str, Any], request_id: str, purpose: str) -> dict:
        from companion import electric_start
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan electric mining")
        smelt = PURPOSES[purpose].get("electric_smelting")
        try:
            plan = (electric_start.plan_smelting if smelt else electric_start.plan_supply)(site, world, purpose)
        except ValueError as exc:
            keep_failure(purpose, site, world, str(exc))
            raise
        drills = sum(1 for e in plan["entities"] if e["prototype"] == "electric-mining-drill")
        furnaces = sum(1 for e in plan["entities"] if e["prototype"] == "stone-furnace")
        keep = plan.get("keep") or {}
        array = (f"; array {int(keep.get('array') or 0) + 1} of {float(keep['target']) * 60:.0f}/min"
                 + (" (a whole one had no room)" if float(keep["target"]) < float(PURPOSES[purpose].get("rate") or 0) - 1e-6
                    else "")) if smelt and keep.get("target") else ""
        self.log(f"{request_id}: {purpose} ({drills} electric drills, {furnaces} furnaces, {len(plan['entities'])} "
                 f"buildings" + (f"; retires {', '.join(plan['retire'])}" if plan.get("retire") else "") + array + ")")
        return plan

    def _wall_block(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import wall_block
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan the wall row")
        plan = wall_block.plan_block(site, world)
        self.log(f"{request_id}: wall row ({len(plan['entities'])} buildings)")
        return plan

    def _military(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import military_modules
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan military science")
        from companion import tier_builds
        # (at the rate the science path wants grey packs, the supplies it waits on first, labs with it when due)
        from companion import tier_path
        # (no science path in the export, an old save: the first grey line's size as before)
        floor = 0.0 if tier_path.assess(site).tiers else military_modules.BLOCK_RATE
        plan = tier_builds.plan(site, world, military_modules.PACK, purpose=military_modules.PURPOSE, floor=floor)
        tier = plan.get("tier") or {}
        self.log(f"{request_id}: military science rows ({len(plan['entities'])} buildings, "
                 f"{float(tier.get('rate') or 0) * 60:.1f} packs/min"
                 + (f", first {', '.join(tier['waits'])}" if tier.get("waits") else "")
                 + (f", {tier['labs']} labs" if tier.get("labs") else "") + ")")
        return plan

    def _supply_rows(self, site: Mapping[str, Any], request_id: str) -> dict:
        """Defense items made by machines at the rate the waves ask for (companion/defense_production.py)."""
        from companion import defense_production
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to build the defense supply")
        plan = defense_production.plan(site, world)
        d = plan.get("defense_production") or {}
        self.log(f"{request_id}: defense supply: {d.get('item')} at {float(d.get('rate') or 0) * 60:.1f}/min "
                 f"(draw {float(d.get('draw') or 0) * 60:.1f}, made {float(d.get('have') or 0) * 60:.1f}; "
                 f"{d.get('why')}; {len(plan['entities'])} buildings)")
        return plan

    def _tier(self, site: Mapping[str, Any], request_id: str) -> dict:
        """What the science path says is due, built at its rate (companion/tier_builds.py)."""
        from companion import tier_builds
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to build what the science path needs")
        plan = tier_builds.plan(site, world)
        tier = plan.get("tier") or {}
        self.log(f"{request_id}: science path: {tier.get('item')} at {float(tier.get('rate') or 0):g}"
                 + ("" if tier.get("item") == "lab" else "/s")
                 + (f", first {', '.join(tier['waits'])}" if tier.get("waits") else "")
                 + (f", {tier['labs']} labs" if tier.get("labs") else "")
                 + f" ({len(plan['entities'])} buildings; {tier.get('why')})")
        if plan.get("notes"):   # (what is fed by belt and what is left to the hand, and why: F-20)
            self.log(f"{request_id}: inputs: by belt {', '.join(sorted(tier.get('delivered') or {})) or 'none'}; "
                     + "; ".join(plan["notes"][:6]))
        return plan

    def _module(self, site: Mapping[str, Any], request_id: str) -> dict:
        """A production block for someone (the strategist agreed to build it at a player's base)."""
        from companion.module_order import plan_module
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the build site")
        plan = plan_module(site, world)
        self.log(f"{request_id}: production block {plan['item']} {plan['rate']:g}/s at "
                 f"{plan['at'][0]},{plan['at'][1]} ({len(plan['entities'])} buildings)")
        return plan

    def _bus(self, site: Mapping[str, Any], request_id: str) -> dict:
        from companion import bus_planner
        world = self.world(site) if self.world else None
        if not world or not world.get("ok", True) or "entities" not in world:
            raise layout_planner.PlanError("no map of the surroundings to plan a bus base")
        if self.modules is None:
            from companion.module_library import ModuleLibrary
            self.modules = ModuleLibrary()   # proven module designs (tools/design_module.py)
        try:
            plan = bus_planner.plan_in_background(site, world, library=self.modules)
        except ValueError as exc:
            # What it searched, kept to be replayed offline: a bus that fit nowhere in run 19 could not be
            # explained from the site alone (the map is fetched apart, Codex's review, 2026-10-25).
            keep_failure("bus", site, world, str(exc))
            raise
        stage = plan["base_plan"]["stage"]
        designs = plan["base_plan"].get("designs") or []
        belts = sum(1 for e in plan["entities"] if e.get("prototype") == "transport-belt")
        self.log(f"{request_id}: bus base stage {stage} ({len(plan['entities'])} buildings, {belts} belts, "
                 f"{plan['base_plan']['spm']:g} science/min"
                 + (f", designed modules: {', '.join(designs)}" if designs else "") + ")")
        notes = plan.pop("notes", None) or []
        for note in notes:
            self.log(f"{request_id}:   note: {note}")
        # (each bus plan's map, kept to replay: run 22's bus fit only lean, and put an underground belt on a cliff
        # the map should have had; a wider one that fit nowhere is kept as such)
        keep_failure("bus-lean" if any("a wider one fit nowhere" in n for n in notes) else "bus-plan", site, world,
                     "; ".join(notes) or "accepted")
        return plan

    def _resource_layout(self, site: Mapping[str, Any], request_id: str, purpose: str, spec: Mapping[str, Any],
                         fallback: Callable) -> dict:
        """Lay out here if the resource fits; else move to a known patch, else go scouting."""
        resource = spec.get("resource")
        if spec.get("suggested"):
            # Built-in layouts are placed in world positions anywhere the engineer
            # knows about; without one, look for the resource if it is unknown (before any saved build or
            # design of a standalone block: neither can place it on ground nobody has seen).
            offered = purpose in (site.get("suggestions") or {})
            if resource and resource not in known_resources(site) and not offered:
                self.log(f"{request_id}: no known {resource} for {purpose}; scouting")
                raise Scout(purpose, resource)
            in_site = {r.get("resource") for r in bp._as_list(site.get("resources")) if isinstance(r, Mapping)}
            if not (resource and spec.get("standalone") and not offered and resource not in in_site):
                try:
                    return {**self._layout(site, request_id, purpose, fallback), "purpose": purpose}
                except layout_planner.PlanError:
                    if resource and resource not in known_resources(site):
                        self.log(f"{request_id}: no known {resource} for {purpose}; scouting")
                        raise Scout(purpose, resource)
                    raise
            # (a standalone block with no layout of the game's and none of its resource in this site: a saved
            # build or a design needs the resource in view, so the site moves to a known patch first, below)
        here = {r.get("resource") for r in bp._as_list(site.get("resources")) if isinstance(r, Mapping)}
        key = (site.get("npc_id"), purpose)
        if not resource or resource in here:
            try:
                plan = {**self._layout(site, request_id, purpose, fallback), "purpose": purpose}
                self.tried_sites.pop(key, None)
                return plan
            except layout_planner.PlanError as exc:
                if not resource:
                    raise
                self.log(f"{request_id}: no room for {purpose} here ({exc})")
        tried = self.tried_sites.setdefault(key, set())
        home = site.get("home") or {}
        origin_offset = (round(32 - float(home.get("x", 32))), round(32 - float(home.get("y", 32))))
        tried.add(origin_offset)
        held_back = False
        for patch in bp._as_list(site.get("known_patches")):   # nearest first
            if not isinstance(patch, Mapping) or patch.get("resource") != resource                     or (patch.get("tiles") or 0) < spec.get("min_tiles", 16):
                continue
            if patch.get("amount") is not None and float(patch["amount"]) < SPENT_PER_TILE * float(patch["tiles"]):
                continue   # nearly mined out: not worth building on
            dx, dy = int(patch.get("dx", 0)), int(patch.get("dy", 0))
            if any(abs(dx - tx) < 24 and abs(dy - ty) < 24 for tx, ty in tried):
                continue
            team = team_area.team_of(site)
            if team:
                # The team world: a site on its own ground that still has the patch in it (a patch by the
                # plot's edge: the site slides inward); none at all, and the patch is not to be had.
                hx, hy = float(home.get("x", 32)), float(home.get("y", 32))
                spot = team_area.window_for(team, hx + dx, hy + dy, half=33)   # (a tile spare: the game floors it)
                if spot is None:
                    held_back = True
                    continue
                dx, dy = round(spot[0] - hx), round(spot[1] - hy)
                if any(abs(dx - tx) < 24 and abs(dy - ty) < 24 for tx, ty in tried):
                    continue
            tried.add((dx, dy))
            self.held_scouts.pop(key, None)
            self.log(f"{request_id}: {purpose} moves to a known {resource} patch at {dx},{dy} from home")
            raise MoveSite(purpose, dx, dy, spec.get("goal", ""))
        self.tried_sites.pop(key, None)
        if held_back:
            # Every patch it knows is someone else's ground. It knows only what it has been near, so look
            # round home (a trip ends only at a patch on its own ground: a field beside home may simply be
            # unseen, rehearsal 2026-10-22); after a couple of trips, wait and ask again.
            scouts = self.held_scouts.get(key, 0)
            if scouts < HELD_SCOUTS:
                self.held_scouts[key] = scouts + 1
                self.log(f"{request_id}: its known {resource} lies where it may not build; scouting its own ground")
                raise Scout(purpose, resource)
            self.log(f"{request_id}: its known {resource} lies where it may not build; waiting")
            raise Defer(f"{resource}_outside_its_ground")
        self.log(f"{request_id}: no known {resource} patch with room; scouting")
        raise Scout(purpose, resource)

    def _layout(self, site: Mapping[str, Any], request_id: str, purpose: str, fallback: Callable) -> dict:
        """Reuse a saved build, adapt one, or have one designed: every build of the job in the library is
        weighed against what the job needs here (companion/build_choice.py), and System One chooses, or
        the rules when it is not there: reuse what fits and works, adapt when there are gaps, design new
        only when nothing comes close."""
        spec = PURPOSES.get(purpose, {})
        built_in = BuiltIn(fallback, site)
        # A standalone block (one of its own, such as the oil block) is site-specific only while the game's own
        # layout fits; when it finds no room, a saved build (the library, the user's blueprint books) or the
        # designer lays it out, as for any other layout.
        site_specific = spec.get("site_specific") and not (spec.get("standalone") and built_in.plan() is None)
        designable = self.designer is not None and not site_specific
        # Before its power runs, the built-in layouts serve (smelting, stone, the first lab and steam
        # plant): proven, free and right first time. The designer comes in then only where they do not fit
        # its ground; after power, for the bigger builds (the user's choice, 2026-10-23: 26 of 29 designs in
        # a live game were simple drill and furnace rows, designed afresh at each new spot).
        early = not has_power(site)
        allowed = designable and not early
        reports = [] if site_specific else build_choice.weigh(self.catalog, site, purpose, reference=built_in.plan())
        choice = build_choice.decide(reports, designer=allowed, busy=build_choice.busy(self.catalog), purpose=purpose)
        how = ("site-specific wiring" if site_specific
               else "no saved layout fits; built-in before power" if choice.kind == "builtin" and designable and early
               and not choice.report else choice.why)
        options = build_choice.options(reports, designer=allowed, describe=lambda r: self._described(
            {"blueprint": r.build, "placement": r.placement}, site, purpose))
        if allowed and options:
            options.append({"id": DESIGN_NEW, "description": "Ask the layout designer for a brand new design for "
                            "this exact site: slower, costs money, could be better than the saved layouts."})
        probabilities: dict[str, float] = {}
        if self.chooser and len(options) > 1:
            try:
                picked, probabilities = self.chooser.choose(site, options)
                picked = self._sample(request_id, probabilities, picked)
                choice = (build_choice.chosen(picked, reports)
                          or (build_choice.Choice("new", None) if picked == DESIGN_NEW else choice))
                how = "system one"
            except Exception as exc:
                self.log(f"{request_id}: layout chooser failed ({exc}); using the rules")
        plan, blueprint = self._carry_out(site, request_id, purpose, choice, reports, built_in, how,
                                          probabilities, designable and early)
        picked = {"reuse": f"bp-{blueprint['id']}", "adapt": f"adapt-{choice.report.id}" if choice.report else None,
                  "new": DESIGN_NEW}.get(choice.kind)
        self.assignments[request_id] = {"blueprint_id": blueprint["id"], "choice": picked, "how": how,
                                        "probabilities": probabilities, "recorded": False,
                                        "world": site.get("world_id"), "npc": site.get("npc_id"),
                                        "engineer": site.get("npc_name")}
        return plan

    def _carry_out(self, site: Mapping[str, Any], request_id: str, purpose: str, choice: build_choice.Choice,
                   reports: list, built_in: "BuiltIn", how: str, probabilities: Mapping[str, float],
                   designer_if_stuck: bool) -> tuple[dict, dict]:
        """Build what was chosen. What cannot be had falls back: a failed or refused design to the built-in
        layout; a built-in layout that does not fit the ground to the designer (before power its only turn);
        and either, in the end, to the best saved build that fits, whatever its gaps."""
        best = next((r for r in reports if r.usable), None)
        if choice.kind == "reuse":
            return self._reuse(choice.report, request_id, how, reports, probabilities)
        if choice.kind == "builtin" and choice.report is not None and choice.report is not best:
            best = choice.report
        plan = source = brief = None
        if choice.kind in ("adapt", "new"):
            if DESIGN_WAIT_S and built_in.plan() is not None:   # (a layout to build at once meanwhile)
                plan, source, brief = self._designed_soon(site, request_id, purpose, choice, how)
            else:
                plan, source, brief = self._designed(site, request_id, purpose, choice, how)
        if plan is None:
            try:
                plan = built_in.build()
                source = plan.get("source", "starter-layout-planner")
            except layout_planner.PlanError as failed:
                if designer_if_stuck and choice.kind not in ("adapt", "new"):
                    # Its ground fits no built-in layout (unusual terrain): the designer's turn after all.
                    self.log(f"{request_id}: no built-in layout fits here ({str(failed)[:100]}); asking the designer")
                    turn = build_choice.decide(reports, designer=True, purpose=purpose)
                    if turn.kind not in ("adapt", "new"):
                        turn = build_choice.Choice("new", None, "no built-in layout fits")
                    plan, source, brief = self._designed(site, request_id, purpose, turn, turn.why)
                if plan is None:
                    if best is None:
                        raise failed from None
                    return self._reuse(best, request_id, "nothing else could be made here", reports, probabilities)
        return self._save(plan, site, request_id, purpose, source, brief, how)

    def _reuse(self, report: build_choice.Report, request_id: str, how: str, reports: list,
               probabilities: Mapping[str, float]) -> tuple[dict, dict]:
        blueprint = report.build
        plan = bp.instantiate(blueprint, report.placement, request_id)
        odds = ", ".join(f"{k.replace('bp-', '')[:8]} {v:.0%}" for k, v in sorted(
            probabilities.items(), key=lambda kv: -kv[1])) if probabilities else ""
        fit = sum(1 for r in reports if r.usable)
        self.log(f"{request_id}: reusing layout {blueprint['id'][:8]} ({how}; "
                 f"{fit} fit, {len(self.catalog)} saved" + (f"; odds {odds}" if odds else "") + ")")
        return plan, blueprint

    def _designed(self, site: Mapping[str, Any], request_id: str, purpose: str, choice: build_choice.Choice,
                  how: str) -> tuple[dict | None, str | None, dict | None]:
        """The designer's layout: a saved build adapted (the build where it stands here, its gaps and its
        notes in the brief), or a new design. None when the designer fails or its design is turned down:
        a failed design must not stall the engineer, the caller falls back."""
        key = build_choice.claim(self.catalog, choice, purpose)
        try:
            if choice.kind == "adapt":
                brief = build_choice.adapt_brief(choice.report, site)
                self.log(f"{request_id}: adapting layout {choice.report.id[:8]} ({how})")
                return self._design({**site, "start_from": brief}, purpose), "designer-adapt", brief
            return self._design(site, purpose), "designer-llm", None
        except Exception as exc:
            self.designer_failures = getattr(self, "designer_failures", 0) + 1
            self.log(f"{request_id}: designer failed ({str(exc)[:160]}); falling back")
            return None, None, None
        finally:
            build_choice.release(self.catalog, key)

    def _designed_soon(self, site: Mapping[str, Any], request_id: str, purpose: str, choice: build_choice.Choice,
                       how: str) -> tuple[dict | None, str | None, dict | None]:
        """The designer's layout if it comes within DESIGN_WAIT_S; else none now (the caller builds the layout
        at hand) and the design goes on meanwhile, into the library when it comes, for next time."""
        lock, came = threading.Lock(), threading.Event()
        state: dict[str, Any] = {"late": False, "result": (None, None, None)}

        def design() -> None:
            result = self._designed(site, request_id, purpose, choice, how)
            with lock:
                if not state["late"]:
                    state["result"] = result
                    came.set()
                    return
            plan, source, brief = result
            if plan is None:
                return
            try:
                self._save(plan, site, request_id, purpose, source, brief, "designed while the layout at hand was built")
            except Exception as exc:   # (a late design is only ever a spare)
                self.log(f"{request_id}: the late design was not kept ({str(exc)[:120]})")
        threading.Thread(target=design, name=f"design-{request_id}", daemon=True).start()
        came.wait(DESIGN_WAIT_S)
        with lock:
            if came.is_set():
                return state["result"]
            state["late"] = True
        self.log(f"{request_id}: the designer is taking over {DESIGN_WAIT_S} s; building the layout at hand now, "
                 "its design is kept for next time")
        return None, None, None

    def _save(self, plan: dict, site: Mapping[str, Any], request_id: str, purpose: str, source: str,
              brief: Mapping[str, Any] | None, how: str) -> tuple[dict, dict]:
        """A new layout into the library, with its job; an adapted one as a variant of its parent."""
        parent = brief.get("parent") if brief else None
        build = build_history.new_build(plan, site, source=source, purpose=purpose, parent=parent,
                                        addressed=(brief or {}).get("addressed") or (), need=(brief or {}).get("need"))
        blueprint = self.catalog.add(build)
        if parent and blueprint["id"] == parent:
            self.log(f"{request_id}: the designer kept layout {parent[:8]} as it was ({how})")
        elif parent:
            build_history.link_variant(self.catalog, parent, blueprint["id"])
            self.log(f"{request_id}: layout {parent[:8]} adapted into {blueprint['id'][:8]} ({how}); "
                     f"library now {len(self.catalog)}")
        else:
            self.log(f"{request_id}: new layout {blueprint['id'][:8]} from {source} ({how}); "
                     f"library now {len(self.catalog)}")
        return {**plan, "blueprint_id": blueprint["id"]}, blueprint

    @staticmethod
    def _described(candidate: Mapping[str, Any], site: Mapping[str, Any], purpose: str) -> str:
        """A saved layout as System One sees it: what it is and how it did, and (with the game's facts)
        what it makes, burns and draws at full speed."""
        text = bp.describe(candidate)
        facts = rates.of(site)
        if facts:
            counts: dict[str, int] = {}
            for entity in candidate["blueprint"]["entities"]:
                counts[entity["prototype"]] = counts.get(entity["prototype"], 0) + 1
            resource = PURPOSES.get(purpose, {}).get("resource") or FIRST_RESOURCE.get(purpose)
            note = rates.layout_note(counts, facts, bp._as_list(site.get("recipes")), resource)
            if note:
                text += " " + note
        return text

    def _design(self, site: Mapping[str, Any], purpose: str) -> dict:
        """The designer's layout; one placing what the engineer cannot make yet (solar panels before their
        research) would stall the build, so it is turned down like a failed one."""
        plan = self.designer(site)
        locked = bp.locked_items(bp.from_plan(plan, site, source="designer-llm", purpose=purpose), site)
        if locked:
            raise layout_planner.PlanError("design needs what cannot be made yet: " + ", ".join(locked))
        off = off_the_job(plan, site, purpose)
        if off:
            raise layout_planner.PlanError("design does not build the job: none of " + ", ".join(off))
        return plan

    def record(self, request_id: str, status: Mapping[str, Any]) -> bool:
        """Update a blueprint's record from the game's /npc-plan status row."""
        assignment = self.assignments.get(request_id)
        if not assignment or assignment["recorded"]:
            return False
        state = status.get("status")
        if state == "deferred" and str(status.get("reason", "")).startswith("designer:rejected"):
            state = "blocked"
        if state not in ("complete", "blocked", "fallback"):
            return False
        accepted = status.get("accepted_tick")
        stage_ticks = [t for t in bp._as_list(status.get("stage_completed_ticks")) if isinstance(t, int) and t >= 0]
        first = stage_ticks[0] - accepted if stage_ticks and isinstance(accepted, int) else None
        done = status.get("completed_tick")
        complete = done - accepted if isinstance(done, int) and isinstance(accepted, int) else None
        self.catalog.record(assignment["blueprint_id"], success=state == "complete", first_output_ticks=first,
                            complete_ticks=complete, reason=status.get("reason"),
                            use={"world": assignment.get("world"), "npc": assignment.get("npc"),
                                 "engineer": assignment.get("engineer"),
                                 "plan": status.get("plan_id") or request_id,
                                 "tick": accepted if isinstance(accepted, int) else None, "outcome": state,
                                 "reason": status.get("reason") if state != "complete" else None,
                                 "first_output_s": round(first / 60) if first is not None else None,
                                 "complete_s": round(complete / 60) if complete is not None else None})
        assignment["recorded"] = True
        self.log(f"{request_id}: layout {assignment['blueprint_id'][:8]} {state}"
                 + (f", first output after {first} ticks" if first is not None else "")
                 + (f" ({status.get('reason')})" if state != "complete" and status.get("reason") else ""))
        return True


def summary(planner: CatalogPlanner) -> str:
    return json.dumps({k: {**v, "probabilities": {o: round(p, 3) for o, p in v["probabilities"].items()}}
                       for k, v in planner.assignments.items()}, sort_keys=True)
