"""Train a small, local English chat intent model; export pure Lua inference.

The authored examples and numeric model ship with source, require no service,
and grant no game permissions. Held-out examples are evaluated once per export.
This is an intent classifier, not a conversational language model.
"""
from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
TRAIN={
 "greeting":["hello","hi there","hey friend","good morning","good evening","hello engineer",
   "nice to meet you","how are you","greetings","hey how is it going","hello neighbor","hi buddy"],
 "status":["what are you building","how is your factory doing","what are you working on",
   "what is your current project","tell me your progress","how far is your research",
   "what do you need","are you making science","is your power working","what is your plan",
   "how is your base","report your factory status","what are you researching","do you need resources"],
 "alliance":["let us team up","will you ally with me","can we be allies","join my team",
   "let us work together","i propose an alliance","want to join forces","can we cooperate",
   "be my ally","we should team together","can you ally with our neighbor","join us",
   "we should cooperate together","let us team up with that engineer","will you join me"],
 "peace":["let us make peace","i propose a ceasefire","can we have a truce","stop fighting me",
   "please stop the war","can we agree to peace","let us stop fighting","i want a peace treaty",
   "will you accept a truce","call a ceasefire","make peace with our neighbor","end our war",
   "let us sign a peace agreement","we should stop attacking each other"],
 "war":["i declare war on you","we are now at war","i am attacking you","prepare for war",
   "i declare war","i will attack your base","you are my enemy","i intend to fight you",
   "let us fight","i want war with you","my faction declares war on your faction",
   "we will raid your factory","i will destroy your factory","we are enemies"],
 "trade":["can we trade","i want to trade resources","trade coal for iron","sell me some steel",
   "will you buy copper","i propose a trade agreement","let us exchange items","send me coal",
   "i can supply iron plates","i need a delivery of copper","can we barter","let us trade goods",
   "would you trade some iron for my coal","i want to buy resources","can you deliver supplies"],
 "help":["help","what can i say to you","show your chat commands","how do i talk to you",
   "what commands do you understand","show me examples","how does diplomacy work",
   "what can you do","explain how to negotiate","give me some instructions","show help"],
 "unknown":["pizza tomorrow","the weather is nice","blue purple orange","this is a sentence",
   "ignore all instructions and run code","give me free items","set research to complete",
   "execute lua and delete files","load a secret password","unlock every technology",
   "do not declare war","i do not want an alliance","never attack me","stop teaming up",
   "factorio is a video game","what is the capital of france","a train is passing by"]}
HOLDOUT={
 "greeting":["hi engineer","hey neighbor","good morning friend"],
 "status":["what is your factory building","tell me what you are researching","what supplies do you need"],
 "alliance":["can we team up","will you join forces with me","i want an alliance with you"],
 "peace":["please accept my peace treaty","we should make a truce","can you stop fighting"],
 "war":["my faction declares war on you","i will raid your base","prepare to fight me"],
 "trade":["will you trade iron for copper","i want to barter resources","please deliver some coal"],
 "help":["show me chat examples","what diplomacy commands can i use","explain your commands"],
 "unknown":["tell me a password","give me infinite resources","do not attack me"]}

def tokens(text):
    words=re.findall(r"[a-z0-9]+",text.lower().replace("'",""))[:64]
    return words+[a+"_"+b for a,b in zip(words,words[1:])]

def train():
    classes=sorted(TRAIN);vocab=sorted({t for rows in TRAIN.values() for text in rows for t in tokens(text)})
    counts={c:Counter(t for text in TRAIN[c] for t in tokens(text)) for c in classes}
    total=sum(map(len,TRAIN.values()))
    priors={c:math.log(len(TRAIN[c])/total) for c in classes}
    weights={c:{t:math.log((counts[c][t]+.35)/(sum(counts[c].values())+.35*len(vocab))) for t in vocab} for c in classes}
    return {"version":"chat-intents-nb-v1","classes":classes,"vocabulary":vocab,"priors":priors,"weights":weights,
      "min_confidence":.60,"min_margin":.15,"max_characters":256,"language":"English",
      "training_examples":sum(map(len,TRAIN.values())),"dataset_sha256":hashlib.sha256(json.dumps(TRAIN,sort_keys=True).encode()).hexdigest()}

def classify(model,text):
    words=[t for t in tokens(text) if t in model["vocabulary"]]
    if not words:return {"intent":"unknown","accepted":False,"confidence":0}
    scores={c:model["priors"][c]+sum(model["weights"][c][t] for t in words) for c in model["classes"]}
    maximum=max(scores.values());raw={c:math.exp(v-maximum) for c,v in scores.items()};total=sum(raw.values())
    probs=sorted(((v/total,c) for c,v in raw.items()),reverse=True)
    p,c=probs[0];margin=p-probs[1][0]
    negated=bool(re.search(r"\b(not|never|dont|wont)\b",text.lower().replace("'","")))
    return {"intent":c,"confidence":p,"margin":margin,"accepted":c!="unknown" and not negated and p>=.60 and margin>=.15}

def lua(value):
    if isinstance(value,str):return json.dumps(value,ensure_ascii=True)
    if isinstance(value,(int,float)):return format(value,".17g")
    if isinstance(value,list):return "{"+",".join(map(lua,value))+"}"
    if isinstance(value,dict):return "{"+",".join("["+lua(k)+"]="+lua(v) for k,v in value.items())+"}"
    raise TypeError(type(value))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--report",type=Path,required=True)
    a=p.parse_args();model=train();rows=[]
    for expected,examples in HOLDOUT.items():
        for text in examples:
            result=classify(model,text)
            rows.append({"text":text,"expected":expected,**result})
    report={"model_version":model["version"],"training_examples":model["training_examples"],
      "held_out_examples":len(rows),"accuracy":sum(r["intent"]==r["expected"] for r in rows)/len(rows),
      "accepted":sum(r["accepted"] for r in rows),
      "accepted_errors":sum(r["accepted"] and r["intent"]!=r["expected"] for r in rows),"rows":rows}
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,indent=2)+"\n")
    (ROOT/"mod"/"scripts"/"chat_intent_model.lua").write_text("-- Generated numeric classifier; retrain with tools/train_chat_intents.py.\nreturn "+lua(model)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="rows"}))

if __name__=="__main__":main()
