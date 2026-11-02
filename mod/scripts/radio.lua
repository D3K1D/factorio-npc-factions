-- How engineers sound in chat: like people on a game radio. Every line an engineer says passes
-- through Radio.line: no em or en dashes (nor a spaced hyphen standing in for one), plain quotes, at
-- most two short sentences.
local Radio={}

local MAX=140          -- characters, the length of a quick radio call
local SENTENCES=2

local DASHES={"\226\128\148","\226\128\147","\226\128\149","\226\136\146"}   -- em, en, bar, minus
local QUOTES={["\226\128\152"]="'",["\226\128\153"]="'",["\226\128\156"]='"',["\226\128\157"]='"'}
local ELLIPSIS="\226\128\166"

-- Punctuation only (every line an engineer prints, whatever its length).
function Radio.clean(text)
  if type(text)~="string" then return text end
  local s=text
  for _,d in ipairs(DASHES) do s=s:gsub("%s*"..d.."%s*",", ") end
  s=s:gsub("(%S)%s+%-%-?%s+(%S)","%1, %2")          -- "word - word": a dash in disguise
  for q,plain in pairs(QUOTES) do s=s:gsub(q,plain) end
  s=s:gsub(ELLIPSIS,"...")
  s=s:gsub(";%s*",", ")
  s=s:gsub("%s+"," "):gsub("^%s+",""):gsub("%s+$","")
  s=s:gsub(",%s*,",","):gsub(",%s*([%.!%?])","%1"):gsub("^,%s*","")
  return s
end

-- What an engineer says on its own (its strategist's words): clean, and at most two short sentences.
function Radio.line(text)
  if type(text)~="string" then return text end
  local s=Radio.clean(text)
  -- at most two sentences
  local out,count,i=nil,0,1
  while i<=#s do
    local a,b=s:find("[%.!%?]+%s",i)
    if not a then break end
    count=count+1
    if count==SENTENCES then out=s:sub(1,b-1) break end
    i=b+1
  end
  s=out or s
  if #s>MAX then
    local cut=s:sub(1,MAX)
    local last=cut:match("^.*()%s")
    s=(last and last>MAX/2) and cut:sub(1,last-1) or cut
    s=s:gsub("[,%s]+$","")
  end
  return s
end

return Radio
