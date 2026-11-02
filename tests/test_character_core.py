"""Character-core contracts executed in Factorio's Lua 5.2 dialect, without Factorio."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime


ROOT = Path(__file__).resolve().parents[1]
MODULES = ("personality", "chronicle", "relations")


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().module_sources = lua.table_from({
        "scripts." + name: (ROOT / "mod" / "scripts" / (name + ".lua")).read_text(encoding="utf-8")
        for name in MODULES
    })
    lua.execute("""
        storage = {}
        game = {tick=0}
        local loaded = {}
        function require(name)
            if not loaded[name] then
                loaded[name] = assert(load(module_sources[name], '@'..name, 't'))()
            end
            return loaded[name]
        end
        P = require('scripts.personality')
        C = require('scripts.chronicle')
        R = require('scripts.relations')
        loaded_without_storage_writes = next(storage) == nil
        storage.npcs = {}
        for id=1,3 do
            local npc = {id=id, name='Engineer '..id, force={name='faction'..id}}
            assert(P.assign(npc, {}, 42))
            storage.npcs[id] = npc
        end
        function neutral()
            for id=1,3 do
                for key in pairs(storage.npcs[id].personality.axes) do
                    storage.npcs[id].personality.axes[key]=0.5
                end
            end
        end
        function event(kind, actor, target, tick, witnesses, payload)
            local e, reason = C.record{kind=kind, actor=actor, target=target,
                tick=tick or 0, source='native', witnesses=witnesses, payload=payload}
            assert(e, reason)
            return e
        end
        -- Deterministic, type-aware sorted-key walker, including every saved field.
        function serialize(value)
            local t=type(value)
            if t=='number' then return string.format('%.17g',value) end
            if t=='string' then return string.format('%q',value) end
            if t~='table' then return tostring(value) end
            assert(getmetatable(value)==nil)
            local keys={}
            for k in pairs(value) do keys[#keys+1]=k end
            table.sort(keys,function(a,b)
                if type(a)==type(b) then return a<b end
                return type(a)<type(b)
            end)
            local out={'{'}
            for _,k in ipairs(keys) do
                out[#out+1]='['..serialize(k)..']='..serialize(value[k])..';'
            end
            out[#out+1]='}'
            return table.concat(out)
        end
    """)
    return lua


class CharacterCoreTests(unittest.TestCase):
    def setUp(self):
        self.lua = runtime()

    def check(self, code):
        self.lua.execute(code)

    def test_module_load_is_storage_free_and_ensure_is_idempotent(self):
        self.check("""
            assert(loaded_without_storage_writes)
            R.ensure(); local before=serialize(storage); R.ensure()
            assert(before==serialize(storage))
            assert(storage.character_version==1 and storage.chronicle.cap==2048)
        """)

    def test_ring_cap_sequence_lookup_and_newest_first(self):
        self.check("""
            for i=1,2100 do event('utterance',nil,nil,i) end
            assert(storage.chronicle.next_seq==2101 and storage.chronicle.count==2048)
            local count=0; for _ in pairs(storage.chronicle.ring) do count=count+1 end
            assert(count==2048 and C.get(52)==nil and C.get(53).seq==53)
            assert(C.get(2100).seq==2100 and C.get(2101)==nil and C.get(1.5)==nil)
            local recent=C.recent(3)
            assert(#recent==3 and recent[1].seq==2100 and recent[3].seq==2098)
            assert(#C.recent(0)==0 and #C.recent(9999)==2048)
        """)

    def test_unknown_kind_rejected_without_storage_writes(self):
        self.check("""
            local before=serialize(storage)
            local result,reason=C.record{kind='invented',tick=0,source='native'}
            assert(result==nil and reason=='unknown_kind' and before==serialize(storage))
        """)

    def test_save_unsafe_and_invalid_events_rejected(self):
        self.check("""
            local cyclic={}; cyclic.self=cyclic
            local bad={function() end,cyclic,setmetatable({},{}),{f=function() end},math.huge}
            for _,payload in ipairs(bad) do
                assert(C.record{kind='gift',tick=0,source='native',payload=payload}==nil)
            end
            assert(C.record{kind='gift',tick=-1,source='native'}==nil)
            assert(C.record{kind='gift',tick=0,source='unknown'}==nil)
            assert(C.record{kind='gift',tick=0,source='native',actor=2}==nil)
            assert(C.record{kind='gift',tick=0,source='native',witnesses={[2]='npc:1'}}==nil)
            local large={}; for i=1,600 do large[i]=i end
            assert(C.record{kind='gift',tick=0,source='native',payload=large}==nil)
        """)

    def test_event_copies_listener_order_and_filters(self):
        self.check("""
            local calls={}
            C.subscribe(function(e) calls[#calls+1]='first'; e.payload.value=900 end)
            C.subscribe(function(e) calls[#calls+1]='second'; assert(e.payload.value==4) end)
            local input={kind='gift',actor='npc:1',target='player:1',tick=8,
                source='player',payload={value=4},refs={position={x=3}},witnesses={'npc:3'}}
            local stored=C.record(input)
            input.refs.position.x=99; stored.payload.value=99
            local read=C.get(1); read.refs.position.x=77
            assert(C.get(1).payload.value==4 and C.get(1).refs.position.x==3)
            assert(calls[1]=='first' and calls[2]=='second')
            assert(#C.recent(10,{involving='npc:3',kinds={'gift'}})==1)
            assert(#C.recent(10,{involving='npc:2'})==0)
            assert(#C.recent(10,{kinds={'attacked'}})==0)
        """)

    def test_event_copy_budget_reserves_sequence_field(self):
        self.check("""
            local payload={}; for i=1,507 do payload[i]=i end
            local e=C.record{kind='utterance',tick=0,source='native',payload=payload}
            assert(e and C.get(e.seq).payload[507]==507)
            payload[508]=508
            local before=serialize(storage)
            local e,reason=C.record{kind='utterance',tick=0,source='native',payload=payload}
            assert(not e and reason=='unsafe_data' and before==serialize(storage))
        """)

    def test_attack_changes_victim_only(self):
        self.check("""
            neutral(); event('attacked','npc:1','npc:2',100)
            local r=R.get(2,'npc:1',100)
            assert(r.trust==-10 and r.grudge==15 and r.fear==5 and r.affinity==-8)
            assert(storage.relations[1]==nil and storage.relations[3]==nil)
            assert(R.stance(2,'npc:1',100)<0)
        """)

    def test_severity_clamps_and_non_numeric_payload_is_safe(self):
        self.check("""
            neutral(); event('attacked','player:1','npc:2',0,nil,{severity=100})
            assert(R.get(2,'player:1',0).trust==-30)
            event('attacked','player:2','npc:2',0,nil,{severity=-2})
            assert(R.get(2,'player:2',0).trust==-2)
            event('attacked','player:3','npc:2',0,nil,{severity='severe'})
            assert(R.get(2,'player:3',0).trust==-10)
        """)

    def test_witness_reputation_is_quarter_and_deduplicated(self):
        self.check("""
            neutral(); event('attacked','npc:1','npc:2',0,{'npc:3','npc:3','npc:1','npc:2'})
            local r=R.get(3,'npc:1',0)
            assert(r.trust==-2.5 and r.grudge==3.75 and r.fear==1.25 and r.affinity==-2)
            assert(storage.relations[1]==nil and R.get(2,'npc:1',0).trust==-10)
            assert(storage.memory[3].counters['npc:1']==nil)
        """)

    def test_player_victim_has_npc_witness_reputation(self):
        self.check("""
            neutral(); event('theft','player:2','player:1',0,{'npc:3'})
            assert(R.get(3,'player:2',0).trust==-5)
            assert(storage.relations[1]==nil and storage.relations[2]==nil)
        """)

    def test_gift_witness_reputation_does_not_create_debt(self):
        self.check("""
            neutral(); event('gift','npc:1','npc:2',100,{'npc:3'},{value=12})
            local witness=R.get(3,'npc:1',100)
            assert(witness.debt==0 and witness.trust==1.5 and witness.affinity==1.25)
            assert(R.get(2,'npc:1',100).debt==12)
        """)

    def test_force_counterparts_and_missing_dead_npcs(self):
        self.check("""
            neutral(); event('gift','player:1','force:faction2',10,nil,{value=12})
            assert(R.get(2,'player:1',10).debt==12)
            event('attacked','force:faction1','npc:2',10)
            assert(R.get(2,'force:faction1',10).grudge==15)
            storage.npcs[3]=nil
            event('promise_broken','npc:1','npc:3',20,{'npc:3','npc:2'})
            assert(storage.relations[3]==nil)
            assert(R.apply(3,'npc:1',{trust=-2},20)==nil)
            assert(R.get(3,'npc:1',20).trust==0)
        """)

    def test_killed_only_witness_relationships(self):
        self.check("""
            neutral(); storage.npcs[2]=nil
            event('killed','npc:1','npc:2',10,{'npc:3'})
            assert(R.get(3,'npc:1',10).trust==-15 and R.get(3,'npc:1',10).grudge==10)
            assert(storage.relations[1]==nil and storage.relations[2]==nil)
        """)

    def test_personality_modulation_and_grudge_decay(self):
        self.check("""
            assert(P.assign(storage.npcs[2],{'vengeful'},42))
            assert(P.assign(storage.npcs[3],{'forgiving'},42))
            event('attacked','player:1','npc:2',0)
            event('attacked','player:1','npc:3',0)
            local v=R.get(2,'player:1',0); local f=R.get(3,'player:1',0)
            assert(v.grudge>f.grudge)
            local later=120*3600
            assert(R.get(2,'player:1',later).grudge/v.grudge > R.get(3,'player:1',later).grudge/f.grudge)
            local before=serialize(storage)
            local d=R.modulate({grudge=1,cooperation=1,courage=1},{trust=10,grudge=10,fear=10})
            assert(d.trust==14 and d.grudge==15 and d.fear==5)
            local loss=R.modulate({cooperation=1},{trust=-10})
            assert(math.abs(loss.trust+6)<1e-10 and before==serialize(storage))
        """)

    def test_lazy_decay_baselines_debt_and_apply_after_decay(self):
        self.check("""
            assert(P.assign(storage.npcs[2],{'trusting'},42))
            R.apply(2,'player:1',{trust=40,affinity=40,fear=40,grudge=40,debt=80},0)
            local before=serialize(storage)
            local r=R.get(2,'player:1',120*3600)
            assert(math.abs(r.trust-30)<1e-9 and r.affinity==20 and r.debt==80)
            assert(math.abs(r.fear-0.625)<1e-9 and before==serialize(storage))
            R.apply(2,'player:1',{trust=4},120*3600)
            assert(math.abs(R.get(2,'player:1',120*3600).trust-34)<1e-9)
            assert(P.assign(storage.npcs[3],{'suspicious'},42))
            assert(R.get(3,'npc:1',0).trust==-10)
        """)

    def test_clamps_scales_and_returned_relation_is_copy(self):
        self.check("""
            local r=R.apply(2,'player:1',{trust=-500,affinity=500,fear=500,grudge=500,debt=5000},0)
            assert(r.trust==-100 and r.affinity==100 and r.fear==100 and r.grudge==100 and r.debt==1000)
            r.trust=99; assert(R.get(2,'player:1',0).trust==-100)
            R.apply(2,'player:1',{scale={grudge=0.7},fear=-500,debt=-5000},0)
            assert(R.get(2,'player:1',0).grudge==70 and R.get(2,'player:1',0).fear==0)
            assert(R.get(2,'player:1',0).debt==-1000)
        """)

    def test_apply_rejects_invalid_deltas_without_storage_writes(self):
        self.check("""
            local function rejected(deltas)
                local before=serialize(storage)
                local result,reason=R.apply(2,'npc:1',deltas,100)
                assert(result==nil and reason=='invalid_deltas')
                assert(before==serialize(storage))
            end
            rejected(nil)
            for _,deltas in ipairs({false,true,42,'invalid',function() end}) do rejected(deltas) end
            R.apply(2,'npc:1',{trust=5},100)
            rejected(nil); rejected('invalid')
            assert(R.get(2,'npc:1',100).trust==5)
        """)

    def test_rules_closed_set_and_direct_effects(self):
        # Independent expected numbers validate each handler, using neutral modulation.
        cases = {
            "theft": (-20, 0, 0, 20, 0), "sabotage": (-20, 0, 0, 20, 0),
            "gift": (6, 5, 0, 0, 1), "helped": (6, 5, 0, 0, 1),
            "delivery_completed": (6, 5, 0, 0, 1), "trade_completed": (3, 1, 0, 0, 0),
            "trade_failed": (-8, 0, 0, 0, 0), "delivery_failed": (-8, 0, 0, 0, 0),
            "promise_kept": (5, 0, 0, 0, 0), "promise_broken": (-12, 0, 0, 10, 0),
            "treaty_accepted": (8, 0, 0, 0, 0), "treaty_broken": (-30, 0, 0, 30, 0),
            "war_declared": (-20, 0, 10, 0, 0), "peace_made": (5, 0, 0, 0, 0),
            "insult": (0, -8, 0, 3, 0), "compliment": (0, 4, 0, 0, 0),
            "threat": (-5, -5, 10, 0, 0), "offer_rejected": (0, -3, 0, 0, 0),
        }
        for kind, expected in cases.items():
            with self.subTest(kind=kind):
                lua = runtime()
                lua.execute(f"neutral(); event('{kind}','npc:1','npc:2',0)")
                rel = lua.eval("R.get(2,'npc:1',0)")
                for field, value in zip(("trust", "affinity", "fear", "grudge", "debt"), expected):
                    self.assertAlmostEqual(rel[field], value)
        self.check("local n=0; for _ in pairs(R.RULES) do n=n+1 end; assert(n==26)")

    def test_bidirectional_positive_rules_and_peace_scale(self):
        self.check("""
            neutral(); event('gift','npc:1','npc:2',0,nil,{value=9})
            assert(R.get(1,'npc:2',0).affinity==2 and R.get(1,'npc:2',0).trust==0)
            event('trade_completed','npc:1','npc:2',0)
            assert(R.get(1,'npc:2',0).trust==3 and R.get(2,'npc:1',0).trust==9)
            R.apply(1,'npc:2',{grudge=20},0); R.apply(2,'npc:1',{grudge=40},0)
            event('peace_made','npc:1','npc:2',0,{'npc:3'})
            assert(R.get(1,'npc:2',0).grudge==14 and R.get(2,'npc:1',0).grudge==28)
            assert(R.get(3,'npc:1',0).trust==1.25)
        """)

    def test_memory_keeps_salience_cap_and_newest_ties(self):
        self.check("""
            neutral(); event('attacked','player:1','npc:2',1)
            for i=2,100 do event('utterance','player:1','npc:2',i) end
            local m=storage.memory[2].episodes
            assert(#m==64 and m[1].seq==1 and m[2].seq==100 and m[64].seq==38)
            local events=R.memories(2,'player:1',3)
            assert(#events==3 and events[1].kind=='attacked' and events[2].seq==100)
            events[1].kind='gift'; assert(C.get(1).kind=='attacked')
            assert(#R.memories(2,'player:9',3)==0 and #R.memories(2,nil,0)==0)
        """)

    def test_expired_chronicle_memories_are_skipped(self):
        self.check("""
            event('attacked','player:1','npc:2',1)
            for i=2,2050 do event('utterance',nil,nil,i) end
            assert(#R.memories(2,'player:1',3)==0)
        """)

    def test_expired_salient_episodes_do_not_crowd_out_new_memories(self):
        self.check("""
            for i=1,64 do event('attacked','player:1','npc:2',i) end
            for i=65,2200 do event('utterance',nil,nil,i) end
            event('gift','player:1','npc:2',2201)
            assert(#storage.memory[2].episodes==1)
            assert(R.memories(2,'player:1',3)[1].kind=='gift')
        """)

    def test_memory_counters_and_repeated_kindness_drift(self):
        self.check("""
            neutral()
            for i=1,3 do event('gift','player:1','npc:2',i) end
            assert(storage.npcs[2].personality.base_axes==nil)
            event('helped','player:1','npc:2',4)
            local p=storage.npcs[2].personality
            assert(p.axes.cooperation>0.5 and #p.drift_log==1)
            local c=storage.memory[2].counters['player:1']
            assert(c.gifts==3 and c.helped==1 and c.kindness==4 and c.last_tick==4)
            event('promise_kept','player:1','npc:2',5)
            event('promise_broken','player:1','npc:2',6)
            event('trade_completed','player:1','npc:2',7)
            assert(c.promises_kept==1 and c.promises_broken==1 and c.trades==1)
            assert(p.drift_log[2].axis=='grudge' and p.drift_log[3].axis=='cooperation')
        """)

    def test_labels_and_memories(self):
        for trust, affinity, fear, grudge, label in (
            (0, 0, 0, 0, "neutral"), (50, 30, 0, 0, "ally"),
            (20, 10, 0, 0, "friend"), (0, 0, 25, 0, "wary"),
            (-20, 0, 0, 0, "rival"), (-50, 0, 0, 0, "enemy"),
            (60, 40, 0, 60, "enemy"),
        ):
            with self.subTest(label=label):
                self.check(f"""
                    storage.relations={{}}
                    R.apply(2,'npc:1',{{trust={trust},affinity={affinity},fear={fear},grudge={grudge}}},0)
                    assert(R.label(R.get(2,'npc:1',0))=='{label}')
                """)
        self.check("""
            event('attacked','npc:3','npc:2',51200)
            local m=R.memories(2,'npc:3',3)[1]
            assert(m.tick==51200 and m.actor=='npc:3' and m.kind=='attacked' and m.target=='npc:2')
        """)

    def test_stance_sign_and_range(self):
        self.check("""
            R.apply(2,'npc:1',{trust=80,affinity=60},0)
            assert(R.stance(2,'npc:1',0)>0 and R.stance(2,'npc:1',0)<=1)
            R.apply(2,'npc:3',{trust=-100,affinity=-100,grudge=100,fear=100},0)
            assert(R.stance(2,'npc:3',0)==-1)
            assert(R.stance(2,'player:1',0)==0)
        """)

    def test_refusal_cooldown_extension_and_topic_cap(self):
        self.check("""
            assert(R.note_refusal(2,'player:1','treaty',100,50))
            assert(not R.refused_recently(2,'player:1','treaty',99))
            assert(R.refused_recently(2,'player:1','treaty',149))
            assert(not R.refused_recently(2,'player:1','treaty',150))
            R.note_refusal(2,'player:1','treaty',110,10)
            assert(R.refused_recently(2,'player:1','treaty',149))
            for i=1,20 do R.note_refusal(2,'player:1','topic'..i,200+i,50) end
            local n=0; for _ in pairs(storage.refusals[2]['player:1'].topics) do n=n+1 end
            assert(n==16 and not R.refused_recently(2,'player:1','treaty',220))
            assert(not R.refused_recently(2,'player:9','treaty',120))
            local before=serialize(storage)
            assert(not R.note_refusal(2,'player:1','invalid',1e308,1e308))
            assert(before==serialize(storage))
        """)

    def test_counterpart_caps_and_deterministic_eviction(self):
        self.check("""
            for i=1,150 do
                event('gift','player:'..i,'npc:2',i)
                R.note_refusal(2,'player:'..i,'treaty',i,50)
            end
            local maps={storage.relations[2],storage.memory[2].counters,storage.refusals[2]}
            for _,map in ipairs(maps) do
                local count=0; for _ in pairs(map) do count=count+1 end
                assert(count==128 and map['player:1']==nil and map['player:150'])
            end
        """)

    def test_refused_recently_returns_false_for_invalid_ticks(self):
        self.check("""
            R.note_refusal(2,'player:1','treaty',100,50)
            local before=serialize(storage)
            assert(not R.refused_recently(2,'player:1','treaty',nil))
            for _,tick in ipairs({'125',false,true,{},function() end,0/0,math.huge,-math.huge}) do
                assert(not R.refused_recently(2,'player:1','treaty',tick))
            end
            assert(before==serialize(storage))
            assert(R.refused_recently(2,'player:1','treaty',125))
        """)

    def test_drift_stability_and_base_cap_in_both_directions(self):
        self.check("""
            P.assign(storage.npcs[1],{'good'},42)
            P.assign(storage.npcs[2],{'stubborn'},42)
            P.assign(storage.npcs[3],{'adaptable'},42)
            local a=storage.npcs[1].personality.axes.honesty
            local _,d=P.drift(storage.npcs[1],'honesty',0.1,'moral',5)
            assert(math.abs(d-0.01)<1e-10)
            local _,slow=P.drift(storage.npcs[2],'cooperation',0.1,'test',5)
            local _,fast=P.drift(storage.npcs[3],'cooperation',0.1,'test',5)
            assert(math.abs(slow-0.02)<1e-10 and math.abs(fast-0.08)<1e-10)
            for i=1,100 do P.drift(storage.npcs[1],'honesty',0.1,'up',i) end
            local p=storage.npcs[1].personality
            assert(p.base_axes.honesty==a and p.axes.honesty<=1 and p.axes.honesty-a<=0.250000001)
            for i=1,100 do P.drift(storage.npcs[1],'honesty',-0.1,'down',i) end
            assert(p.axes.honesty>=0 and a-p.axes.honesty<=0.250000001)
            assert(#p.drift_log==16 and p.drift_log[16].tick==100 and p.selected.good)
            assert(P.drift(storage.npcs[1],'unknown',1,'test')==nil)
        """)

    def test_betrayal_drift_only_victim(self):
        self.check("""
            neutral(); event('treaty_broken','npc:1','npc:2',100,{'npc:3'})
            local p=storage.npcs[2].personality
            assert(p.axes.grudge>0.5 and p.axes.cooperation<0.5 and #p.drift_log==2)
            assert(storage.npcs[1].personality.drift_log==nil and storage.npcs[3].personality.drift_log==nil)
        """)

    def test_drift_global_stability_clamp_and_invalid_metadata(self):
        self.check("""
            P.assign(storage.npcs[1],{'loyal','stubborn'},42)
            local p=storage.npcs[1].personality
            local _,d=P.drift(storage.npcs[1],'cooperation',0.1,'core',5)
            assert(math.abs(d-0.005)<1e-10)
            local before=serialize(storage)
            assert(P.drift(storage.npcs[1],'cooperation',0.1,function() end)==nil)
            assert(P.drift(storage.npcs[1],'cooperation',0.1,'bad',function() end)==nil)
            assert(P.drift(storage.npcs[1],'cooperation',math.huge,'bad',0)==nil)
            assert(before==serialize(storage))
            local npc=storage.npcs[2]
            local traits=npc.personality.traits
            P.drift(npc,'cooperation',0.02,'optional tick')
            assert(npc.personality.traits==traits and npc.personality.drift_log[1].tick==nil)
        """)

    def test_mood_effects_decay_and_read_is_copy(self):
        self.check("""
            event('attacked','player:1','npc:2',100)
            local before=serialize(storage)
            local m=R.mood(2,100+10*3600)
            assert(m.stress==6 and m.anger==7.5 and m.morale==50)
            assert(before==serialize(storage))
            m.stress=90; assert(R.mood(2,100).stress==12)
            event('gift','player:1','npc:2',100+10*3600)
            assert(R.mood(2,100+10*3600).morale==58)
            assert(R.mood(2,100+20*3600).morale==54)
            event('plan_failed','npc:1',nil,0)
            event('research_completed','npc:3',nil,0)
            assert(R.mood(1,0).stress==10 and R.mood(3,0).morale==60)
            assert(R.mood(9,0).morale==50 and storage.mood[9]==nil)
        """)

    def test_deterministic_state_for_same_events_and_different_key_insertion_order(self):
        snapshots = []
        for reverse in (False, True):
            lua = runtime()
            lua.globals().reverse = reverse
            lua.execute("""
                local payload={}
                if reverse then payload.value=5; payload.severity=1.5
                else payload.severity=1.5; payload.value=5 end
                for i=1,150 do
                    local kind=({'gift','attacked','trade_completed','promise_broken','peace_made','helped'})[i%6+1]
                    event(kind,'player:'..(i%5+1),'npc:2',i*100,{'npc:3','npc:3'},payload)
                end
                for i=1,20 do R.note_refusal(2,'player:1','topic'..i,20000+i,500) end
                R.get(2,'player:1',50000); R.mood(2,50000); R.memories(2,'player:1',3)
            """)
            snapshots.append(lua.eval("serialize(storage)"))
        self.assertEqual(*snapshots)

    def test_save_reload_reinstalls_listener_without_resetting_saved_state(self):
        self.check("""
            neutral(); event('gift','player:1','npc:2',10)
            local before=serialize(storage)
            local c=assert(load(module_sources['scripts.chronicle']))()
            local p=P
            local old_require=require
            require=function(name)
                if name=='scripts.chronicle' then return c end
                if name=='scripts.personality' then return p end
                return old_require(name)
            end
            local r=assert(load(module_sources['scripts.relations']))()
            assert(before==serialize(storage))
            c.record{kind='gift',actor='player:1',target='npc:2',tick=10,source='native'}
            assert(r.get(2,'player:1',10).trust==12 and storage.chronicle.next_seq==3)
        """)


if __name__ == "__main__":
    unittest.main()
