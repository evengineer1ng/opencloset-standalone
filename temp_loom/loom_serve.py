#!/usr/bin/env python3
"""The Loom Booth, on the web — a touch surface that runs anywhere with a browser (desktop, and
the phone via Termux + localhost). Stdlib HTTP server + one page; the BROWSER speaks (Web Speech
API), so no audio plumbing and no per-OS TTS. Same headless engine as the CLI (booth.render_session).

    python loom_serve.py --tape f1=data/f1_barcelona_2026.json --tape news=data/rss_f1news.json
    # then open http://127.0.0.1:8765  (on the phone: open it in the browser)
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from booth import render_session
from oradio_engine.antenna import Antenna, Source
from oradio_engine.mix import Mixer

ROOT = Path(__file__).resolve().parent
VOICES = ["intern", "town_crier", "prime_minister"]
COLOR_MODELS = ["qwen3:8b", "phi3:3.8b", "tinyllama:1.1b", "smollm2:135m"]
_MIXER_KEYS = {"depth", "flavour", "salience", "curiosity", "continuity", "voice", "color", "color_model"}

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Loom Booth</title><style>
 body{background:#0c0d10;color:#eef1f4;font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;padding:14px}
 h1{color:#7ad7f0;font:bold 20px Consolas,monospace;margin:0 0 2px} .sub{color:#9aa3af;font-size:12px;margin-bottom:12px}
 .rack{display:flex;flex-wrap:wrap;gap:14px;background:#16181d;border:1px solid #2b2f37;border-radius:10px;padding:12px}
 .fader{display:flex;flex-direction:column;min-width:130px} label{color:#9aa3af;font-size:12px;margin-bottom:4px}
 input[type=range]{width:150px;height:34px} select,input[type=checkbox]{font-size:16px;min-height:34px}
 .ant{margin:12px 0} .ant label{display:inline-flex;align-items:center;gap:6px;margin-right:14px;color:#eef1f4;font-size:15px}
 .bar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
 button{background:#16181d;color:#eef1f4;border:1px solid #2b2f37;border-radius:8px;padding:12px 18px;font-size:16px}
 #play{background:#5ad27a;color:#000;font-weight:bold} #now{color:#7ad7f0;min-height:24px;margin:8px 0;font-size:17px}
 #tape{background:#08090b;border-radius:8px;padding:8px;height:46vh;overflow:auto;white-space:pre-wrap;font:14px Consolas,monospace}
 #tape div.kept{color:#5ad27a}
</style></head><body>
<h1>THE LOOM BOOTH</h1><div class=sub>ride the faders &middot; hear the tape &middot; keep what you want</div>
<div class=rack id=rack></div>
<div class=ant id=ant></div>
<div class=bar>
 <button id=play>&#9654; PLAY</button><button id=stop>&#9632; STOP</button>
 <button id=keep>KEEP</button><button id=save>SAVE MIXTAPE</button>
 <label style=color:#9aa3af>tempo <input type=range id=tempo min=400 max=5000 step=100 value=2200></label>
</div>
<div id=now>press play</div><div id=tape></div>
<script>
let playing=false, idx=0, stories=[], kept=[];
const F={depth:[0,4,1,2],salience:[0,1,0.05,0.4],curiosity:[0,3,1,0]};
async function init(){
 const s=await (await fetch('/state')).json();
 const rack=document.getElementById('rack');
 for(const [k,[mn,mx,st,dv]] of Object.entries(F)){
  rack.insertAdjacentHTML('beforeend',`<div class=fader><label>${k.toUpperCase()}: <b id=v_${k}>${dv}</b></label>
   <input type=range id=${k} min=${mn} max=${mx} step=${st} value=${dv} oninput="v_${k}.textContent=this.value"></div>`);}
 rack.insertAdjacentHTML('beforeend',`<div class=fader><label>FLAVOUR</label><select id=flavour>
   <option>both</option><option>back</option><option>forward</option></select></div>`);
 rack.insertAdjacentHTML('beforeend',`<div class=fader><label>VOICE</label><select id=voice>${s.voices.map(v=>`<option>${v}</option>`).join('')}</select></div>`);
 rack.insertAdjacentHTML('beforeend',`<div class=fader><label><input type=checkbox id=continuity checked> CONTINUITY</label>
   <label><input type=checkbox id=color> COLOR</label>
   <select id=color_model>${s.color_models.map(m=>`<option>${m}</option>`).join('')}</select></div>`);
 const ant=document.getElementById('ant');
 ant.innerHTML='<b style=color:#9aa3af>ANTENNA:</b> '+s.sources.map(x=>`<label><input type=checkbox class=src value="${x.name}" checked> ${x.name} (${x.count})</label>`).join('');
}
function mixer(){return {depth:+depth.value,salience:+salience.value,curiosity:+curiosity.value,
  flavour:flavour.value,voice:voice.value,continuity:continuity.checked,color:color.checked,color_model:color_model.value};}
function enabled(){return [...document.querySelectorAll('.src:checked')].map(c=>c.value);}
function speak(t){return new Promise(res=>{ if(!window.speechSynthesis){setTimeout(res,+tempo.value);return;}
  const u=new SpeechSynthesisUtterance(t.replace(/^\\[lap \\w+\\] /,'')); u.onend=()=>setTimeout(res,200); speechSynthesis.cancel(); speechSynthesis.speak(u);});}
async function run(){
 for(;playing && idx<stories.length; idx++){
   const line=stories[idx]; const d=document.createElement('div'); d.textContent=line; d.dataset.line=line;
   document.getElementById('tape').appendChild(d); d.scrollIntoView(); now.textContent=line;
   await speak(line); await new Promise(r=>setTimeout(r,+tempo.value*0.1));
 }
 if(idx>=stories.length) now.textContent='— end of tape —';
 playing=false; play.textContent='\\u25B6 PLAY';
}
play.onclick=async()=>{
 if(playing) return; now.textContent='looming...';
 const r=await fetch('/narrate',{method:'POST',body:JSON.stringify({enabled:enabled(),mixer:mixer()})});
 const j=await r.json();
 stories=j.stories.map(([lap,line])=>(lap?`[lap ${lap}] `:'')+line);
 if(j.questions.length) stories.push('— questions —',...j.questions.map(([q,])=>'? '+q));
 document.getElementById('tape').innerHTML=''; idx=0; playing=true; play.textContent='\\u275A\\u275A PAUSE'; run();
};
stop.onclick=()=>{playing=false; if(window.speechSynthesis)speechSynthesis.cancel(); play.textContent='\\u25B6 PLAY';};
keep.onclick=()=>{const t=document.getElementById('now').textContent; if(t&&kept[kept.length-1]!==t){kept.push(t);
  [...document.querySelectorAll('#tape div')].filter(d=>d.dataset.line===t).forEach(d=>d.className='kept'); now.textContent='\\u2605 kept';}};
save.onclick=()=>{const b=new Blob(['# loom mixtape\\n\\n'+kept.join('\\n')],{type:'text/plain'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='mixtape.txt'; a.click();};
init();
</script></body></html>"""


