-- Pure local learned classifier. Intent labels never execute text as Lua.
local Model=require("scripts.chat_intent_model")
local Chat={version=Model.version}
local vocab={} for _,word in ipairs(Model.vocabulary) do vocab[word]=true end
function Chat.classify(text)
  if type(text)~="string" or #text>Model.max_characters then
    return {intent="unknown",accepted=false,confidence=0,reason="invalid_text_length"}
  end
  local lower=text:lower():gsub("'","")
  local words={}
  for word in lower:gmatch("[a-z0-9]+") do words[#words+1]=word;if #words>=64 then break end end
  local features={}
  for i,word in ipairs(words) do
    if vocab[word] then features[#features+1]=word end
    local pair=words[i+1] and word.."_"..words[i+1]
    if pair and vocab[pair] then features[#features+1]=pair end
  end
  if #features==0 then return {intent="unknown",accepted=false,confidence=0,reason="unknown_vocabulary"} end
  local scores,maximum={},-math.huge
  for _,class in ipairs(Model.classes) do
    local score=Model.priors[class]
    for _,feature in ipairs(features) do score=score+Model.weights[class][feature] end
    scores[class]=score;maximum=math.max(maximum,score)
  end
  local total=0
  for _,class in ipairs(Model.classes) do scores[class]=math.exp(scores[class]-maximum);total=total+scores[class] end
  local best,first,second=nil,-1,-1
  for _,class in ipairs(Model.classes) do
    local p=scores[class]/total
    if p>first then best=class;second=first;first=p elseif p>second then second=p end
  end
  local negated=false
  for _,word in ipairs(words) do if word=="not" or word=="never" or word=="dont" or word=="wont" then negated=true end end
  return {intent=best,confidence=first,margin=first-second,
    accepted=best~="unknown" and not negated and first>=Model.min_confidence and first-second>=Model.min_margin,
    reason=negated and "negated_request" or nil,model=Model.version}
end
return Chat
