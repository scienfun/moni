"""브리지가 직접 내려주는 채팅 화면. 외부 파일 없이 한 장으로 동작한다."""

INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>모니</title>
<style>
:root{--bg:#f6f5f2;--panel:#fff;--ink:#1d1d1b;--sub:#77756f;--mine:#2f6fed;--mineink:#fff;--bubble:#ecebe6;--line:#dcdad3}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--sub:#8c8a84;--mine:#4b82f0;--bubble:#2a2a27;--line:#33332f}}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Noto Sans KR",sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:8px;padding:10px 16px;padding-top:calc(10px + env(safe-area-inset-top));background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0;flex:1}
header small{color:var(--sub);font-size:12px}
.tool{background:none;border:1px solid var(--line);color:var(--sub);border-radius:8px;padding:5px 10px;font-size:13px}
.tool[aria-pressed=true]{color:var(--mine);border-color:var(--mine)}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.row{display:flex;flex-direction:column;max-width:82%}
.row.me{align-self:flex-end;align-items:flex-end}
.msg{padding:9px 13px;border-radius:16px;background:var(--bubble);white-space:pre-wrap;word-break:break-word}
.me .msg{background:var(--mine);color:var(--mineink)}
.meta{font-size:11px;color:var(--sub);margin:3px 6px 0}
.empty{margin:auto;color:var(--sub);text-align:center}
form{display:flex;gap:8px;padding:10px 16px;padding-bottom:calc(10px + env(safe-area-inset-bottom));background:var(--panel);border-top:1px solid var(--line)}
input{flex:1;min-width:0;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:22px;padding:10px 16px}
input:focus{outline:2px solid var(--mine);outline-offset:-1px}
button.send,button.mic{border:0;border-radius:22px;padding:0 18px;font:inherit;font-weight:600;color:#fff;background:var(--mine)}
button.mic{background:var(--bubble);color:var(--ink);padding:0 14px}
button.mic.on{background:#d9453a;color:#fff}
button:disabled{opacity:.5}
</style>
</head>
<body>
<header>
  <h1>모니 <small id="state"></small></h1>
  <button class="tool" id="speak" aria-pressed="false" type="button">읽어주기</button>
  <button class="tool" id="clear" type="button">지우기</button>
</header>
<div id="log" aria-live="polite"></div>
<form id="form">
  <button class="mic" id="mic" type="button" hidden aria-label="음성 입력">마이크</button>
  <input id="text" autocomplete="off" placeholder="모니에게 말하기" maxlength="500">
  <button class="send" id="send">보내기</button>
</form>
<script>
const $=id=>document.getElementById(id);
const log=$("log"),text=$("text"),send=$("send"),state=$("state");
let speakOn=false;
try{speakOn=localStorage.getItem("moni.speak")==="1"}catch(e){}
$("speak").setAttribute("aria-pressed",speakOn);

const hhmm=ts=>new Date(ts*1000).toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"});
function add(entry){
  const empty=log.querySelector(".empty"); if(empty) empty.remove();
  const row=document.createElement("div"); row.className="row "+(entry.role==="user"?"me":"bot");
  const msg=document.createElement("div"); msg.className="msg"; msg.textContent=entry.text;
  const meta=document.createElement("div"); meta.className="meta";
  meta.textContent=hhmm(entry.ts)+(entry.latency_ms?" · "+(entry.latency_ms/1000).toFixed(1)+"초":"");
  row.append(msg,meta); log.append(row); log.scrollTop=log.scrollHeight;
  return row;
}
function showEmpty(){log.innerHTML='<div class="empty">아직 대화가 없어요.<br>모니에게 말을 걸어 보세요.</div>'}
async function api(path,opt){
  const r=await fetch(path,opt); const j=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error||r.status); return j;
}
async function load(){
  const h=await api("/chat/history");
  log.innerHTML=""; if(!h.messages.length) showEmpty(); else h.messages.forEach(add);
}
function say(t){
  if(!speakOn||!window.speechSynthesis) return;
  const u=new SpeechSynthesisUtterance(t); u.lang="ko-KR"; speechSynthesis.cancel(); speechSynthesis.speak(u);
}
$("form").addEventListener("submit",async e=>{
  e.preventDefault(); const t=text.value.trim(); if(!t) return;
  text.value=""; send.disabled=true; state.textContent="생각 중…";
  add({role:"user",text:t,ts:Date.now()/1000});
  try{
    const r=await api("/chat/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text:t})});
    add(r.reply); say(r.reply.speak||r.reply.text);
  }catch(err){
    add({role:"moni",text:"모니가 자는 중이에요. ("+err.message+")",ts:Date.now()/1000});
  }
  state.textContent=""; send.disabled=false; text.focus();
});
$("clear").addEventListener("click",async()=>{
  if(!confirm("대화 내역을 모두 지울까요?")) return;
  await api("/chat/history",{method:"DELETE",headers:{"Content-Type":"application/json"},body:"{}"}); showEmpty();
});
$("speak").addEventListener("click",e=>{
  speakOn=!speakOn; e.currentTarget.setAttribute("aria-pressed",speakOn);
  try{localStorage.setItem("moni.speak",speakOn?"1":"0")}catch(err){}
});
const Rec=window.SpeechRecognition||window.webkitSpeechRecognition;
if(Rec){
  const mic=$("mic"); mic.hidden=false; let rec=null;
  mic.addEventListener("click",()=>{
    if(rec){rec.stop();return}
    rec=new Rec(); rec.lang="ko-KR"; rec.interimResults=false;
    rec.onresult=ev=>{text.value=ev.results[0][0].transcript; $("form").requestSubmit()};
    rec.onend=()=>{rec=null; mic.classList.remove("on")};
    rec.onerror=()=>{rec=null; mic.classList.remove("on")};
    mic.classList.add("on"); rec.start();
  });
}
load().catch(()=>{showEmpty()});
</script>
</body>
</html>
"""