class Booth:
    antenna = None
    rules = None
    inquiry = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/state":
            self._send(200, json.dumps({
                "sources": [{"name": s.name, "count": len(s.events)} for s in Booth.antenna.sources],
                "voices": VOICES, "color_models": COLOR_MODELS}))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path != "/narrate":
            self._send(404, "{}"); return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        enabled = set(body.get("enabled", []))
        for s in Booth.antenna.sources:
            s.enabled = s.name in enabled
        m = {k: v for k, v in (body.get("mixer") or {}).items() if k in _MIXER_KEYS}
        mixer = Mixer(**m)
        stories, questions = render_session(Booth.antenna, mixer, rules=Booth.rules, inquiry=Booth.inquiry)
        self._send(200, json.dumps({"stories": [[l, t] for l, t in stories], "questions": questions}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", action="append", default=[], help="name=path (repeatable)")
    ap.add_argument("--rules", default="data/f1_causal_rules.json")
    ap.add_argument("--inquiry", default="data/inquiry/f1.json")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    Booth.antenna = Antenna()
    for spec in args.tape or ["f1=data/f1_barcelona_2026.json"]:
        name, path = spec.split("=", 1) if "=" in spec else (Path(spec).stem, spec)
        try:
            Booth.antenna.add(Source.from_tape(name, path))
        except Exception as exc:
            print(f"[antenna] skipped {name}: {exc}")
    if args.rules and Path(args.rules).exists():
        Booth.rules = json.load(open(args.rules, encoding="utf-8"))
    if args.inquiry and Path(args.inquiry).exists():
        from oradio_engine.inquiry import Inquiry
        Booth.inquiry = Inquiry.from_file(args.inquiry)

    print(f"Loom Booth on http://127.0.0.1:{args.port}  (tapes: {Booth.antenna.names()})")
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
