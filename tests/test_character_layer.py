"""Character layer (voice, event glue, initiative, player barter) in Factorio's Lua 5.2, without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime


ROOT = Path(__file__).resolve().parents[1]
MODULES = ("personality", "chronicle", "relations", "voice", "player_trade", "character", "strategy_policy", "scouting",
           "team_world", "radio", "hands", "robots", "logistics", "mend", "ghosts")


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().module_sources = lua.table_from({
        "scripts." + name: (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
        for name in MODULES
    })
    lua.execute("""
        storage = {}
        printed = {}
        chat_level = 'normal'
        settings = {global={['npc-factions-chat']={}}}
        setmetatable(settings.global['npc-factions-chat'], {__index=function() return chat_level end})
        prototypes = {item={['iron-plate']={}, ['iron-gear-wheel']={}, coal={}},
            entity={['iron-ore']={mineable_properties={products={{type='item', name='iron-ore'}}}},
                    ['crude-oil']={mineable_properties={products={{type='fluid', name='crude-oil'}}}}}}
        function log() end
        surface = {index=1, name='nauvis'}
        players = {}
        game = {tick=0,
            print=function(text) printed[#printed+1]={to='all', text=text} end,
            get_player=function(index) return players[index] end,
            get_surface=function(index) return surface end}
        local loaded = {}
        function require(name)
            if not loaded[name] then
                loaded[name] = assert(load(module_sources[name], '@'..name, 't'))()
            end
            return loaded[name]
        end
        -- One stack per item; indexed access and # work like a LuaInventory.
        function inventory(items)
            local inv = {valid=true, items=items}
            local function names()
                local out = {}
                for name in pairs(inv.items) do out[#out+1] = name end
                table.sort(out)
                return out
            end
            inv.get_item_count = function(q) return inv.items[type(q)=='table' and q.name or q] or 0 end
            inv.get_insertable_count = function() return 1000 end
            return setmetatable(inv, {
                __len=function() return #names() end,
                __index=function(_, i)
                    local name = type(i)=='number' and names()[i]
                    if name then return {valid_for_read=true, name=name, count=inv.items[name], health=1,
                        quality={name='normal'}} end
                end})
        end
        function force(name)
            local f = {name=name, valid=true, players={}}
            f.get_cease_fire = function() return true end
            return f
        end
        P = require('scripts.personality')
        C = require('scripts.chronicle')
        R = require('scripts.relations')
        V = require('scripts.voice')
        T = require('scripts.player_trade')
        CH = require('scripts.character')
        S = require('scripts.strategy_policy')
        SC = require('scripts.scouting')
        storage.npcs = {}
        for id=1,3 do
            local npc = {id=id, name='Engineer'..id, force=force('npc-faction-'..id)}
            npc.inv = inventory({})
            npc.character = {valid=true, type='character', surface=surface, position={x=id*10, y=0},
                force=npc.force, get_main_inventory=function() return npc.inv end}
            assert(P.assign(npc, {}, 42))
            for key in pairs(npc.personality.axes) do npc.personality.axes[key]=0.5 end
            storage.npcs[id] = npc
        end
        storage.founding_force = force('player')
        function add_player(index, x)
            local p = {index=index, name='Player'..index, valid=true, force=storage.founding_force}
            p.inv = inventory({})
            p.character = {valid=true, type='character', surface=surface, position={x=x or 0, y=0},
                player=p, force=storage.founding_force, get_main_inventory=function() return p.inv end}
            p.print = function(text) printed[#printed+1]={to=index, text=text} end
            players[index] = p
            storage.founding_force.connected_players = {p}
            storage.founding_force.players = {p}
            return p
        end
        function event(kind, actor, target, payload)
            local e, reason = C.record{kind=kind, actor=actor, target=target, tick=game.tick,
                source='native', payload=payload}
            assert(e, reason)
            return e
        end
        function last() return printed[#printed] and printed[#printed].text end
    """)
    return lua


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()

    def test_rate_limits_by_level_and_priority(self):
        self.lua.execute("""
            local npc = storage.npcs[1]
            game.tick = 100
            assert(V.say(npc, 'greet', {target='Ann'}, {priority=2, target='a'}))
            game.tick = 200
            local text, why = V.say(npc, 'greet', {target='Bob'}, {priority=2, target='b'})
            assert(text == nil and why == 'npc_gap', why)
            -- Major events always get through.
            assert(V.say(npc, 'war_declared', {target='Bob'}, {priority=3, target='b'}))
            -- ...but are never repeated within five minutes.
            game.tick = 300
            text, why = V.say(npc, 'war_declared', {target='Bob'}, {priority=3, target='b'})
            assert(why == 'repeat', why)
            chat_level = 'quiet'
            game.tick = 100000
            text, why = V.say(npc, 'greet', {target='Cy'}, {priority=2, target='c'})
            assert(why == 'below_chat_level', why)
            -- A reply the player asked for is always shown, to that player only.
            local p = add_player(1)
            assert(V.say(npc, 'greet', {target='Player1'}, {player=p, direct=true}))
            assert(printed[#printed].to == 1)
        """)

    def test_tone_follows_relationship_and_personality(self):
        self.lua.execute("""
            local npc = storage.npcs[1]
            assert(V.tone(npc, 'enemy') == 'hostile')
            assert(V.tone(npc, 'neutral') == 'warm')
            npc.personality.axes.humor = 0.9
            assert(V.tone(npc, 'friend') == 'playful')
            npc.personality.axes.humor = 0.5
            npc.personality.axes.sociability = 0.1
            assert(V.tone(npc, 'neutral') == 'curt')
            local line = V.render('trade_offer', 'curt', {deal=7, npc_count=20, npc_item='iron plate',
                player_count=10, player_item='coal', name='Engineer1'}, 1)
            assert(line:find('#7') and line:find('20 iron plate') and line:find('accept 7'), line)
        """)


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()

    def test_society_rejection_hurts_the_proposer_view_and_is_voiced(self):
        self.lua.execute("""
            game.tick = 5000
            CH.on_society(storage.npcs[2], 'rejection', {offer_id=3, proposer=1, offer_kind='alliance'})
            local e = C.recent(1)[1]
            assert(e.kind == 'offer_rejected' and e.actor == 'npc:2' and e.target == 'npc:1', e.kind)
            assert(R.get(1, 'npc:2', game.tick).affinity < 0)
            assert(last():find('Engineer2'), last())
        """)

    def test_theft_creates_grudge_and_the_victim_speaks(self):
        self.lua.execute("""
            game.tick = 7000
            CH.on_society(storage.npcs[1], 'hostile_action',
                {intent={kind='theft', actor_id=1, target_id=2, item='iron-plate', count=20}})
            local r = R.get(2, 'npc:1', game.tick)
            assert(r.trust < 0 and r.grudge > 0)
            -- Engineer 3 stood nearby and saw it.
            assert(C.recent(1)[1].witnesses[1] == 'npc:3')
            assert(last():find('^%[Engineer2%]') and last():find('Engineer1'), last())
        """)

    def test_damage_is_reported_in_bursts(self):
        self.lua.execute("""
            local p = add_player(1)
            local victim = storage.npcs[2]
            for i=1,50 do
                game.tick = 60 + i
                CH.on_entity_damaged{entity=victim.character, cause=p.character, final_damage_amount=10}
            end
            game.tick = 240
            CH.tick(240)
            local attacks = C.recent(10, {kinds={'attacked'}})
            assert(#attacks == 1, #attacks)
            assert(attacks[1].actor == 'player:1' and attacks[1].target == 'npc:2')
            assert(attacks[1].payload.severity > 3 - 1e-9)
            -- More damage right away waits for the repeat window.
            CH.on_entity_damaged{entity=victim.character, cause=p.character, final_damage_amount=10}
            game.tick = 600; CH.tick(600)
            assert(#C.recent(10, {kinds={'attacked'}}) == 1)
            game.tick = 2100; CH.tick(2100)
            assert(#C.recent(10, {kinds={'attacked'}}) == 2)
            assert(R.get(2, 'player:1', game.tick).grudge > 0)
        """)

    def test_trade_terms_offer_surplus_for_what_is_lacking(self):
        self.lua.execute("""
            local npc = storage.npcs[1]
            npc.inv.items = {['iron-plate']=200, coal=5, ['copper-plate']=50}
            local terms = CH.trade_terms(npc, 0)
            assert(terms.npc_gives == 'iron-plate' and terms.player_gives == 'coal', terms.player_gives)
            assert(terms.npc_count == 20 and terms.player_count >= 40, terms.player_count)
            npc.inv.items = {['iron-plate']=20}
            assert(CH.trade_terms(npc, 0) == nil)
        """)

    def test_initiative_threatens_when_holding_a_grudge(self):
        self.lua.execute("""
            local p = add_player(1, 15)
            local npc = storage.npcs[1]
            npc.personality.axes.aggression = 0.8
            for i=1,3 do game.tick = 100 + i; event('attacked', 'player:1', 'npc:1', {severity=3}) end
            game.tick = 4000
            assert(CH.initiative(npc, game.tick) == 'threat')
            assert(last():find('remember') or last():find('forgotten'), last())
        """)

    def test_initiative_asks_for_the_missing_item(self):
        self.lua.execute("""
            add_player(1, 15)
            local npc = storage.npcs[1]
            npc.phase = 'designed-start:need:waiting_for:iron-gear-wheel'
            game.tick = 4000
            assert(CH.initiative(npc, game.tick) == 'need_help')
            assert(last():find('iron gear wheel'), last())
        """)

    def test_an_engineer_with_a_strategist_does_not_ask_every_minute(self):
        self.lua.execute("""
            add_player(1, 15)
            local npc = storage.npcs[1]
            npc.phase = 'designed-start:need:waiting_for:iron-gear-wheel'
            game.tick = 4000
            CH.thinks = function() return true end
            assert(CH.initiative(npc, game.tick) == nil)
            CH.thinks = nil
        """)

    def test_initiative_offers_a_trade_the_player_can_accept(self):
        self.lua.execute("""
            local p = add_player(1, 500)
            local npc = storage.npcs[1]
            npc.personality.axes.trade = 0.9
            npc.voice_rng = 1
            npc.inv.items = {['iron-plate']=200, coal=5}
            game.tick = 4000
            assert(CH.initiative(npc, game.tick) == 'trade_offer')
            local deal = T.observe(p)[1]
            assert(deal.status == 'pending_player' and deal.npc_gives == 'iron-plate')
            assert(printed[#printed].to == 1 and last():find('accept '..deal.id), last())
            -- One open offer at a time.
            game.tick = 9000
            assert(CH.initiative(npc, game.tick) ~= 'trade_offer')
        """)

    def test_player_barter_answer_cannot_be_rerolled(self):
        self.lua.execute("""
            local p = add_player(1, 5)
            local npc = storage.npcs[1]
            npc.inv.items = {['iron-plate']=100, coal=100}
            local answers = {}
            for i=1,8 do
                game.tick = 1000 + i
                local deal = T.propose(p, 1, {player_gives='coal', player_count=10,
                    npc_gives='iron-plate', npc_count=10})
                answers[deal.status] = true
            end
            local n = 0
            for _ in pairs(answers) do n = n + 1 end
            assert(n == 1, 'asking again changed the answer')
        """)

    def test_committed_trade_builds_trust(self):
        self.lua.execute("""
            local p = add_player(1)
            game.tick = 100
            CH.on_trade_resolved{id=1, npc_id=1, player_index=1, status='committed',
                npc_gives='coal', player_gives='iron-plate', npc_count=10}
            assert(R.get(1, 'player:1', game.tick).trust > 0)
            assert(last():find('Player1'), last())
        """)

    def test_social_summary_lists_strongest_relationships_with_memories(self):
        self.lua.execute("""
            add_player(1)
            game.tick = 100
            event('theft', 'npc:2', 'npc:1', {item='iron-plate'})
            event('attacked', 'npc:2', 'npc:1', {severity=2})
            event('gift', 'player:1', 'npc:1', {value=5})
            game.tick = 200
            local s = CH.social_summary(storage.npcs[1], game.tick)
            assert(s.relations[1].name == 'Engineer2', s.relations[1].name)
            assert(s.relations[1].label == 'rival' or s.relations[1].label == 'enemy', s.relations[1].label)
            local memories = table.concat(s.relations[1].memories, '|')
            assert(memories:find('Engineer2 attacked you') and memories:find('Engineer2 stole from you'), memories)
            assert(s.relations[2].name == 'Player1')
            assert(s.recent_attacks == 2 and s.attackers[1] == 'Engineer2')
            assert(s.mood.anger > 0)
        """)

    def test_grudges_and_friendship_shift_social_choices(self):
        self.lua.execute("""
            local hostile = {affinity=-20, grudge=70, fear=0, debt=0, anger=50}
            local friendly = {affinity=60, grudge=0, fear=0, debt=40, anger=0}
            for _, action in ipairs({'declare_war', 'theft', 'sabotage', 'reject'}) do
                assert(S.social_prior(action, hostile) > S.social_prior(action, friendly), action)
            end
            for _, action in ipairs({'accept', 'supply', 'offer_alliance', 'offer_trade'}) do
                assert(S.social_prior(action, friendly) > S.social_prior(action, hostile), action)
            end
            assert(S.social_prior('idle', hostile) == 0)
        """)

class ScoutingTests(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()
        self.lua.execute("""
            ore = {}
            for x=62,69 do for y=-4,3 do ore[#ore+1] = {name='iron-ore', position={x=x, y=y}, amount=100} end end
            function surface.find_entities_filtered(q)
                local out = {}
                for _, e in ipairs(ore) do
                    local dx, dy = e.position.x - q.position.x, e.position.y - q.position.y
                    if dx*dx + dy*dy <= q.radius*q.radius then out[#out+1] = e end
                end
                return out
            end
            function surface.count_entities_filtered(q) return 0 end
            function surface.find_non_colliding_position(name, p) return {x=p.x, y=p.y} end
            npc = storage.npcs[1]
            npc.home = {x=0, y=0}
            npc.character.position = {x=0, y=0}
        """)

    def test_engineer_only_knows_what_it_has_seen(self):
        self.lua.execute("""
            SC.observe(npc, 0, true)
            assert(#SC.patches(npc, {{-32,-32},{32,32}}) == 0)
            npc.character.position = {x=40, y=0}
            SC.observe(npc, 600, true)
            local seen = SC.patches(npc, {{-32,-32},{32,32}})
            assert(#seen > 0 and seen[1].resource == 'iron-ore' and seen[1].dx > 50, #seen)
        """)

    def test_the_nearest_patches_of_every_resource_are_kept(self):
        self.lua.execute("""
            npc.known_patches = {}
            for k = 1, 20 do   -- many near iron cells, one coal cell further out
                npc.known_patches['iron-ore:' .. k] = {resource='iron-ore', x=40 + k, y=0, tiles=20, amount=9000,
                                                       tick=0, surface_index=1}
            end
            npc.known_patches['coal:1'] = {resource='coal', x=200, y=0, tiles=20, amount=9000, tick=0, surface_index=1}
            seen = SC.patches(npc, {{-32,-32},{32,32}})
            coal, iron = 0, 0
            for _, p in ipairs(seen) do
                if p.resource == 'coal' then coal = coal + 1 else iron = iron + 1 end
            end
            assert(coal == 1, 'coal crowded out')
            assert(iron == 4, iron)
            assert(seen[1].amount == 9000)
        """)

    def test_trip_walks_rings_until_the_resource_is_found(self):
        self.lua.execute("""
            SC.start(npc, 'iron-ore', 'iron-smelting-expansion', 32, 0)
            local job, outcome = SC.next(npc, 0)
            assert(job.kind == 'move' and math.abs(job.position.x - 56) < 1, job and job.position.x)
            npc.character.position = job.position
            SC.arrived(npc, 600)
            job, outcome = SC.next(npc, 600)
            assert(job == nil and outcome == 'found', outcome)
            -- With nothing to find, the trip ends after its last point.
            ore = {}
            npc.known_patches = {}
            SC.start(npc, 'copper-ore', nil, 32, 0)
            local walks = 0
            repeat job, outcome = SC.next(npc, 0); if job then walks = walks + 1 end until not job
            assert(outcome == 'exhausted' and walks == 24, walks)
        """)
    def test_in_the_team_world_a_trip_goes_on_past_ore_on_someone_elses_ground(self):
        # (rehearsal 2026-10-22: Borin knew copper only past his plot's edge, waited for good, and never saw
        # the field 60 tiles from home)
        self.lua.execute("""
            local TW = require('scripts.team_world')
            TW.active = function() return true end
            TW.bounds = function() return {plot={-40, -40, 40, 200}, area={-500, -500, 500, 500}} end
            npc.character.position = {x=40, y=0}
            SC.observe(npc, 0, true)                      -- the iron at x 62..69: a teammate's plot
            assert(not SC.usable(npc, {x=65, y=0}))
            SC.start(npc, 'iron-ore', 'iron-smelting-expansion', 32, 0)
            local job, outcome = SC.next(npc, 0)
            assert(job and job.kind == 'move', outcome)   -- not found: it walks on
            for x = 0, 7 do for y = 100, 107 do ore[#ore + 1] = {name='iron-ore', position={x=x, y=y}, amount=100} end end
            npc.character.position = {x=0, y=90}
            SC.arrived(npc, 600)
            job, outcome = SC.next(npc, 600)
            assert(job == nil and outcome == 'found', outcome)
            assert(SC.usable(npc, {x=0, y=600}))          -- (out beyond the team area: an outpost)
            assert(not SC.usable(npc, {x=35, y=0}))       -- (inside, but at the very edge: no site fits)
        """)

    def test_a_single_oil_well_is_worth_remembering(self):
        self.lua.execute("""
            ore = {{name='crude-oil', position={x=70.5, y=0.5}, amount=300000}}
            npc.character.position = {x=40, y=0}
            SC.observe(npc, 0, true)
            local seen = SC.patches(npc, {{-32,-32},{32,32}})
            assert(#seen == 1 and seen[1].resource == 'crude-oil', #seen)
            SC.start(npc, 'crude-oil', 'oil-gathering', 32, 0)
            local job, outcome = SC.next(npc, 0)
            assert(job == nil and outcome == 'found', outcome)
        """)

if __name__ == "__main__":
    unittest.main()
