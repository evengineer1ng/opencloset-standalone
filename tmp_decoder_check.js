
const $=id=>document.getElementById(id);
let audioBuf=null, audioEl=null, decodedEvents=[], decodedChars=[];
// Shared timestamp protocol. Keep this block mirrored with dbooth.html.
const TIMESTAMP_PROTOCOL={
 alphabet:"abcdefghijklmnopqrstuvwxyz",
 punctuationMap:{".":0,",":1,"?":2,"!":3,";":4,":":5},
 fallbackScale:[220.00,246.94,277.18,329.63,369.99,440.00,493.88],
 stylePresets:{
  opera:{dur:0.32,freqs:[220.00,261.63,293.66,329.63,392.00,440.00,523.25]},
  rap:{dur:0.17},
  blues:{dur:0.38,freqs:[220.00,261.63,293.66,311.13,329.63,392.00,440.00]},
  country:{dur:0.30,freqs:[220.00,246.94,277.18,329.63,369.99,440.00,493.88]},
  speak:{dur:0.105},
  podcast:{dur:0.135}
 },
 timing:{
  cellRatio:0.52,
  noteAdvanceMultiplier:1.04,
  spaceRestMultiplier:2.35,
  punctRestMultiplier:3.25,
  pairGapMs:170,
  spaceGapMs:210,
  punctGapMultiplier:1.45,
  maskFloor:0.16
 },
 decoderDefaults:{
  hopMs:24,
  winMs:72,
  minRms:0.012,
  minSegMs:55,
  mergeGapMs:46,
  pairGapMs:170,
  spaceGapMs:210,
  harsh:0.42,
  maskFloor:0.16
 }
};
const ALPHABET=TIMESTAMP_PROTOCOL.alphabet;
function timestampPreset(styleKey){
 const preset=TIMESTAMP_PROTOCOL.stylePresets[styleKey] || TIMESTAMP_PROTOCOL.stylePresets.opera;
 const freqs=(preset.freqs && preset.freqs.length===7) ? preset.freqs.slice() : TIMESTAMP_PROTOCOL.fallbackScale.slice();
 return {key:styleKey,dur:Number(preset.dur||0.32),freqs:freqs};
}
function timestampBase(){ return expectedFreqs().length || TIMESTAMP_PROTOCOL.fallbackScale.length || 7; }
function freqToMidi(f){return 69+12*Math.log2(f/440)}
function rms(x){let s=0;for(let i=0;i<x.length;i++)s+=x[i]*x[i];return Math.sqrt(s/Math.max(1,x.length))}
function median(a){if(!a.length)return 0;const b=a.slice().sort((x,y)=>x-y);return b[Math.floor(b.length/2)]}
function activePreset(){return timestampPreset($('style').value)}
function currentBpm(){
 return Math.max(1, Number($('tempo').value) || 120);
}
function styleCellSeconds(){
 const preset=activePreset();
 if(!preset) return 0;
 return (preset.dur||0.32)*TIMESTAMP_PROTOCOL.timing.cellRatio*(120/currentBpm());
}
function autocorrPitch(samples,sr,minF=70,maxF=1200){
 const r=rms(samples); if(r<Number($('minRms').value)) return {freq:0,conf:0,rms:r};
 let bestLag=0,best=0; const minLag=Math.floor(sr/maxF), maxLag=Math.floor(sr/minF);
 for(let lag=minLag;lag<=maxLag;lag++){
  let sum=0,a=0,b=0;
  for(let i=0;i<samples.length-lag;i++){const x=samples[i],y=samples[i+lag];sum+=x*y;a+=x*x;b+=y*y}
  const c=sum/Math.sqrt((a*b)||1);
  if(c>best){best=c;bestLag=lag}
 }
 return {freq:bestLag?sr/bestLag:0,conf:Math.max(0,Math.min(1,best)),rms:r};
}
function expectedFreqs(){
 const preset=activePreset();
 return preset && preset.freqs && preset.freqs.length ? preset.freqs.slice() : [];
}
function quantile(list,q){
 const arr=(list||[]).filter(Number.isFinite).slice().sort((a,b)=>a-b);
 if(!arr.length) return 0;
 const idx=(arr.length-1)*Math.max(0,Math.min(1,q));
 const lo=Math.floor(idx), hi=Math.ceil(idx);
 if(lo===hi) return arr[lo];
 const mix=idx-lo;
 return arr[lo]*(1-mix)+arr[hi]*mix;
}
function nearestBin(freq){
 const fs=expectedFreqs(); let best=0,bd=Infinity,bf=freq,bo=0;
 const candidates=[freq,freq*2,freq*3,freq*4,Math.max(1,freq/2)];
 for(let j=0;j<candidates.length;j++){
  const cand=candidates[j];
  for(let i=0;i<fs.length;i++){
   const d=Math.abs(freqToMidi(cand)-freqToMidi(fs[i])) + j*0.08;
   if(d<bd){bd=d;best=i;bf=cand;bo=j}
  }
 }
 const conf=Math.exp(-bd/(Number($('harsh').value)||0.42));
 return {bin:best,dist:bd,conf:Math.max(0,Math.min(1,conf)),target:fs[best],mappedFreq:bf,octaveHint:bo};
}
function smoothFrames(frames){
 if(!frames.length) return frames;
 return frames.map(function(fr,i){
  const prev=frames[i-1], next=frames[i+1];
  if(prev && next && prev.bin===next.bin && fr.bin!==prev.bin){
   return Object.assign({}, fr, {
    bin:prev.bin,
    mappedFreq:median([prev.mappedFreq||prev.f, fr.mappedFreq||fr.f, next.mappedFreq||next.f]),
    conf:Math.max(fr.conf, Math.min(prev.conf,next.conf)*0.92),
    smoothed:true
   });
  }
  return fr;
 });
}
function deriveTimingModel(events){
 const presetCell=styleCellSeconds();
 const cell=presetCell || 0.16;
 const pairGapUi=Number($('pairGap').value)/1000;
 const spaceGapUi=Number($('spaceGap').value)/1000;
 return {
  cell:cell,
  pairGap:Math.max(0.05,Math.min(0.95,pairGapUi || (TIMESTAMP_PROTOCOL.timing.pairGapMs/1000))),
  spaceGap:Math.max(0.09,Math.min(1.4,spaceGapUi || (TIMESTAMP_PROTOCOL.timing.spaceGapMs/1000))),
  punctGap:Math.max(0.14,Math.min(2.2,(spaceGapUi || (TIMESTAMP_PROTOCOL.timing.spaceGapMs/1000))*TIMESTAMP_PROTOCOL.timing.punctGapMultiplier))
 };
}
function maskThreshold(chars){
 const floor=Number($('maskFloor').value)||TIMESTAMP_PROTOCOL.timing.maskFloor;
 const confs=chars.map(c=>c.conf).filter(Number.isFinite);
 if(!confs.length) return floor;
 return Math.max(floor,Math.min(0.23,quantile(confs,0.28)));
}
function drawWave(){
 const c=$('wave'),ctx=c.getContext('2d'); const W=c.width=c.clientWidth*devicePixelRatio,H=c.height=c.clientHeight*devicePixelRatio;
 ctx.clearRect(0,0,W,H); ctx.strokeStyle='#7ad7f0'; ctx.lineWidth=1*devicePixelRatio; ctx.beginPath();
 if(!audioBuf){ctx.fillStyle='#99a4b3';ctx.fillText('no audio',20,30);return}
 const data=audioBuf.getChannelData(0); const step=Math.max(1,Math.floor(data.length/W));
 for(let x=0;x<W;x++){let mn=1,mx=-1; for(let j=0;j<step;j++){const v=data[x*step+j]||0; if(v<mn)mn=v;if(v>mx)mx=v} ctx.moveTo(x,H/2+mn*H*.45);ctx.lineTo(x,H/2+mx*H*.45)}
 ctx.stroke();
 ctx.fillStyle='rgba(255,216,77,.8)';
 decodedEvents.forEach(e=>{const x=e.t/audioBuf.duration*W;ctx.fillRect(x,0,1*devicePixelRatio,H)});
}
async function loadFile(file){
 const arr=await file.arrayBuffer(); const ac=new (window.AudioContext||window.webkitAudioContext)(); audioBuf=await ac.decodeAudioData(arr.slice(0));
 if(audioEl) URL.revokeObjectURL(audioEl.src); audioEl=new Audio(URL.createObjectURL(file));
 audioEl.ontimeupdate=updateClock; audioEl.onplay=()=>startCrawl(); audioEl.onpause=()=>stopCrawl(false); audioEl.onended=()=>stopCrawl(true);
 $('status').textContent=`loaded ${file.name} · ${audioBuf.duration.toFixed(2)}s`; drawWave();
}
function analyze(){
 if(!audioBuf)return alert('Load audio first.');
 const sr=audioBuf.sampleRate, data=audioBuf.getChannelData(0);
 const hop=Math.floor(sr*Number($('hopMs').value)/1000), win=Math.floor(sr*Number($('winMs').value)/1000);
 const frames=[];
 for(let pos=0;pos+win<data.length;pos+=hop){
  const sl=data.subarray(pos,pos+win); const p=autocorrPitch(sl,sr); const t=(pos+win/2)/sr;
  if(p.freq>0){const nb=nearestBin(p.freq); frames.push({t,f:p.freq,mappedFreq:nb.mappedFreq,bin:nb.bin,conf:p.conf*nb.conf,rms:p.rms,dist:nb.dist,octaveHint:nb.octaveHint})}
 }
 decodedEvents=segmentFrames(smoothFrames(frames));
 decodeEvents(); drawWave(); renderOutputsFortified();
}
function expandRepeatedBins(events){
 const cell=styleCellSeconds();
 const advance=cell*TIMESTAMP_PROTOCOL.timing.noteAdvanceMultiplier;
 if(!advance || !events.length) return events;
 const out=[];
 for(const ev of events){
  const count=Math.max(1,Math.round(ev.dur/advance));
  if(count===1){out.push(ev);continue}
  const span=Math.max(ev.dur, advance*count);
  for(let i=0;i<count;i++){
   const start=ev.start + span*(i/count);
   const end=(i===count-1)?ev.end:(ev.start + span*((i+1)/count));
   out.push({
    t:(start+end)/2,
    start,
    end,
    dur:end-start,
    bin:ev.bin,
    conf:ev.conf,
    freq:ev.freq,
    mappedFreq:ev.mappedFreq,
    gap:0
   });
  }
 }
 return out.map((e,i,a)=>{e.gap=i?Math.max(0,e.start-a[i-1].end):0;return e});
}
function segmentFrames(frames){
 const minSeg=Number($('minSeg').value)/1000, mergeGap=Number($('mergeGap').value)/1000;
 const segs=[]; let cur=null;
 for(const fr of frames){
  if(!cur){cur={start:fr.t,end:fr.t,frames:[fr]};continue}
  const prev=cur.frames[cur.frames.length-1];
  const same=fr.t-cur.end<=mergeGap && fr.bin===prev.bin;
  if(same){cur.end=fr.t;cur.frames.push(fr)} else {segs.push(cur);cur={start:fr.t,end:fr.t,frames:[fr]}}
 }
 if(cur)segs.push(cur);
 const events=segs.filter(s=>s.end-s.start>=minSeg).map(s=>{
  const bins=s.frames.map(f=>f.bin); const b=mode(bins); const conf=median(s.frames.map(f=>f.conf)); const freq=median(s.frames.map(f=>f.f));
  const mappedFreq=median(s.frames.map(f=>f.mappedFreq||f.f));
  return {t:(s.start+s.end)/2,start:s.start,end:s.end,dur:s.end-s.start,bin:b,conf,freq,mappedFreq,gap:0};
 }).map((e,i,a)=>{e.gap=i?e.start-a[i-1].end:0;return e});
 return expandRepeatedBins(events);
}
function mode(a){const m={};let best=a[0]||0,bc=0;for(const x of a){m[x]=(m[x]||0)+1;if(m[x]>bc){bc=m[x];best=x}}return best}
function decodeEvents(){
 decodedChars=[]; let pending=null, out=[]; const timing=deriveTimingModel(decodedEvents);
 const spaceGap=timing.spaceGap, pairGap=timing.pairGap, punctGap=timing.punctGap;
 const punctMap=Object.fromEntries(Object.entries(TIMESTAMP_PROTOCOL.punctuationMap).map(([ch,bin])=>[bin,ch]));
 const base=timestampBase();
 for(const ev of decodedEvents){
  if(ev.gap>punctGap && punctMap[ev.bin]){flushPending(out,pending,ev.t); pending=null; addChar(out,punctMap[ev.bin],ev.start,ev.conf,'punct-marker '+ev.bin); continue}
  if(ev.gap>spaceGap){flushPending(out,pending,ev.t); pending=null; addChar(out,' ',ev.start,ev.conf,'gap-space')}
  if(!pending){pending=ev;continue}
  if(ev.start-pending.end>pairGap){flushPending(out,pending,ev.t); pending=ev; continue}
  const code=pending.bin*base+ev.bin;
  let ch='�', why='out-of-range';
  if(code>=0&&code<ALPHABET.length){ch=ALPHABET[code];why='letter'}
  addChar(out,ch,(pending.t+ev.t)/2,Math.min(pending.conf,ev.conf),why+` ${pending.bin},${ev.bin}`);
  pending=null;
 }
 flushPending(out,pending,audioBuf?audioBuf.duration:0);
decodedChars=out;
}
function flushPending(out,p,t){ if(p) addChar(out,'�',p.t,p.conf,'unpaired '+p.bin) }
function addChar(out,ch,t,conf,why){out.push({ch,t,conf,why})}
function renderOutputs(){
 const text=decodedChars.map(c=>c.conf<0.23?'·':c.ch).join('').replace(/ +/g,' ');
 $('decoded').innerHTML=escapeHtml(text||'No decoded characters.')
 const good=decodedChars.filter(c=>c.conf>=0.5).length, weak=decodedChars.filter(c=>c.conf<0.23).length;
 const avg=decodedChars.length?decodedChars.reduce((s,c)=>s+c.conf,0)/decodedChars.length:0;
 const freqs=expectedFreqs().map(f=>f.toFixed(2)).join(', ');
 $('report').innerHTML=`events: ${decodedEvents.length}\nchars: ${decodedChars.length}\navg confidence: ${avg.toFixed(3)}\nhigh confidence chars: ${good}\nmasked/weak chars: ${weak}\nexpected bins (Hz): ${freqs}\nstyle cell s: ${styleCellSeconds().toFixed(3)}\ncodec base: ${timestampBase()}\n\nThis is not speech recognition. It is a timestamp/base-${timestampBase()} note decoder. If the source was not encoded with matching settings, garble is expected and honest.`;
 $('events').value=decodedEvents.map((e,i)=>`${String(i).padStart(4,'0')} t=${e.t.toFixed(3)} bin=${e.bin} freq=${e.freq.toFixed(1)} mapped=${(e.mappedFreq||e.freq).toFixed(1)} conf=${e.conf.toFixed(2)} gap=${e.gap.toFixed(3)}`).join('\n');
 const crawlText=(text||'NO DECODABLE MESSAGE').replace(/�/g,'?').replace(/·/g,'*').toUpperCase(); $('crawl').textContent=crawlText;
}
function codeToChar(code){
 if(code>=0&&code<ALPHABET.length) return ALPHABET[code];
 return 'ï¿½';
}
function bestRepairFromNeighbors(prev,next){
 const candidates=[];
 const base=timestampBase();
 for(let a=-1;a<=1;a++){
  for(let b=-1;b<=1;b++){
   const left=prev.bin+a, right=next.bin+b;
   if(left<0||left>=base||right<0||right>=base) continue;
   const code=left*base+right;
   const ch=codeToChar(code);
   if(ch==='ï¿½') continue;
   const penalty=Math.abs(a)+Math.abs(b);
   const punctuation=/[.,!?]/.test(ch)?0.12:0;
   candidates.push({ch,score:(prev.conf+next.conf)/2 - penalty*0.18 - punctuation,left,right});
  }
 }
 candidates.sort((x,y)=>y.score-x.score);
 return candidates[0]||null;
}
function cleanupPunctuation(chars){
 const out=[];
 for(let i=0;i<chars.length;i++){
  const cur=Object.assign({}, chars[i]);
  const prev=out[out.length-1];
  const next=chars[i+1];
  if(/[.,!?;:]/.test(cur.ch) && prev && /[.,!?;: ]/.test(prev.ch) && cur.conf<0.42) continue;
  if(cur.ch===' ' && prev && prev.ch===' ') continue;
  if(cur.ch==='ï¿½' && prev && next && /[a-z]/i.test(prev.ch) && /[a-z]/i.test(next.ch) && cur.conf<0.22) continue;
  out.push(cur);
 }
 return out;
}
function repairDecodedChars(chars){
 const repaired=chars.map(c=>Object.assign({}, c));
 for(let i=0;i<repaired.length;i++){
  const cur=repaired[i];
  if(cur.ch!=='ï¿½' && cur.conf>=0.24) continue;
  const prevEvent=decodedEvents[i*2];
  const nextEvent=decodedEvents[i*2+1];
  if(!prevEvent||!nextEvent) continue;
  const best=bestRepairFromNeighbors(prevEvent,nextEvent);
  if(!best) continue;
  if(best.score>=0.08 || (cur.ch==='ï¿½' && cur.conf<0.2)){
   cur.ch=best.ch;
   cur.conf=Math.max(cur.conf, Math.min(0.49, best.score+0.18));
   cur.why=`repair ${best.left},${best.right}`;
  }
 }
 return cleanupPunctuation(repaired);
}
function renderOutputsFortified(){
 const raw=decodedChars.map(c=>c.ch).join('').replace(/ +/g,' ');
 const threshold=maskThreshold(decodedChars);
 const text=decodedChars.map(c=>c.conf<threshold?'Â·':c.ch).join('').replace(/ +/g,' ');
 $('decoded').innerHTML=escapeHtml((text||'No decoded characters.') + `\n\nraw: ${raw||'(empty)'}`);
 const good=decodedChars.filter(c=>c.conf>=0.5).length, weak=decodedChars.filter(c=>c.conf<threshold).length;
 const avg=decodedChars.length?decodedChars.reduce((s,c)=>s+c.conf,0)/decodedChars.length:0;
 const freqs=expectedFreqs().map(f=>f.toFixed(2)).join(', ');
 const timing=deriveTimingModel(decodedEvents);
 $('report').innerHTML=`events: ${decodedEvents.length}\nchars: ${decodedChars.length}\navg confidence: ${avg.toFixed(3)}\nhigh confidence chars: ${good}\nmasked/weak chars: ${weak}\nmask threshold: ${threshold.toFixed(3)}\nexpected bins (Hz): ${freqs}\nstyle cell s: ${styleCellSeconds().toFixed(3)}\ncodec base: ${timestampBase()}\nderived cell s: ${timing.cell.toFixed(3)}\nderived pair gap s: ${timing.pairGap.toFixed(3)}\nderived space gap s: ${timing.spaceGap.toFixed(3)}\n\nThis is not speech recognition. It is a timestamp/base-${timestampBase()} note decoder. If the source was not encoded with matching settings, garble is expected and honest.`;
 $('events').value=decodedEvents.map((e,i)=>`${String(i).padStart(4,'0')} t=${e.t.toFixed(3)} bin=${e.bin} freq=${e.freq.toFixed(1)} mapped=${(e.mappedFreq||e.freq).toFixed(1)} conf=${e.conf.toFixed(2)} gap=${e.gap.toFixed(3)}`).join('\n');
 const crawlText=(text||'NO DECODABLE MESSAGE').replace(/ï¿½/g,'?').replace(/Â·/g,'*').toUpperCase();
 $('crawl').textContent=crawlText;
}
function escapeHtml(s){return String(s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]))}
function updateClock(){
 if(!audioEl)return; $('clock').textContent=`${audioEl.currentTime.toFixed(2)} / ${(audioEl.duration||0).toFixed(2)}`;
 let cur='—'; for(const c of decodedChars){if(c.t<=audioEl.currentTime)cur=c.ch;else break} $('liveChar').textContent='current: '+cur;
}
function startCrawl(){const cr=$('crawl');cr.classList.remove('playing');void cr.offsetWidth; const dur=Math.max(12,audioEl.duration||40);cr.style.setProperty('--crawlDur',dur+'s');cr.classList.add('playing')}
function stopCrawl(done){$('crawl').classList.remove('playing'); if(done) updateClock()}
$('file').onchange=e=>{const f=e.target.files&&e.target.files[0]; if(f)loadFile(f)};
$('decode').onclick=analyze;$('play').onclick=()=>{if(audioEl)audioEl.play()};$('stop').onclick=()=>{if(audioEl){audioEl.pause();audioEl.currentTime=0;updateClock()}stopCrawl(false)};
['tempo','vib','bright','cont'].forEach(id=>$(id).oninput=()=>$(id+'V').textContent=Number($(id).value).toFixed(id==='vib'?3:(id==='tempo'?0:2)));
function applyTimestampProtocolDefaults(){
 $('hopMs').value=TIMESTAMP_PROTOCOL.decoderDefaults.hopMs;
 $('winMs').value=TIMESTAMP_PROTOCOL.decoderDefaults.winMs;
 $('minRms').value=TIMESTAMP_PROTOCOL.decoderDefaults.minRms;
 $('minSeg').value=TIMESTAMP_PROTOCOL.decoderDefaults.minSegMs;
 $('mergeGap').value=TIMESTAMP_PROTOCOL.decoderDefaults.mergeGapMs;
 $('spaceGap').value=TIMESTAMP_PROTOCOL.decoderDefaults.spaceGapMs;
 $('pairGap').value=TIMESTAMP_PROTOCOL.decoderDefaults.pairGapMs;
 $('harsh').value=TIMESTAMP_PROTOCOL.decoderDefaults.harsh;
 $('maskFloor').value=TIMESTAMP_PROTOCOL.decoderDefaults.maskFloor;
}
applyTimestampProtocolDefaults();
setInterval(updateClock,100);
