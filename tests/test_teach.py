"""Teaching blueprints (teach.lua) in Factorio's Lua 5.2, without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime


ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().module_sources = lua.table_from({
        "scripts." + name: (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
        for name in ("chronicle", "teach", "teach_gui")
    })
    # where engineers stand (diplomacy) and how they feel (relations), set per test
    lua.globals().module_sources["scripts.diplomacy"] = \
        "return {state=function(a, b) return stances[a.name .. '|' .. b.name] or 'peace' end}"
    lua.globals().module_sources["scripts.relations"] = \
        "return {get=function() return {trust=12.7, grudge=40.2} end, stance=function(id) return feelings[id] or 0 end}"
    lua.execute("""
        stances, feelings = {}, {}
        storage = {npcs={{id=1, name='Ada'}, {id=2, name='Bram'}}}
        defines = {inventory={item_main=1}}
        prototypes = {entity={
            ['assembling-machine-1']={type='assembling-machine', tile_width=3, tile_height=3,
                items_to_place_this={{name='assembling-machine-1', count=1}}},
            ['inserter']={type='inserter', tile_width=1, tile_height=1, items_to_place_this={{name='inserter', count=1}}}}}
        function log() end
        game = {tick=600}
        local loaded = {}
        function require(name)
            if not loaded[name] then loaded[name] = assert(load(module_sources[name], '@'..name, 't'))() end
            return loaded[name]
        end
        function blueprint(label, entities)
            return {valid_for_read=true, is_blueprint=true, is_blueprint_book=false, label=label,
                is_blueprint_setup=function() return true end,
                get_blueprint_entities=function() return entities end}
        end
        function book(stacks)
            local inventory = setmetatable({}, {__index=stacks, __len=function() return #stacks end})
            return {valid_for_read=true, is_blueprint=false, is_blueprint_book=true,
                get_inventory=function() return inventory end}
        end
        GEARS = {{name='assembling-machine-1', position={x=1.5, y=1.5}, recipe='iron-gear-wheel'},
                 {name='inserter', position={x=0.5, y=3.5}, direction=8}}
        -- Just enough LuaGuiElement: add/destroy/clear, children, lookup by name.
        function element(spec, parent)
            local e = {spec=spec, name=spec.name, children={}, style={}, valid=true, parent=parent}
            e.add = function(child)
                local c = element(child, e); e.children[#e.children+1] = c; return c
            end
            e.destroy = function()
                e.valid = false
                for i, c in ipairs(parent.children) do if c == e then table.remove(parent.children, i) end end
            end
            e.clear = function() e.children = {} end
            e.force_auto_center = function() end
            return setmetatable(e, {__index=function(_, key)
                for _, c in ipairs(rawget(e, 'children')) do if c.name == key then return c end end
            end})
        end
        flying, toggled = {}, nil
        player = {name='Andre', index=1, gui={screen=element({})},
            set_shortcut_toggled=function(name, on) toggled = on end,
            create_local_flying_text=function(spec) flying[#flying+1] = spec.text end}
        game.get_player = function() return player end
        C = require('scripts.chronicle')
        T = require('scripts.teach')
        G = require('scripts.teach_gui')
    """)
    return lua


def helped_targets(lua):
    return sorted(e.target for e in lua.eval("storage.chronicle.ring").values() if e.kind == "helped")


class TeachTests(unittest.TestCase):
    def test_one_blueprint_teaches_every_engineer(self):
        lua = runtime()
        lua.execute("player.cursor_stack = blueprint('gears', GEARS)")
        self.assertEqual(lua.eval("T.from_cursor(player, nil)"), 'The engineers will study "gears".')
        waiting = list(lua.eval("T.waiting(4)").values())
        self.assertEqual(len(waiting), 1)
        entry = waiting[0]
        self.assertEqual(entry.label, "gears")
        self.assertIsNone(entry.npc_id)
        machine = entry.entities[1]
        self.assertEqual((machine.name, machine.recipe, machine.w, machine.kind, machine.item),
                         ("assembling-machine-1", "iron-gear-wheel", 3, "assembling-machine", "assembling-machine-1"))
        self.assertEqual(entry.entities[2].direction, 8)
        self.assertEqual(helped_targets(lua), ["npc:1", "npc:2"])

    def test_a_book_teaches_every_blueprint_in_it(self):
        lua = runtime()
        lua.execute("""player.cursor_stack = book({blueprint('gears', GEARS), blueprint('', {}),
            book({blueprint('more gears', GEARS)}), {valid_for_read=false}})""")
        self.assertEqual(lua.eval("T.from_cursor(player, 'bram')"), 'Bram: Thanks, I\'ll study 2 blueprints.')
        recent = list(lua.eval("T.recent(6)").values())
        self.assertEqual([e.label for e in recent], ["more gears", "gears"])
        self.assertEqual({e.npc_id for e in recent}, {2})
        self.assertEqual(helped_targets(lua), ["npc:2"])

    def test_nothing_held_or_nothing_useful(self):
        lua = runtime()
        lua.execute("player.cursor_stack = {valid_for_read=false}")
        self.assertIn("Hold a blueprint", lua.eval("T.from_cursor(player, nil)"))
        lua.execute("player.cursor_stack = blueprint('empty', {})")
        self.assertEqual(lua.eval("T.from_cursor(player, nil)"), "Nothing to learn there: it has no buildings.")
        self.assertEqual(len(lua.eval("T.recent(6)")), 0)

    def test_an_area_shown_from_the_base_is_learned_relative_to_it(self):
        lua = runtime()
        reply = lua.eval("""(function()
            local function made(name, x, y, recipe)
                return {valid=true, name=name, type=prototypes.entity[name].type, direction=0,
                        position={x=x, y=y}, get_recipe=function() return recipe and {name=recipe} end}
            end
            local player = {index=1, name='Andre'}
            local r, what = T.from_area(player, {made('assembling-machine-1', 101.5, 51.5, 'iron-gear-wheel'),
                made('inserter', 100.5, 53.5), made('inserter', 102.5, 53.5)},
                {left_top={x=100, y=50}, right_bottom={x=104, y=54}})
            return r
        end)()""")
        self.assertIn("2 inserter", reply)
        entry = lua.eval("T.recent(1)[1]")
        self.assertTrue(entry.label.startswith("shown by Andre"))
        first = entry.entities[1]
        self.assertEqual((first.name, first.x, first.y, first.recipe), ("assembling-machine-1", 1.5, 1.5, "iron-gear-wheel"))
        self.assertEqual(lua.eval("T.from_area({index=1, name='Andre'}, {}, {left_top={x=0,y=0}})")[0],
                         "Drag over some of your buildings to show the engineers.")

    def test_learned_entries_drop_their_layout(self):
        lua = runtime()
        lua.execute("player.cursor_stack = blueprint('gears', GEARS)")
        lua.eval("T.from_cursor(player, nil)")
        self.assertTrue(lua.eval("T.learned(1, 'module:iron-gear-wheel')"))
        self.assertEqual(len(lua.eval("T.waiting(4)")), 0)
        entry = lua.eval("T.recent(1)")[1]
        self.assertEqual(entry.purpose, "module:iron-gear-wheel")
        self.assertIsNone(entry.entities)


class TeachWindowTests(unittest.TestCase):
    def test_shortcut_opens_window_and_slot_click_teaches(self):
        lua = runtime()
        lua.execute("G.on_shortcut({prototype_name='npc-teach-blueprint', player_index=1})")
        self.assertTrue(lua.eval("toggled"))
        frame = lua.eval("player.gui.screen.npc_teach_frame")
        self.assertIsNotNone(frame)
        self.assertEqual(lua.eval("player.gui.screen.npc_teach_frame.children[4].npc_teach_list.children[1].spec.caption"),
                         "Nothing taught yet.")
        lua.execute("player.cursor_stack = blueprint('gears', GEARS)")
        handled = lua.eval("G.on_click({player_index=1, element=player.gui.screen.npc_teach_frame.children[4]"
                           ".children[1].npc_teach_slot})")
        self.assertTrue(handled)
        self.assertEqual(lua.eval("flying[1]"), 'The engineers will study "gears".')
        caption = lua.eval("player.gui.screen.npc_teach_frame.children[4].npc_teach_list.children[1].spec.caption")
        self.assertIn("gears", caption)
        self.assertIn("waiting to be studied", caption)
        self.assertFalse(lua.eval("G.on_click({player_index=1, element={valid=true, name='something_else'}})"))
        lua.execute("G.on_shortcut({prototype_name='npc-teach-blueprint', player_index=1})")
        self.assertFalse(lua.eval("toggled"))
        self.assertIsNone(lua.eval("player.gui.screen.npc_teach_frame"))

    def test_the_window_shows_where_every_engineer_stands_with_you_and_each_other(self):
        lua = runtime()
        lua.execute("""
            local function body(r, g, b) return {valid=true, color={r=r, g=g, b=b}} end
            storage.npcs = {{id=1, name='Ada', force={name='npc-faction-1'}, character=body(1, 0, 0)},
                            {id=2, name='Bram', force={name='npc-faction-2'}, character=body(0, 0, 1),
                             joined={player_index=1}},
                            {id=3, name='Cora', force={name='npc-faction-3'}, character=body(0, 1, 0)}}
            player.force = {name='player'}
            stances['npc-faction-1|player'] = 'war'
            stances['npc-faction-2|player'] = 'allied'
            stances['npc-faction-1|npc-faction-3'] = 'war'
            stances['npc-faction-3|npc-faction-1'] = 'war'
            stances['npc-faction-2|npc-faction-3'] = 'allied'
            feelings[1], feelings[2] = -0.6, 0.5
            G.open(player)
            grid = player.gui.screen.npc_teach_frame.children[2].npc_relations.children[1]
        """)
        cells = [c.spec.caption for c in lua.eval("grid.children").values()]
        self.assertEqual(cells[4:8], ["Ada (#1)", "war", "hostile", "[color=1,0.35,0.3]at war with Cora[/color]"])
        self.assertEqual(cells[8:11], ["Bram (#2)", "allied, on your team", "friendly"])
        self.assertIn("allied with Cora", cells[11])
        self.assertEqual(cells[12:16], ["Cora (#3)", "peace", "neutral", "[color=1,0.35,0.3]at war with Ada[/color]"])
        self.assertEqual(lua.eval("grid.children[6].style.font_color[1]"), 1)          # war in red
        self.assertEqual(lua.eval("grid.children[7].spec.tooltip"), "trust 12, grudge 40")
        # it keeps up while open
        lua.execute("stances['npc-faction-1|player'] = 'peace'; game.connected_players = {player}; G.tick(341)")
        grid = "player.gui.screen.npc_teach_frame.children[2].npc_relations.children[1]"
        self.assertEqual(lua.eval(grid + ".children[6].spec.caption"), "peace")

    def test_other_shortcuts_are_ignored(self):
        lua = runtime()
        lua.execute("G.on_shortcut({prototype_name='give-blueprint', player_index=1})")
        self.assertIsNone(lua.eval("player.gui.screen.npc_teach_frame"))


if __name__ == "__main__":
    unittest.main()
