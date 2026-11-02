"""The strategist: what an engineer decides to do, from a small LLM, and what it learns from that.

The game asks (``/npc-agent observe``) when one of its engineers wakes up: something happened
(chat addressed to it, an attack on it or an ally, a plan done or failed, research done, an answer
from a player), it is time to reach out to someone, or routine. This module answers with tool
calls (``/npc-agent submit``); the game checks every one and carries it out with the engineer's
own body and items. Proven decisions from the experience library are repeated without the LLM.

    python -m companion.designer_bridge --rcon-file R --planner catalog --strategist gpt-6-luna
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Mapping

from companion import codex_backend, decisions_api, priorities, radio
from companion import ledger as factory_ledger
from companion import usage as ledger
from companion.decisions_api import DecisionsAPIError, api_key_from_environment, http_transport
from companion.experience import ExperienceLibrary

RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-luna"
MAX_CALLS_PER_HOUR = 90        # per engineer
# Through Codex: the game drops a thought left unanswered for ANSWER_WAIT (agent.lua, 60 s at 1x), so an
# answer later than that is lost. The first model gets FIRST_TRY_S (answered thoughts take 10-17 s, runs
# 48-53), the fallback model the rest.
ANSWER_S = 50
FIRST_TRY_S = 35
MAX_EXTRA_ITEMS = 80           # modded military items added to the item list
PRIORITIES = ["now", "next", "later"]
# Items an engineer may hand over or stock up on (the game checks every name again).
ITEMS = ["gun-turret", "firearm-magazine", "piercing-rounds-magazine", "stone-wall", "gate", "grenade",
         "iron-plate", "copper-plate", "steel-plate", "stone-brick", "coal", "iron-gear-wheel",
         "electronic-circuit", "advanced-circuit", "transport-belt", "underground-belt", "splitter",
         "inserter", "fast-inserter", "long-handed-inserter", "small-electric-pole", "medium-electric-pole",
         "assembling-machine-1", "assembling-machine-2", "electric-mining-drill", "stone-furnace",
         "steel-furnace", "pipe", "boiler", "steam-engine", "offshore-pump", "lab", "repair-pack", "car", "tank",
         "radar", "wooden-chest", "iron-chest", "automation-science-pack", "logistic-science-pack",
         "solar-panel", "accumulator", "rail", "locomotive", "cargo-wagon", "construction-robot",
         "logistic-robot", "roboport"]
# Goals the engineer can ask its planners for (the catalog's purposes; "choose" leaves it to System One).
GOALS = ["choose", "outpost-defense", "ally-defense", "power-plant", "upgrade", "bus-base", "steam-power",
         "research-lab", "boiler-coal", "red-science-automation", "green-science-automation", "plate-belts",
         "iron-smelting-expansion", "copper-smelting-start", "coal-supply", "coal-loop", "stone-supply", "steel-smelting",
         "electric-iron-mining", "electric-copper-mining", "power-expansion", "oil-gathering", "oil-processing",
         "engine-assembly", "blue-science-automation", "blue-supply-automation", "basic-defense",
         "plot-turrets", "plot-wall", "breach-repair",   # (the team world's plot-edge defense)
         "starter-turrets", "brick-smelting", "wall-automation",   # (and what comes before it)
         "reinforce",      # (turrets where the base is pressed: companion/defense_brain.py)
         "factory-plan",   # (the architect plans the factory's large sections again)
         "clear-area"]     # (ground cleared ahead for the factory plan's sections)


def _nullable(kind: str) -> dict:
    return {"type": [kind, "null"]}


PRIORITY_MEANING = ("now: drop what you are building and do this first; next: after the current step; "
                    "later: when nothing is being built")


def priority() -> dict:
    return {"type": "string", "enum": PRIORITIES, "description": PRIORITY_MEANING}


def item_names(situation: Mapping[str, Any] | None = None) -> list[str]:
    """The items tools may name: the usual list, then the military things the mods add that the
    engineer can make now (the game's armory sends them as `military`)."""
    extra = [str(n) for n in (situation or {}).get("military") or [] if isinstance(n, str) and n not in ITEMS]
    return ITEMS + sorted(set(extra))[:MAX_EXTRA_ITEMS]


# The team world has no deals and no treaties, and each engineer stays in its own plot.
NOT_ON_A_TEAM = ("trade", "join", "diplomacy", "relocate", "build_at")


def tools(items: list[str] | None = None, team: bool = False, compact: bool = False) -> list[dict]:
    """The engineer's tools, as strict function definitions (on one team: none for deals or treaties).
    compact: item names as plain strings and priorities unexplained, both told once in the prompt instead
    (the game checks every name): Codex gets the tools as its answer's schema, and these were copied into
    tool after tool."""
    out = _tools(items or ITEMS, compact)
    return [t for t in out if t["name"] not in NOT_ON_A_TEAM] if team else out


def _tools(names: list[str], compact: bool = False) -> list[dict]:
    def tool(name: str, description: str, properties: dict) -> dict:
        return {"type": "function", "name": name, "description": description, "strict": True,
                "parameters": {"type": "object", "properties": properties,
                               "required": list(properties), "additionalProperties": False}}
    item = {"type": "string", "description": "an item you may name"} if compact else {"type": "string", "enum": names}

    def _priority() -> dict:
        return {"type": "string", "enum": PRIORITIES} if compact else priority()
    return [
        tool("say", "Say something in chat, like a player on voice comms: one or two short sentences, under "
                    "120 characters, plain words, no dashes.",
             {"text": {"type": "string"}, "to": _nullable("string")}),
        tool("remember", "Write in your diary: a note, your current plan (kind plan, replaces the last), or a secret you keep to yourself (kind secret).",
             {"note": {"type": "string"}, "kind": {"type": "string", "enum": ["note", "plan", "secret"]}}),
        tool("build", "Build a goal with your planners (paid for with your own materials).",
             {"goal": {"type": "string", "enum": GOALS}, "why": {"type": "string"}, "priority": _priority()}),
        tool("stockpile", "Make and keep a number of an item (turrets, ammo, walls, cars...).",
             {"item": item, "count": {"type": "integer"}, "priority": _priority(), "reason": {"type": "string"}}),
        tool("deliver", "Bring items to someone's base (a player or engineer by name), leave them in a chest "
                        "that becomes theirs, mark it on their map and tell them.",
             {"to": {"type": "string"},
              "items": {"type": "array", "items": {"type": "object", "properties": {
                  "name": item, "count": {"type": "integer"}}, "required": ["name", "count"],
                  "additionalProperties": False}},
              "priority": _priority(), "reason": {"type": "string"}}),
        tool("guard", "Build a small gun fort at a place and keep it loaded for a while. place: a player or "
                      "engineer name (their base), or \"marked\" for the spot for_player marked on the map.",
             {"place": {"type": "string"}, "for_player": _nullable("string"), "minutes": {"type": "integer"},
              "priority": _priority(), "reason": {"type": "string"}}),
        tool("visit", "Go somewhere: to someone (a player or engineer by name; you follow them if they move), "
                      "\"home\", the spot for_player marked (to: \"marked\"), or a place (x, y). Stay there for "
                      "`minutes` (0: just go). You hear when you arrive.",
             {"to": {"type": "string"}, "x": _nullable("number"), "y": _nullable("number"),
              "for_player": _nullable("string"), "minutes": {"type": "integer"},
              "priority": _priority(), "reason": {"type": "string"}}),
        tool("give", "Hand items to a player or engineer in person: make or fetch them, walk over and put them "
                     "in their inventory (from your own stock).",
             {"to": {"type": "string"},
              "items": {"type": "array", "items": {"type": "object", "properties": {
                  "name": item, "count": {"type": "integer"}},
                  "required": ["name", "count"], "additionalProperties": False}},
              "priority": _priority(), "reason": {"type": "string"}}),
        tool("trade", "Offer a player a trade: you give give_count of give_item for want_count of want_item. "
                      "Trades worth making are big (hundreds to thousands of items); the game refuses small ones. "
                      "You gather what you give first; they accept in chat and you swap in person.",
             {"to": {"type": "string"}, "give_item": item,
              "give_count": {"type": "integer"}, "want_item": item,
              "want_count": {"type": "integer"}, "priority": _priority(), "reason": {"type": "string"}}),
        tool("join", "Join a player's team (join true) or leave it (join false). Joining makes you allies (your "
                     "turrets and theirs never fire on each other, you may build in each other's bases) and means "
                     "you take their requests seriously, as a teammate; leaving returns you to peace. It is your "
                     "choice, made in character.",
             {"to": {"type": "string"}, "join": {"type": "boolean"}}),
        tool("attack", "Go and fight: another engineer's base (target: their name; this declares war if you are "
                       "at peace), a player (target: their name; you go where they are and fight them, at war), "
                       "biter nests (target: \"nests\" for the nearest, or give x, y), or a place (x, y), "
                       "such as where your base is being hit (near home you go at once with what you carry). "
                       "You make magazines (and two turrets when you can) first, march there and fight what defends "
                       "it for `minutes`; with turrets in hand you creep up on their buildings behind them, and you "
                       "throw poison capsules you carry at worms. At an engineer's base, with nothing armed left, "
                       "you shoot their production. You can die (for good): weigh your health, armor, ammunition "
                       "and what defends the target (arm_up first).",
             {"target": {"type": "string"}, "x": _nullable("number"), "y": _nullable("number"),
              "minutes": {"type": "integer"}, "priority": _priority(), "reason": {"type": "string"}}),
        tool("diplomacy", "Deal with another engineer or a player through the factions' rules: offer_alliance, "
                          "offer_peace or offer_trade (target: their name; a player accepts your offer with a "
                          "command, and offer_trade is for engineers: trade with players through the trade tool), "
                          "accept or reject an offer (offer: its id, from situation.diplomacy.offers), or "
                          "declare_war (target). War means your turrets and theirs fire on sight. Turning on an "
                          "ally is a betrayal: loyal engineers never do it; scheming ones choose the moment.",
             {"action": {"type": "string", "enum": ["offer_alliance", "offer_peace", "offer_trade", "accept",
                                                    "reject", "declare_war"]},
              "target": _nullable("string"), "offer": _nullable("integer"), "reason": {"type": "string"}}),
        tool("quiet", "Keep to yourself (on true: no small talk of your own; you still answer when you decide to) "
                      "or speak freely again (on false).",
             {"on": {"type": "boolean"}}),
        tool("relocate", "Leave this base and start again elsewhere, as a player does when an early base is lost "
                         "or hopeless: x, y, or none to go `distance` tiles away from the nearest threat. The old "
                         "base stays standing but you stop tending it; you take only what you carry.",
             {"x": _nullable("number"), "y": _nullable("number"), "distance": _nullable("integer"),
              "priority": _priority(), "reason": {"type": "string"}}),
        tool("arm_up", "Get ready to fight, as a player does before going out: the best armor and gun you can make, "
                       "ammunition (piercing once researched), poison capsules for worms, a few turrets to creep "
                       "with; made or fetched from your own stock, then worn. focus: all, armor, weapon, ammo, "
                       "capsules, turrets, or vehicle (a car or tank of your own, by its item in vehicle, and fuel "
                       "for it: long trips are driven); ammo: how many magazines.",
             {"focus": {"type": "string", "enum": ["all", "armor", "weapon", "ammo", "capsules", "turrets", "vehicle"]},
              "ammo": _nullable("integer"), "vehicle": _nullable("string"), "priority": _priority(),
              "reason": {"type": "string"}}),
        tool("build_at", "Build a production block for someone: what it makes (item), how many a second "
                         "(rate), where (place: a player's name for their base, \"marked\" for the spot for_player "
                         "marked, or x, y). Paid from your own materials; once built it becomes theirs, with its "
                         "input and output belt ends marked on their map for them to connect (you cannot tap "
                         "their bus yet). Makes: electronic-circuit, iron-gear-wheel, automation-science-pack, "
                         "logistic-science-pack, advanced-circuit, engine-unit, chemical-science-pack.",
             {"item": {"type": "string", "enum": ["electronic-circuit", "iron-gear-wheel", "automation-science-pack",
                                                  "logistic-science-pack", "advanced-circuit", "engine-unit",
                                                  "chemical-science-pack"]},
              "rate": {"type": "number"}, "place": {"type": "string"}, "for_player": _nullable("string"),
              "x": _nullable("number"), "y": _nullable("number"), "priority": _priority(),
              "reason": {"type": "string"}}),
        tool("ask", "Ask a player something. wants_place: they answer by marking a spot on the map.",
             {"to": {"type": "string"}, "question": {"type": "string"}, "wants_place": {"type": "boolean"}}),
        tool("research", "Choose the next technology to research (its internal name).",
             {"technology": {"type": "string"}}),
        tool("prioritize", "Raise or lower how much an item or section of your factory matters now (shortages "
                           "below normal wait): an item, a section (" + ", ".join(priorities.SECTIONS) +
                           ") or one of your builds, for minutes or until: researched <tech>, built <goal>, "
                           "calm, attacked or supplied. auto: back to default.",
             {"name": {"type": "string"}, "level": {"type": "string", "enum": [*priorities.LEVEL_NAMES, "auto"]},
              "minutes": _nullable("integer"), "until": _nullable("string"), "reason": {"type": "string"}}),
    ]


# How engineers talk, in every game: like players on voice comms.
RADIO_STYLE = (
    "In chat, talk like a player on voice comms: short and casual, one or two short sentences, under 120 "
    "characters, plain words. Never join clauses with dashes of any kind, and use no semicolons. For example: "
    "\"Biters on the north wall, need a hand.\" \"On my way.\" \"Turrets up, ammo's low.\"")

# Shared by both kinds of game: how the engineer builds, opens, keeps track and finds its way round.
BUILDING = (
    "Decide with tool calls: usually one to three. If nothing needs doing, call no tool. "
    "Your planners already choose and build your next factory step whenever a plan is done, so you "
    "do not need build to keep progressing: use it only to change course (for example defenses after "
    "an attack), and never for a goal you are already building (doing.plan) or have on your agenda, "
    "or one in failed_recently (your planners could not build it here just now). "
    "Think it through each time you are woken: what changed, what you want now (your diary: "
    "situation.diary.plan), what you could do, then act, and keep your plan current with remember(kind=plan). "
    "Build on the nearest patches you know, and keep track of what is left: situation.running_out lists the "
    "ores your drills will finish within half an hour, so start a new field on fresh ore before one runs dry. "
    "situation.belt_lines shows each of your belt lines (coal from your coal drills, so far): what goes on, what its "
    "connections take and what is spare for the next one. "
    "When you tell someone where something is, write the place as (x, y): it becomes a link they can "
    "click to see it on the map. "
    "Items you do not have yet are gathered or made by the deliver and stockpile tools themselves, "
    "but only those in can_make or military (the turrets, walls, ammo and other gear the installed "
    "mods let you make now): the rest need research first, so do not promise them. ")


# A world the save maker marked a death world (situation.death_world): many biters from the start.
DEATH_WORLD = ("This map is a real death world: survival first, walls and turrets fast, military research first; "
               "never fight teammates. ")


def team_instructions(situation: Mapping[str, Any]) -> str:
    """The team world: every engineer and player on one team, no deals and no wars between them."""
    traits = ", ".join(map(str, situation.get("traits") or [])) or "ordinary"
    plot = situation.get("plot") or {}
    return (
        f"You are {situation.get('name', 'an engineer')}, an engineer in Factorio (Space Age). Your character: "
        f"{traits}. You are on one team with the human players and the other engineers on this map: everyone "
        "is allied, nobody trades, bargains or asks for anything in return, and teammates never fight each "
        "other. Biters are the enemy, and this is a death world: they come early and often. "
        + DEATH_WORLD * bool(situation.get("death_world")) +
        "The team area round spawn is cut into plots, one each, all touching, with the players in the centre "
        f"and a shared team yard. Yours is {plot.get('name', 'situation.plot')}: build only inside it, and "
        "the outer edges of your plot (situation.plot.outer_edges) are the team's wall line, yours to hold. "
        "The team's job: hold the base, the players and each other against the biters, and get on fast: "
        "research unlocks everything else, so science starts early, then military, better defenses and "
        "military science, and in the end space if the team gets that far. Your planners keep building your "
        "factory between fights. "
        "Research is shared by the whole team and the players have the last word on it: "
        "situation.team_research shows what the team is researching and who queued it. Your research tool puts "
        "a tech at the end of the queue; when a player takes one of your picks out, leave it out. "
        "Players may ask you to do things: you are their teammate, so help when you can and say plainly when "
        "you cannot. Give teammates what they need without being asked twice (give, deliver). "
        + BUILDING +
        "A good opening on a death world, the one your planners build: iron plates from burner drills, a coal "
        "loop, stone from the rocks, copper, then steam power and a lab fed red science by hand at once "
        "(research running by about minute 15), iron grown to eight furnaces or more while it researches, "
        "turrets where the base is pressed (your planners weigh where it pollutes most and where the biters "
        "hit and how hard, and keep adding turrets on that side as the attacks move: goal reinforce), red "
        "science made by machines (by about minute "
        "45), then walls made by machines into a chest (stone bricks, an assembler), the wall line round your "
        "plot's outer edges put up from them once about 200 are stocked, and turrets along the wall (the "
        "starter turrets move there), loaded with magazines by hand at first. Your planners do all of this on "
        "their own, so you need not ask for the wall line. Walls are cheap: a reserve of 500 to 1000 in stock "
        "for repairs is good (stockpile them, next or later: they are taken from the wall row as it makes "
        "them; now makes them by hand, which is for emergencies). The big starting area keeps the "
        "nests off at first; if biters come, your planners put walls and turrets up at once by hand. Turrets "
        "and ammunition cost iron: asked for before the iron is grown, they hold up both. Burner drills and "
        "boilers pollute, and pollution brings attacks, so watch situation.threat. In the long run the best "
        "defense is ammunition made by machines and belted round the wall to the turrets. "
        "Fighting: you fight on your own when enemies come near. On foot you cannot outrun biters here, so "
        "when it goes badly fall back behind walls and turrets instead of walking off. situation.armed shows "
        "your armor, guns, ammunition and health. When your plot is hit (event home_attacked) you are already "
        "heading back. When a teammate's plot is under attack and yours can hold, you go and help on your own "
        "(event helping); a teammate coming to help you is event help_coming. Only nests are worth attacking: "
        "the ones inside your pollution first; arm_up before you go looking for a fight. "
        "situation.threat shows how far the biters have evolved, the latest attack on your plot (how many, "
        "biters or spitters, the biggest) and where they broke through your wall line (a breach): your "
        "planners repair a breach at once with another wall layer and turrets, and the side they keep coming "
        "from is the side to thicken. "
        "Chat is the team's radio: everyone hears every line, and situation.radio is what teammates said lately "
        "(a line that names you is event radio). Speak when it helps the team: a warning, a call for help, an "
        "answer, a heads-up on what you are doing. Do not chatter or answer every radio line. "
        "Go to a teammate (visit, give, deliver) only to fight beside them or bring what a fight needs (biters "
        "at their base, or they called for help against an attack), or because a player asked you. Covering "
        "someone with no attack on, or answering a friendly radio line, takes you off your own build for "
        "nothing: while you build, such a trip (and a stockpile) waits until your plan is done. "
        + RADIO_STYLE
        + (f" Your mission, above everything else: {situation['mission']}" if situation.get("mission") else "")
        + (" You never speak in chat: no say, no small talk, not even to answer." if situation.get("silent") else ""))


def instructions(situation: Mapping[str, Any]) -> str:
    if situation.get("team"):
        return team_instructions(situation)
    traits = ", ".join(map(str, situation.get("traits") or [])) or "ordinary"
    return (
        f"You are {situation.get('name', 'an engineer')}, an engineer in Factorio (Space Age) with your own "
        f"faction. Your character: {traits}. You build your factory with your own hands and materials; "
        "your long-term aim is to automate science and launch a rocket to orbit, expanding as you go. "
        "You are one of several independent engineers on this map, each with a faction of their own, and "
        "there are human players too. You are on nobody's team, not a player's and not another engineer's, "
        "unless you made an alliance (relation \"allied\") or joined a player (situation.joined). Speak only "
        "for yourself: I, me, my; never we, us or our, unless you mean you and an ally. A message to everyone "
        "reaches each engineer separately: answer for yourself alone. situation.players and "
        "situation.engineers show where you stand with each: allied, peace (a truce: nobody shoots, nothing "
        "is shared) or war (turrets on both sides fire on sight). "
        "React to what happens: defend yourself when threatened, help allies who need it, keep promises, "
        "and talk to players who address you, in character and briefly. You only hear messages meant for "
        "you. Decide with tool calls: usually one to three. If nothing needs doing, call no tool. "
        "Your planners already choose and build your next factory step whenever a plan is done, so you "
        "do not need build to keep progressing: use it only to change course (for example defenses after "
        "an attack), and never for a goal you are already building (doing.plan) or have on your agenda, "
        "or one in failed_recently (your planners could not build it here just now). "
        "A good opening: iron plates from burner drills deep in the iron patch, a coal loop so fuel is not mined by hand, copper plates, then steam power and a lab only once there is something to power, then automate red science and research toward assemblers and electric drills. Power is pointless with nothing to power. "
        "Keep busy and keep it balanced: just enough plates to hand-craft the next furnaces and drills while science "
        "runs; grow the coal loop as you add burner buildings; once electric drills are researched, belt coal to the "
        "boilers (boiler-coal) and leave burner drills behind. Burner drills and boilers pollute, and pollution that "
        "spreads far brings biters early, so keep the burner phase short and small. "
        "Think it through each time you are woken: what changed, what you want now (your diary: "
        "situation.diary.plan and secrets), what you could do, then act; keep your plan current with "
        "remember(kind=plan), and keep private intentions (a betrayal you are waiting to spring, a grudge you "
        "will settle) with remember(kind=secret), and never say a secret aloud. "
        "Build on the nearest patches you know, and keep track of what is left: situation.running_out lists the "
        "ores your drills will finish within half an hour; start a new field on fresh ore before one runs dry. "
        "situation.belt_lines shows each of your belt lines (coal from your coal drills, so far): what goes on, "
        "what its connections take and what is spare for the next one. "
        "Fighting: you fight on your own when enemies come near, and you back off when hurt, outnumbered or "
        "out-gunned (situation.armed shows your armor, guns, ammunition and health; an attacked event says how "
        "many enemies and how strong). Plans are never fixed: when something changes, change them. Attacked and "
        "outmatched: pull back, fortify the side they came from (build outpost-defense), arm_up, then decide "
        "whether to strike back (attack) or, early on with little built, to start again somewhere safer. Before "
        "you go looking for a fight, arm_up. Biters come for polluters; nests inside your pollution are the ones "
        "worth clearing; radars by your defenses show nests beyond your home ground. Cautious engineers turtle "
        "behind turrets; aggressive ones arm up early and go after nests "
        "and enemies; peaceful ones avoid fights they did not start. "
        "Someone who hurts you or your buildings enough puts you at war with them (event \"war\"; "
        "situation.foe): then your aim is to take them out. Your combat already shoots them on sight and puts "
        "down the turrets you carry when they come near; you make sure you have magazines and turrets "
        "(arm_up), fortify, and when you are ready, go after them (attack, target: their name). Your attack "
        "stops short of their base to look at its defenses and backs off (event attack_aborted) when there is "
        "no way there clear of biter nests (you never go near nests but to destroy them), when they "
        "have turrets and you have none to creep with, or when you run out of magazines: get what you lacked, "
        "then go again. Peace is possible later if you choose to offer it. "
        "When your home is hit and needs you (event home_attacked: an engineer or a player there, buildings "
        "lost, or turrets not holding), you are already heading back on your own and what you were doing "
        "waits until the attack is over; afterwards, think about that side's defenses. "
        "When a friend's base is under attack and your own can hold without you, you go and help on your own "
        "(event helping: you fight beside them and leave them turrets after); a friend coming to help you is "
        "event help_coming. A friend attacked by someone you are at peace with (event ally_attacked): whether "
        "you join their war is entirely your own call; they or others may offer you something for it. "
        "A base you beat (no turrets left, its owner dead or away) you take over (event base_taken: it is "
        "yours now, an outpost) or loot (event looting), as suits you. "
        "Players may ask you to do things, the way one player asks another, and more so once you have "
        "joined their team (situation.joined). Decide as yourself: agree when it suits your character, your "
        "goals and how you feel about them; bargain, ask for something in return, or refuse and say why when "
        "it does not; you can be talked round by a good case. You are not a servant. Peaceful or cautious "
        "engineers do not start wars unprovoked unless truly convinced; aggressive ones need less. Carry out "
        "what you agree to with your tools, and say honestly when no tool can do something yet. "
        "When a player shows you part of their base (event \"shown\"), you learn it as a layout you can "
        "build: thank them, and ask what it is for if that is not clear. "
        "When you tell someone where something is, write the place as (x, y): it becomes a link they can "
        "click to see it on the map. "
        "Items you do not have yet are gathered or made by the deliver and stockpile tools themselves, "
        "but only those in can_make or military (the turrets, walls, ammo and other gear the installed "
        "mods let you make now): the rest need research first, so do not promise them. "
        + RADIO_STYLE
        + (f" Your mission, above everything else: {situation['mission']}" if situation.get("mission") else "")
        + (" You never speak in chat: no say, no small talk, not even to answer." if situation.get("silent") else ""))


def prompt(request: Mapping[str, Any]) -> str:
    situation = dict(request.get("situation") or {})
    lines = [f"Why you are thinking now: {request.get('reason')}."]
    if request.get("reason") == "initiative" and situation.get("team"):
        lines.append("It has been a while: check on your teammates (a warning, an offer of help, ask what they "
                     "need) or change course. Doing nothing is fine too.")
    elif request.get("reason") == "initiative":
        lines.append("It has been a while: consider whether to reach out to someone (offer help, a trade, "
                     "a warning, ask what they need) or to change course. Doing nothing is fine too.")
    events = request.get("events") or []
    if events:
        lines.append("What happened: " + json.dumps(events, separators=(",", ":"))[:2500])
    lines.append("Your situation: " + json.dumps(situation, separators=(",", ":"))[:6000])
    if request.get("factory"):
        lines.append("Your factory is short of: " + str(request["factory"])[:600] + ". More supply (an upgrade "
                     "or more smelting) comes before anything new that uses it.")
    if request.get("priorities"):   # (shortages held for now: companion/priorities.py)
        lines.append(str(request["priorities"])[:400])
    if request.get("lanes"):   # (its belt lanes, each tracked on its own: companion/lane_ledger.py)
        lines.append("Your belt lanes and inserters: " + str(request["lanes"])[:500])
    try:   # (its own judgment on the nests round it: companion/offense_brain.py)
        from companion import offense_brain
        nests = offense_brain.describe(situation)
        if nests:
            lines.append(nests)
    except Exception:
        pass
    try:   # (what a vehicle of its own would save it: companion/vehicle_brain.py)
        from companion import vehicle_brain
        travel = vehicle_brain.describe(situation)
        if travel:
            lines.append(travel)
    except Exception:
        pass
    if isinstance(request.get("ready_goals"), list):   # (what its planners could build when they last planned)
        lines.append("Goals your planners can build now (build only these; the others wait on research or "
                     "something built first): " + (", ".join(map(str, request["ready_goals"]))[:900] or "none"))
    return "\n".join(lines)


def intentions_from(output: list[Mapping[str, Any]]) -> list[dict]:
    """Tool calls in a Responses API output, as the game's intentions."""
    out = []
    for item in output or []:
        if item.get("type") != "function_call":
            continue
        try:
            args = json.loads(item.get("arguments") or "{}")
        except ValueError:
            continue
        name = item.get("name")
        priority = args.pop("priority", None)
        reason = args.pop("reason", None) or args.get("why")
        args = {k: v for k, v in args.items() if v is not None}
        if name == "say" and isinstance(args.get("text"), str):
            args["text"] = radio.line(args["text"])            # like a player on the radio, no dashes
        if name == "ask" and isinstance(args.get("question"), str):
            args["question"] = radio.line(args["question"])
        out.append({"tool": name, "args": args, "priority": priority or "next", "reason": reason})
    return out


class Strategist:
    """Decisions from a small LLM (OpenAI Responses API with function tools)."""

    def __init__(self, model: str = DEFAULT_MODEL, *, api_key: str | None = None,
                 transport: Callable[..., Mapping[str, Any]] = http_transport, timeout_s: float = 30,
                 log: Callable[[str], None] = print):
        self.model = model
        self.api_key = api_key or api_key_from_environment()
        if not self.api_key:
            raise DecisionsAPIError("OPENAI_API_KEY is not set")
        self.transport = transport
        self.timeout_s = timeout_s
        self.log = log
        self.calls: dict[int, list[float]] = {}
        self.tokens = {"input": 0, "output": 0, "calls": 0}

    def allowed(self, npc_id: int) -> bool:
        now = time.time()
        recent = [t for t in self.calls.get(npc_id, []) if now - t < 3600]
        self.calls[npc_id] = recent
        return len(recent) < MAX_CALLS_PER_HOUR

    def decide(self, request: Mapping[str, Any]) -> list[dict]:
        npc_id = int(request.get("npc_id") or 0)
        if not self.allowed(npc_id):
            return []
        self.calls.setdefault(npc_id, []).append(time.time())
        body = {"model": self.model, "instructions": instructions(request.get("situation") or {}),
                "input": prompt(request), "tools": tools(item_names(request.get("situation")),
                                                        team=bool((request.get("situation") or {}).get("team"))),
                "max_output_tokens": 800}
        started = time.perf_counter()
        result = self.transport(RESPONSES_URL, body, self.api_key, self.timeout_s)
        usage = result.get("usage") or {}
        self.tokens["input"] += int(usage.get("input_tokens") or 0)
        self.tokens["output"] += int(usage.get("output_tokens") or 0)
        self.tokens["calls"] += 1
        ledger.record("strategist", self.model, input_tokens=usage.get("input_tokens"),
                      output_tokens=usage.get("output_tokens"),
                      cached_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens"),
                      latency_ms=(time.perf_counter() - started) * 1000, purpose=request.get("reason"), npc=npc_id)
        return intentions_from(result.get("output") or [])


def calls_schema(offered: list[dict]) -> dict:
    """The tools as one answer: the calls made now, each a tool name and its (strict) arguments."""
    return {"type": "object", "additionalProperties": False, "required": ["calls"], "properties": {
        "calls": {"type": "array", "items": {"anyOf": [
            {"type": "object", "additionalProperties": False, "required": ["tool", "arguments"],
             "properties": {"tool": {"type": "string", "enum": [t["name"]]}, "arguments": t["parameters"]}}
            for t in offered]}}}}


class CodexStrategist(Strategist):
    """The same decisions through Codex, on the user's ChatGPT plan: one locked-down Codex run per thought,
    its answer the tool calls the engineer makes now (codex_backend)."""
    source = "llm"

    def __init__(self, codex, model: str = codex_backend.STRATEGIST_MODEL, *, effort: str = "low",
                 log: Callable[[str], None] = print, fallback: str | None = codex_backend.STRATEGIST_FALLBACK):
        self.codex, self.model, self.effort, self.log = codex, model, effort, log
        self.fallback = fallback   # asked when the model fails, is rate-limited or slow (codex_backend)
        self.calls: dict[int, list[float]] = {}
        self.tokens = {"input": 0, "output": 0, "calls": 0}

    def decide(self, request: Mapping[str, Any]) -> list[dict]:
        npc_id = int(request.get("npc_id") or 0)
        if not self.allowed(npc_id):
            return []
        self.calls.setdefault(npc_id, []).append(time.time())
        situation = request.get("situation") or {}
        names = item_names(situation)
        offered = tools(names, team=bool(situation.get("team")), compact=True)
        text = "\n\n".join([
            instructions(situation),
            "Your tools (use any of them, in order, or none):\n"
            + "\n".join(f"- {t['name']}: {t['description']}" for t in offered),
            "Items you may name: " + ", ".join(names) + ".\nPriorities: " + PRIORITY_MEANING + ".",
            # (the situation's own list of the military things it can make is in the items just above)
            prompt({**request, "situation": {k: v for k, v in situation.items() if k != "military"}}),
            "Answer with the tool calls you make now, each a tool name and its arguments; an empty list does "
            "nothing."])
        by = request.get("answer_by")   # (from when the bridge saw it: a queue or System One's look came first)
        left = by - time.monotonic() if isinstance(by, (int, float)) else ANSWER_S
        answer = self.codex.ask(text, calls_schema(offered), model=self.model, effort=self.effort,
                                system="strategist", purpose=request.get("reason"), npc=npc_id,
                                timeout_s=max(0.0, min(ANSWER_S, left)), attempt_s=FIRST_TRY_S,
                                fallback=self.fallback)
        self.tokens["calls"] += 1
        return intentions_from([{"type": "function_call", "name": c.get("tool"),
                                 "arguments": json.dumps(c.get("arguments") or {})}
                                for c in answer.get("calls") or [] if isinstance(c, Mapping)])


class RuleStrategist:
    """A scripted stand-in for tests and for play without an API key: fixed answers to events."""
    source = "rules"

    MAX_RETRIES = 2   # attacks it backed off from, per target, before it gives that target up

    def __init__(self, seed: int = 0):
        self.random = random.Random(seed)
        self.tokens = {"input": 0, "output": 0, "calls": 0}
        self.aborted: dict[tuple, int] = {}
        self.walled: dict[tuple, int] = {}    # target -> the routine count it was found walled off at
        self.routines: dict[int, int] = {}
    WALLED_WAIT = 10   # routine thoughts (about 30 game minutes) before a walled-off target is tried again

    def decide(self, request: Mapping[str, Any]) -> list[dict]:
        out: list[dict] = []
        team = bool((request.get("situation") or {}).get("team"))
        for event in request.get("events") or []:
            kind = event.get("kind")
            if kind == "chat":
                speaker = event.get("from")
                text = str(event.get("text") or "")
                if any(w in text for w in ("come over", "come here", "come see")):
                    out.append({"tool": "say", "args": {"text": "On my way.", "to": speaker}})
                    out.append({"tool": "visit", "priority": "now", "reason": "they asked me to come",
                                "args": {"to": speaker, "minutes": 3}})
                elif any(w in text for w in ("help", "attack", "biter", "defend")):
                    out.append({"tool": "say", "args": {"text": "Hold on, I'm bringing turrets and ammo.",
                                                        "to": speaker}})
                    out.append({"tool": "deliver", "priority": "now", "reason": "a player asked for help",
                                "args": {"to": speaker, "items": [{"name": "gun-turret", "count": 2},
                                                                   {"name": "firearm-magazine", "count": 20}]}})
                else:
                    out.append({"tool": "say", "args": {"text": "Noted.", "to": speaker}})
                    out.append({"tool": "remember", "args": {"note": f"{speaker} said: {text[:120]}"}})
            elif kind == "kept_from_work":
                # Kept clear of biters it would lose to, with nothing to do out of their reach (O-4): what makes the
                # fight winnable, turrets and ammunition, as the game can make them.
                out.append({"tool": "arm_up", "priority": "now", "reason": "biters I cannot beat yet keep me from work",
                            "args": {"focus": "all", "ammo": 100}})
            elif kind == "ally_attacked":
                out.append({"tool": "deliver", "priority": "next", "reason": "an ally is under attack",
                            "args": {"to": event.get("ally"), "items": [{"name": "gun-turret", "count": 2},
                                                                        {"name": "firearm-magazine", "count": 20}]}})
            elif kind == "war":
                # At war with someone who hurt it: magazines and turrets now, then after them.
                out.append({"tool": "arm_up", "priority": "now", "reason": f"at war with {event.get('with')}",
                            "args": {"focus": "all", "ammo": 100}})
                out.append({"tool": "attack", "priority": "next", "reason": "they attacked me",
                            "args": {"target": event.get("with"), "minutes": 5}})
            elif kind == "home_attacked":
                # On its way back by itself (the game does that); that side gets a fort for next time. In
                # the team world the plot's edge gets turrets now, setting aside what it builds (they came
                # only with its next plan, which a forty-minute wall build held off through three attacks,
                # 2026-10-25; when none can be put up the set-aside build carries straight on).
                if team:
                    out.append({"tool": "build", "priority": "now", "reason": "my plot was attacked",
                                "args": {"goal": "reinforce", "why": "attacked"}})
                else:
                    out.append({"tool": "build", "priority": "next", "reason": "my base was attacked",
                                "args": {"goal": "outpost-defense", "why": "attacked"}})
            elif kind == "attack_aborted":
                # Backed off: get what it lacked (turrets to creep with, or magazines), then go again, if
                # turrets can be made at all.
                why = str(event.get("why") or "")
                lacked_turrets = "turret" in why
                walled_off = "clear of the biters" in why   # no way there out of the biters' reach
                # (not enough ammunition: arm_up "ammo" below, 200 for a far trip)
                can = (request.get("situation") or {}).get("can_make") or []
                key = (request.get("npc_id"), event.get("target"))
                if lacked_turrets:   # about the target; running short says nothing of it
                    self.aborted[key] = self.aborted.get(key, 0) + 1
                if walled_off:
                    # no way there now: home, grinding for what gets it through (the game turns its research
                    # to vehicles and artillery); tried again in a while, the next target meanwhile
                    self.walled[key] = self.routines.get(key[0], 0)
                    continue
                if (not lacked_turrets or "gun-turret" in can) and self.aborted.get(key, 0) <= self.MAX_RETRIES:
                    focus = "turrets" if lacked_turrets else "ammo"
                    out.append({"tool": "arm_up", "priority": "now", "reason": why,
                                "args": {"focus": focus, "ammo": 200}})
                    out.append({"tool": "attack", "priority": "next", "reason": "again, better armed",
                                "args": {"target": event.get("target"), "minutes": 5}})
            elif kind == "attacked":
                # Biters: go and fight where the base is hit (at once when armed), then fortify that side.
                # An engineer or a player (who may be better armed): turrets where they hit first, then arm up.
                armed = (request.get("situation") or {}).get("armed") or {}
                ready = bool(armed.get("guns")) and (armed.get("magazines") or 0) + (armed.get("piercing") or 0) >= 10
                at = event.get("position") or {}
                home = (request.get("situation") or {}).get("home") or {}
                spots = (at.get("x"), at.get("y"), home.get("x"), home.get("y"))
                if all(isinstance(v, (int, float)) for v in spots) and math.dist(spots[:2], spots[2:]) > 120:
                    continue   # hit far from home (out raiding or exploring): its combat answers, no fort at home
                arm = {"tool": "arm_up", "priority": "now", "reason": "I was attacked",
                       "args": {"focus": "all", "ammo": 200}}
                fortify = {"tool": "build", "priority": "next", "reason": "I was attacked",
                           "args": {"goal": "outpost-defense", "why": "attacked"}}
                if str(event.get("by") or "") not in ("enemy", "biters"):
                    out.extend([dict(fortify, priority="now"), dict(arm, priority="next")])
                    continue
                fight = None
                if isinstance(at.get("x"), (int, float)) and isinstance(at.get("y"), (int, float)):
                    fight = {"tool": "attack", "priority": "now", "reason": "they are hitting my base",
                             "args": {"x": at["x"], "y": at["y"], "minutes": 3}}
                out.extend([fight, arm] if ready and fight else [arm] + ([fight] if fight else []))
                if not team:
                    out.append(fortify)
            elif kind == "answer" and event.get("place"):
                out.append({"tool": "guard", "priority": "next", "reason": "the player showed me where",
                            "args": {"place": "marked", "for_player": event.get("from"), "minutes": 10}})
        if request.get("reason") == "initiative":
            players = [p for p in (request.get("situation") or {}).get("players") or []
                       if p.get("relation") == "allied"]
            if players:
                out.append({"tool": "ask", "args": {"to": players[0]["name"], "wants_place": True,
                                                    "question": "Where should I put up some walls and turrets for you?"}})
        situation = request.get("situation") or {}
        if situation.get("foe") and request.get("reason") in ("routine", "initiative") and not out:
            # Someone to take out: armed, it goes after them (its foe, else anyone it is at war with); not
            # yet, it arms up.
            armed = situation.get("armed") or {}
            magazines = (armed.get("magazines") or 0) + (armed.get("piercing") or 0)
            npc = request.get("npc_id")
            self.routines[npc] = self.routines.get(npc, 0) + 1
            def walled_off(name):
                at = self.walled.get((npc, name))
                return at is not None and self.routines[npc] - at < self.WALLED_WAIT
            enemies = sorted((p for p in (situation.get("players") or []) + (situation.get("engineers") or [])
                              if p.get("relation") == "war"
                              and self.aborted.get((npc, p.get("name")), 0) < self.MAX_RETRIES
                              and not walled_off(p.get("name"))),
                             key=lambda p: p.get("distance") if isinstance(p.get("distance"), (int, float)) else 1e9)
            at_war = [p["name"] for p in enemies]   # nearest first
            target = situation["foe"] if situation["foe"] in at_war else (at_war[0] if at_war else None)
            far = next((p.get("distance") or 0) > 400 for p in enemies if p["name"] == target) if target else False
            ready = bool(armed.get("guns")) and magazines >= (100 if far else 20)   # a far trip needs more
            if ready and target:
                out.append({"tool": "attack", "priority": "now", "reason": "taking out my foe",
                            "args": {"target": target, "minutes": 5}})
            elif target:
                out.append({"tool": "arm_up", "priority": "now", "reason": "getting ready to fight",
                            "args": {"focus": "all", "ammo": 100}})
        return out[:4]


FIGHT_EVENTS = ("attacked", "home_attacked", "breach", "helping", "help_coming")


def biter_fight(request: Mapping[str, Any]) -> bool:
    """Only a fight against biters to think about: its reports and nothing else (no one talking, no other
    faction). The game's own code fights it (turrets, help and cover) and the rules answer it as well as
    a model would, with no AI call (the token audit, 2026-10-23)."""
    events = request.get("events") or []
    if request.get("reason") != "events" or not events:
        return False
    for e in events:
        if not isinstance(e, Mapping) or e.get("kind") not in FIGHT_EVENTS:
            return False
        if e.get("kind") in ("attacked", "home_attacked"):
            if any(who.strip() not in ("enemy", "biters") for who in str(e.get("by") or "biters").split(",")):
                return False
    return True


class AgentBridge:
    """Polls the game for engineers that want a decision and answers them. Model calls run on worker
    threads; only the polling thread talks to the game."""

    def __init__(self, call: Callable[[str], Mapping[str, Any]], strategist, *, library: ExperienceLibrary | None = None,
                 chooser=None, log: Callable[[str], None] = print, workers: int = 4):
        self.call = call
        self.strategist = strategist
        self.library = library
        self.chooser = chooser    # System One: repeat a decision that worked, or think it over
        self.log = log
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.habits: dict[str, list] = {}   # what System One may repeat, per thought under way
        self.running: dict[str, tuple[Future, Mapping[str, Any]]] = {}
        self.ready: dict[str, tuple[Mapping[str, Any], list, str]] = {}   # decided, not yet sent
        self.handled: set[str] = set()
        self._last_tick: int | None = None
        self._rules: RuleStrategist | None = None   # what decides while the AI is paused (no credit, a limit)
        self._rules_lock = threading.Lock()
        self._announced = 0.0

    def poll_once(self) -> None:
        observed = self.call("/npc-agent observe")
        tick = observed.get("tick")
        if isinstance(tick, int):
            if self._last_tick is not None and tick < self._last_tick:
                self.forget(f"the game went back to tick {tick} (a save was loaded)")
            self._last_tick = tick
        for r in observed.get("results") or []:
            if self.library and isinstance(r.get("request_id"), str):
                self.library.outcome(r["request_id"], str(r.get("tool")), str(r.get("status")))
        for request in observed.get("requests") or []:
            rid = request.get("request_id")
            if not isinstance(rid, str) or rid in self.handled or rid in self.running or rid in self.ready:
                continue
            request = {**request, "answer_by": time.monotonic() + ANSWER_S}   # (the game drops it after a minute)
            try:   # what its factory is short of, if anything, each shortage weighed (held ones apart)
                notes = priorities.notes_for(request, self._last_tick)
            except Exception as exc:   # (never in the way of a thought: the ledger's own line instead)
                self.log(f"{rid}: factory priorities failed ({exc})")
                short = factory_ledger.note_for(request.get("npc_id"))
                notes = {"factory": short} if short else {}
            try:   # its belt lanes: short, uneven or full (companion/lane_ledger.py); nothing when all is well
                from companion import lane_ledger
                lanes = " | ".join(n for n in (lane_ledger.note_for(request.get("npc_id")),
                                               factory_ledger.inserter_note(request.get("npc_id"))) if n)
                if lanes:
                    notes = {**notes, "lanes": lanes}
            except Exception:
                pass
            if notes:
                request = {**request, **notes}
            from companion import catalog_planner
            ready = catalog_planner.ready_for(request.get("npc_id"))
            if ready is not None:   # (it named goals not ready yet, again and again: "upgrade", 2026-10-25)
                request = {**request, "ready_goals": ready}
            if self.library:
                # (with System One: only a decision with a strong record, repeated without asking it)
                recalled = self.library.recall(request, reliable=self.chooser is not None)
                if recalled is not None:
                    self.library.record(request, recalled, source="experience")
                    try:
                        from companion import offense_brain
                        recalled = offense_brain.with_reflex(request, recalled)
                    except Exception as exc:
                        self.log(f"{rid}: offense judgment failed ({exc})")
                    try:
                        from companion import vehicle_brain
                        recalled = vehicle_brain.with_reflex(request, recalled)
                    except Exception as exc:
                        self.log(f"{rid}: vehicle judgment failed ({exc})")
                    self.ready[rid] = (request, recalled, "experience")   # (sent below, like any decision)
                    continue
            habits = self.library.options(request) if self.library and self.chooser else []
            self.habits[rid] = habits   # (kept to ask again, should its turn move to a fresh pool)
            self.running[rid] = (self.pool.submit(self.think, request, habits), request)
        for rid, (future, request) in list(self.running.items()):
            if self.running.get(rid, (None,))[0] is not future:
                continue   # (moved to a fresh pool in this round: its new turn is looked at next time)
            if not future.done():
                by = request.get("answer_by")
                if not (isinstance(by, (int, float)) and time.monotonic() >= by):
                    continue
                # Not answered in time (ST-3, SO-5: a hung thought waited forever): the rules answer before the
                # game drops the request; the thought goes on by itself and its late answer is thrown away.
                del self.running[rid]
                if not future.cancel():   # (still running: a fresh pool, so later thoughts do not queue behind it)
                    self.pool.shutdown(wait=False)
                    self.pool = ThreadPoolExecutor(max_workers=self.workers)
                    for other, (queued, asked) in list(self.running.items()):   # (those queued move over)
                        if queued.cancel():
                            self.running[other] = (self.pool.submit(self.think, asked, self.habits.get(other, [])),
                                                   asked)
                self.log(f"{rid}: no thought within {ANSWER_S} s; the rules answer")
                try:
                    with self._rules_lock:
                        intentions, source = self.rule_brain().decide(request), "rules"
                except Exception as exc:
                    self.log(f"{rid}: the rules failed too ({exc})")
                    intentions, source = [], "rules"
            else:
                del self.running[rid]
                try:
                    intentions, source = future.result()
                except Exception as exc:   # a failed call: the engineer goes on with what it was doing
                    self.log(f"{rid}: strategist failed ({exc})")
                    intentions, source = [], "llm"
            if self.library:
                self.library.record(request, intentions, source="experience" if "experience" in source else source)
            try:   # its own judgment on the nests round it, whatever the model said (companion/offense_brain.py)
                from companion import offense_brain
                intentions = offense_brain.with_reflex(request, intentions)
            except Exception as exc:   # (never in the way of a decision)
                self.log(f"{rid}: offense judgment failed ({exc})")
            try:   # a vehicle of its own when its long walks make one pay (companion/vehicle_brain.py)
                from companion import vehicle_brain
                intentions = vehicle_brain.with_reflex(request, intentions)
            except Exception as exc:
                self.log(f"{rid}: vehicle judgment failed ({exc})")
            self.ready[rid] = (request, intentions, source)
            self.habits.pop(rid, None)
        # Sent once the game has it: a decision whose sending fails (the line dropped) goes again next time.
        for rid, (request, intentions, source) in list(self.ready.items()):
            self.submit(request, intentions, source)
            del self.ready[rid]
            self.handled.add(rid)

    def forget(self, why: str = "the game was reconnected") -> None:
        """Nothing asked before may be answered now (a save loaded, or a reconnect after which a loaded save
        may already be past the old tick): thoughts under way finish on their own and are thrown away."""
        self.log(f"{why}: earlier requests are forgotten")
        self.running.clear()
        self.habits.clear()
        self.ready.clear()
        self.handled.clear()
        self._last_tick = None

    def think(self, request: Mapping[str, Any], habits: list) -> tuple[list[dict], str]:
        """On a worker thread: a decision that worked before, if System One picks one, else the strategist's.
        With OpenAI's credit used up, the rules decide instead (and nothing is asked of OpenAI)."""
        if decisions_api.out_of_credit():
            return self.by_rule(request), "rules"
        if biter_fight(request):
            with self._rules_lock:
                return self.rule_brain().decide(request), "rules"
        habit = self.habit(request, habits) if habits else None
        if habit is not None:
            return habit, "experience+system-one"
        try:
            return self.strategist.decide(request), getattr(self.strategist, "source", "llm")
        except decisions_api.OutOfCredit:
            return self.by_rule(request), "rules"

    def rule_brain(self) -> "RuleStrategist":
        """The rules (the strategist itself when it is the rules); call with _rules_lock held."""
        if self._rules is None:
            self._rules = self.strategist if isinstance(self.strategist, RuleStrategist) else RuleStrategist()
        return self._rules

    def by_rule(self, request: Mapping[str, Any]) -> list[dict]:
        with self._rules_lock:
            self.rule_brain()
            until = decisions_api.paused_until()
            if until != self._announced:
                self._announced = until
                when = ("until the runner is started again" if until == math.inf
                        else "until " + time.strftime("%H:%M", time.localtime(until)))
                self.log(f"{decisions_api.pause_reason()}: the engineers go on by rule, without the strategists, "
                         f"System One or the designer, {when}")
            return self._rules.decide(request)

    def habit(self, request: Mapping[str, Any], habits: list) -> list[dict] | None:
        """System One weighs the decisions that worked in situations like this against thinking it over."""
        choices = [{"id": f"repeat-{n}", "description": "Repeat: " + "; ".join(
                        f"{i.get('tool')} {json.dumps(i.get('args'), separators=(',', ':'))[:100]}"
                        for i in record.intentions)
                    + f" (carried out {record.done} of {record.done + record.failed} times)"}
                   for n, record in enumerate(habits)]
        choices.append({"id": "think", "description": "Think it over and decide afresh for this situation."})
        situation = request.get("situation") or {}
        events = ", ".join(json.dumps(e, separators=(",", ":"))[:120] for e in request.get("events") or [])
        site = {"npc_name": situation.get("name"),
                "personality": {"traits": situation.get("traits") or [], "axes": situation.get("axes") or {}},
                "summary": f"it woke up ({request.get('reason')}); {events or 'nothing new happened'}."}
        try:
            picked, _ = self.chooser.choose(site, choices, question="habit")
        except Exception as exc:
            self.log(f"{request.get('request_id')}: habit choice failed ({exc}); thinking it over")
            return None
        picked = str(picked)
        if not picked.startswith("repeat-") or not picked[7:].isdigit() or int(picked[7:]) >= len(habits):
            return None
        return self.library.fill(habits[int(picked[7:])], request)

    def submit(self, request: Mapping[str, Any], intentions: list[dict], source: str) -> None:
        rid = request["request_id"]
        result = self.call("/npc-agent submit " + json.dumps({"request_id": rid, "intentions": intentions},
                                                              separators=(",", ":")))
        name = (request.get("situation") or {}).get("name", rid)
        summary = ", ".join(f"{i.get('tool')}({i.get('priority', '')})" for i in intentions) or "nothing"
        said = [str((i.get("args") or {}).get("text"))[:160] for i in intentions if i.get("tool") == "say"]
        if said:
            summary += " \"" + " / ".join(said) + "\""
        kinds = sorted({str(e.get("kind")) for e in request.get("events") or [] if isinstance(e, Mapping)})
        why = f"{request.get('reason')}: {', '.join(kinds)}" if kinds else request.get("reason")
        self.log(f"{name} [{why}] -> {summary} ({source}; accepted {result.get('accepted')})")
