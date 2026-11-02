"""Targets to mine are found ring by ring (8, 24, 64, 128 tiles out, by the names that yield the item) and the
search stops once nothing farther can do better: the very target one query over the whole radius would give
(Worker.discover; the Workers Union mod's ring search, OP-6, 2026-10-29)."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_obstacles  # noqa: E402

WORLD = r"""
    character.prototype = {mining_speed=0.5}
    character.surface.find_entities_filtered = function(f)
        queries[#queries + 1] = f.radius
        local names = {}
        for _, n in ipairs(f.name or {}) do names[n] = true end
        local out = {}
        for _, e in ipairs(world) do
            local dx, dy = e.position.x - f.position.x, e.position.y - f.position.y
            if names[e.name] and dx * dx + dy * dy <= f.radius * f.radius then out[#out + 1] = e end
        end
        scanned = scanned + #out
        return out
    end
    local kinds = {
        ['stone-ore']={type='resource', mining_time=1, products={{type='item', name='stone', amount=1}}},
        ['big-rock']={type='simple-entity', mining_time=2, products={{type='item', name='stone', amount=24},
                                                                    {type='item', name='coal', amount=12}}},
        ['huge-rock']={type='simple-entity', mining_time=2,
                       products={{type='item', name='stone', amount_min=24, amount_max=50}}},
        ['tree-01']={type='tree', mining_time=0.55, products={{type='item', name='wood', amount=4}}},
        ['coal-ore']={type='resource', mining_time=1, products={{type='item', name='coal', amount=1}}},
        ['dead-bush']={type='simple-entity', mining_time=0.4, products={}},
    }
    for name, k in pairs(kinds) do
        prototypes.entity[name] = {name=name, type=k.type,
            mineable_properties={minable=true, mining_time=k.mining_time, products=k.products}}
    end
    prototypes.get_entity_filtered = function(filters)
        local want, out = {}, {}
        for _, t in ipairs(filters[1].type) do want[t] = true end
        for name, p in pairs(prototypes.entity) do if want[p.type] then out[name] = p end end
        return out
    end
    world = {}
    function spawn(name, x, y, amount)
        local p = prototypes.entity[name]
        world[#world + 1] = {valid=true, name=name, type=p.type, prototype=p, minable=true, amount=amount,
                             force={name='neutral'}, position={x=x, y=y}, unit_number=#world + 1}
    end
    function pick(item, count, natural)
        queries, scanned = {}, 0
        local job = {item=item, count=count, natural_only=natural}
        local e = W.discover(npc, job)
        return e and e.unit_number or 0
    end
"""


def world(seed):
    lua = test_obstacles.worker_with(WORLD)
    rng = random.Random(seed)
    names = ["stone-ore", "big-rock", "huge-rock", "tree-01", "coal-ore", "dead-bush"]
    for _ in range(rng.randint(0, 400)):
        name = rng.choice(names)
        r = rng.uniform(0, 140) ** rng.choice((1, 1, 0.8))
        import math
        a = rng.uniform(0, 2 * math.pi)
        lua.execute(f"spawn('{name}', {r * math.cos(a):.3f}, {r * math.sin(a):.3f}, {rng.randint(1, 2000)})")
    return lua


class RingTests(unittest.TestCase):
    def test_the_rings_pick_what_one_whole_query_picks(self):
        cases = 0
        for seed in range(60):
            lua = world(seed)
            for item, count, natural in (("stone", 1, False), ("stone", 50, False), ("coal", 10, False),
                                         ("wood", 20, True), ("stone", 200, True), ("coal", 3, True)):
                nat = "true" if natural else "false"
                rings = lua.eval(f"pick('{item}', {count}, {nat})")
                lua.execute("W.RINGS = {128}")
                whole = lua.eval(f"pick('{item}', {count}, {nat})")
                lua.execute("W.RINGS = {8, 24, 64, 128}")
                self.assertEqual(rings, whole, (seed, item, count, natural))
                cases += 1
        self.assertGreater(cases, 300)

    def test_a_target_close_by_ends_the_search_in_the_first_rings(self):
        lua = world(1)
        lua.execute("world = {}; spawn('tree-01', 3, 0)")
        for i in range(300):   # a forest farther out
            lua.execute(f"spawn('tree-01', {30 + i % 40}, {i // 40})")
        self.assertEqual(lua.eval("pick('wood', 20, true)"), 1)
        self.assertLessEqual(lua.eval("#queries"), 2)   # (8 and 24 tiles: nothing farther can beat it)
        self.assertLess(lua.eval("scanned"), 5)
        # a stone ore tile close by is no such end: a rock far out may give more stone a second, walk and all
        lua.execute("world = {}; spawn('stone-ore', 3, 0, 1000); spawn('huge-rock', 60, 0)")
        self.assertEqual(lua.eval("pick('stone', 50, false)"), 2)

    def test_only_what_yields_the_item_is_looked_at(self):
        lua = world(2)
        lua.execute("world = {}; for i = 1, 50 do spawn('coal-ore', i, 0, 100) end; spawn('big-rock', 90, 0)")
        lua.eval("pick('stone', 10, false)")
        self.assertLessEqual(lua.eval("scanned"), 1)   # (the coal tiles are never fetched for stone)


if __name__ == "__main__":
    unittest.main()
