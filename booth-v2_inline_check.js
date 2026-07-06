
const OFFICIAL_CODEC_SPEC={"codec_version":"loom.timestamp.codec.v1","artifact_kind":"audio_timestamp_text","alphabet":"abcdefghijklmnopqrstuvwxyz","punctuation_map":{".":0,",":1,"?":2,"!":3,";":4,":":5},"style_presets":{"opera":{"scale":"pent","freqs":[220.0,261.63,293.66,329.63,392.0,440.0,523.25],"dur":0.32},"rap":{"scale":"narrow","freqs":[220.0,233.08,246.94],"dur":0.17},"blues":{"scale":"blues","freqs":[220.0,261.63,293.66,311.13,329.63,392.0,440.0],"dur":0.38},"country":{"scale":"major","freqs":[220.0,246.94,277.18,329.63,369.99,440.0,493.88],"dur":0.3},"speak":{"scale":"narrow","freqs":[220.0,233.08,246.94],"dur":0.105},"podcast":{"scale":"narrow","freqs":[220.0,233.08,246.94],"dur":0.135}},"timing":{"cell_ratio":0.52,"space_gap_ms":210,"pair_gap_ms":170,"punct_gap_multiplier":1.45,"punct_rest_multiplier":3.25,"space_rest_multiplier":2.35,"mask_floor":0.16},"checksum":{"algorithm":"sha256","length":12,"field":"render_text"},"visual_reserved":{"planned_codec":"svg_glyph_strip","enabled":false}};

const $ = id => document.getElementById(id);
const SOURCE_SYMBOLS = OFFICIAL_CODEC_SPEC.alphabet + " .,!?;:";
const GLYPH_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const GLYPH_RENDER = ["◼","◻","◆","◇","●","○","▲","△","■","□","⬢","⬡","✦","✧","✳","✶","☉","☌","☍","☰","☱","☲","☳","☴","☵","☶","☷","♠","♣","♥","♦","♪","♫","☼","☾","☽","⚑","⚐","✚","✜","✣","✤","✥","✺","✹","✸","✷","✵","✴","✲","✱","✰","⟐","⟡","⟢","⟣","⟤","⟥","⟦","⟧","⟨","⟩","⌁","⌂"];
const SAMPLE_TEXT = "maximum recoverable meaning per byte. booth v2 emits one deterministic payload across varints, glyphs, scatter, png, and audio.";
const LOOM_PIXEL_CODEC = {
 magic:[76,80,88,49],
 pad:14,
 cell:12,
 membrane:1
};
let lastPayload = null;
let lastSvgMarkup = "";
let loadedPacket = null;
let audioCtx = null;
let activeNodes = [];
let activeOscillators = [];
let activeWavUrl = "";
let lastLoomPixelFrame = null;

function clamp(n, lo, hi){ return Math.max(lo, Math.min(hi, n)); }
function parsePositiveInt(value, fallback){
 const n = Number(value);
 return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}
function escapeHtml(text){
 return String(text || "").replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
}
function normalizeText(text){
 const allowed = new Set(SOURCE_SYMBOLS.split(""));
 const out = [];
 const lowered = String(text || "").toLowerCase();
 for(let i=0;i<lowered.length;i++){
  const ch = lowered[i];
  if(allowed.has(ch)) out.push(ch);
  else if(/\s/.test(ch)) out.push(" ");
 }
 return out.join("").replace(/\s+/g, " ").trim();
}
function fnv1a(text){
 let h = 2166136261 >>> 0;
 const s = String(text || "");
 for(let i=0;i<s.length;i++){
  h ^= s.charCodeAt(i);
  h = Math.imul(h, 16777619) >>> 0;
 }
 return h >>> 0;
}
function encodeVarint(value){
 let n = value >>> 0;
 const out = [];
 while(n >= 0x80){
  out.push((n & 0x7f) | 0x80);
  n >>>= 7;
 }
 out.push(n);
 return out;
}
function bytesToHex(bytes){
 return bytes.map(b => b.toString(16).padStart(2, "0")).join(" ");
}
function rgbCss(rgb){
 return "rgb(" + rgb.r + "," + rgb.g + "," + rgb.b + ")";
}
function hslToRgb(h, s, l){
 const hue = ((h % 360) + 360) % 360;
 const sat = clamp(s, 0, 1);
 const lig = clamp(l, 0, 1);
 const c = (1 - Math.abs(2 * lig - 1)) * sat;
 const hp = hue / 60;
 const x = c * (1 - Math.abs(hp % 2 - 1));
 let r1 = 0, g1 = 0, b1 = 0;
 if(hp < 1){ r1 = c; g1 = x; }
 else if(hp < 2){ r1 = x; g1 = c; }
 else if(hp < 3){ g1 = c; b1 = x; }
 else if(hp < 4){ g1 = x; b1 = c; }
 else if(hp < 5){ r1 = x; b1 = c; }
 else { r1 = c; b1 = x; }
 const m = lig - c / 2;
 return {
  r:Math.round((r1 + m) * 255),
  g:Math.round((g1 + m) * 255),
  b:Math.round((b1 + m) * 255)
 };
}
function bytesToGlyphString(bytes){
 if(!bytes.length) return "";
 let out = "";
 for(let i=0;i<bytes.length;i++){
  out += GLYPH_RENDER[bytes[i] % GLYPH_RENDER.length];
 }
 return out;
}
function bytesToGlyphBase(bytes){
 if(!bytes.length) return "";
 let out = "";
 for(let i=0;i<bytes.length;i++){
  const b = bytes[i];
  out += GLYPH_ALPHABET[(b >> 2) & 63];
  out += GLYPH_ALPHABET[((b & 3) << 4) & 63];
 }
 return out;
}
function symbolId(ch){
 const idx = SOURCE_SYMBOLS.indexOf(ch);
 return idx >= 0 ? idx : SOURCE_SYMBOLS.indexOf(" ");
}
function symbolRgb(id){
 const n = id + 1;
 const r = (n * 53) % 256;
 const g = (n * 97) % 256;
 const b = (n * 193) % 256;
 return {r:r,g:g,b:b,css:"rgb(" + r + "," + g + "," + b + ")"};
}
function buildPathTape(ids){
 const groups = [];
 for(let i=0;i<ids.length;i+=6){
  groups.push(ids.slice(i, i + 6).map(id => id.toString(36)).join("."));
 }
 return groups.join(" / ");
}
function checksumBytes(hex){
 const clean = String(hex || "").padStart(8, "0").slice(-8);
 return [
  parseInt(clean.slice(0, 2), 16) || 0,
  parseInt(clean.slice(2, 4), 16) || 0,
  parseInt(clean.slice(4, 6), 16) || 0,
  parseInt(clean.slice(6, 8), 16) || 0
 ];
}
function buildPayload(text){
 const normalized = normalizeText(text);
 const ids = normalized.split("").map(symbolId);
 const bytes = [];
 for(let i=0;i<ids.length;i++){
  const varint = encodeVarint(ids[i] + 1);
  for(let j=0;j<varint.length;j++) bytes.push(varint[j]);
 }
 const checksum = fnv1a(normalized).toString(16).padStart(8, "0");
 return {
  sourceText:String(text || ""),
  normalized:normalized,
  ids:ids,
  bytes:bytes,
  checksum:checksum,
  glyphVisual:bytesToGlyphString(bytes),
  glyphBase:bytesToGlyphBase(bytes),
  pathTape:buildPathTape(ids),
  symbolSet:[...new Set(ids)].sort((a,b)=>a-b)
 };
}
function buildLoomPixelFrame(payload){
 const head = LOOM_PIXEL_CODEC.magic.slice();
 const body = [];
 const byteLen = encodeVarint(payload.bytes.length);
 const charLen = encodeVarint(payload.ids.length);
 for(let i=0;i<byteLen.length;i++) body.push(byteLen[i]);
 for(let i=0;i<charLen.length;i++) body.push(charLen[i]);
 const sum = checksumBytes(payload.checksum);
 for(let i=0;i<sum.length;i++) body.push(sum[i]);
 const bytes = head.concat(body, payload.bytes);
 return {
  bytes:bytes,
  byteLength:payload.bytes.length,
  charLength:payload.ids.length,
  checksum:payload.checksum
 };
}
function buildSvgScatter(payload){
 const cols = parsePositiveInt($("grid-width").value, 32);
 const cell = 18;
 const radius = 5;
 const ids = payload.ids;
 const rows = Math.max(1, Math.ceil(ids.length / cols));
 const width = cols * cell + 20;
 const height = rows * cell + 20;
 const pieces = [
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + width + " " + height + '" width="' + width + '" height="' + height + '">',
  '<rect width="100%" height="100%" fill="#02070b"/>'
 ];
 for(let i=0;i<ids.length;i++){
  const id = ids[i];
  const rgb = symbolRgb(id);
  const col = i % cols;
  const row = Math.floor(i / cols);
  const x = 10 + col * cell + (id % 3) * 1.5 + cell * 0.25;
  const y = 10 + row * cell + (Math.floor(id / 3) % 3) * 1.5 + cell * 0.25;
  pieces.push('<rect x="' + x.toFixed(2) + '" y="' + y.toFixed(2) + '" width="' + radius * 2 + '" height="' + radius * 2 + '" fill="' + rgb.css + '"/>');
 }
 pieces.push("</svg>");
 return pieces.join("");
}
function drawMosaic(payload){
 const canvas = $("mosaic");
 const ctx = canvas.getContext("2d");
 const cols = parsePositiveInt($("grid-width").value, 32);
 const cell = 16;
 const pad = 12;
 const ids = payload.ids;
 const rows = Math.max(1, Math.ceil(ids.length / cols));
 canvas.width = cols * cell + pad * 2;
 canvas.height = rows * cell + pad * 2;
 ctx.fillStyle = "#02070b";
 ctx.fillRect(0, 0, canvas.width, canvas.height);
 for(let i=0;i<ids.length;i++){
  const rgb = symbolRgb(ids[i]);
  const x = pad + (i % cols) * cell;
  const y = pad + Math.floor(i / cols) * cell;
  ctx.fillStyle = rgb.css;
  ctx.fillRect(x, y, cell - 1, cell - 1);
 }
}
function loomPixelBaseColor(index, total){
 const ratio = total > 1 ? index / (total - 1) : 0;
 return hslToRgb(210 + ratio * 240, 0.78, 0.56);
}
function loomPixelVariant(base, value){
 const max = Math.max(base.r, base.g, base.b, 1);
 const min = Math.min(base.r, base.g, base.b);
 const delta = max - min;
 let hue = 0;
 if(delta){
  if(max === base.r) hue = 60 * (((base.g - base.b) / delta) % 6);
  else if(max === base.g) hue = 60 * (((base.b - base.r) / delta) + 2);
  else hue = 60 * (((base.r - base.g) / delta) + 4);
 }
 const lightness = (max + min) / 510;
 const sat = delta ? delta / (255 * (1 - Math.abs(2 * lightness - 1))) : 0;
 const shifts = [
  {h:-26,s:0.72,l:0.22},
  {h:-8,s:0.82,l:0.38},
  {h:10,s:0.88,l:0.56},
  {h:26,s:0.94,l:0.74}
 ];
 const shift = shifts[value & 3];
 return hslToRgb(hue + shift.h, sat * 0.55 + shift.s * 0.45, shift.l);
}
function drawLoomPixel(payload){
 const frame = buildLoomPixelFrame(payload);
 const canvas = $("loom-pixel");
 const ctx = canvas.getContext("2d");
 const cols = parsePositiveInt($("grid-width").value, 32);
 const cell = LOOM_PIXEL_CODEC.cell;
 const pad = LOOM_PIXEL_CODEC.pad;
 const membrane = LOOM_PIXEL_CODEC.membrane;
 const rows = Math.max(1, Math.ceil(frame.bytes.length / cols));
 canvas.width = cols * cell + pad * 2;
 canvas.height = rows * cell + pad * 2;
 ctx.fillStyle = "#02070b";
 ctx.fillRect(0, 0, canvas.width, canvas.height);
 for(let i=0;i<frame.bytes.length;i++){
  const x = pad + (i % cols) * cell;
  const y = pad + Math.floor(i / cols) * cell;
  const base = loomPixelBaseColor(i, frame.bytes.length);
  const b = frame.bytes[i];
  ctx.fillStyle = rgbCss(base);
  ctx.fillRect(x, y, cell, cell);
  const inner = cell - membrane * 2;
  const half = inner / 2;
  const quads = [(b >> 6) & 3, (b >> 4) & 3, (b >> 2) & 3, b & 3];
  for(let q=0;q<4;q++){
   const qx = x + membrane + (q % 2) * half;
   const qy = y + membrane + Math.floor(q / 2) * half;
   ctx.fillStyle = rgbCss(loomPixelVariant(base, quads[q]));
   ctx.fillRect(Math.round(qx), Math.round(qy), Math.ceil(half), Math.ceil(half));
  }
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  ctx.fillRect(x, y, cell, 1);
 }
 lastLoomPixelFrame = frame;
 $("loom-pixel-out").textContent =
  "frame magic: LPX1" +
  "\nframed bytes: " + frame.bytes.length +
  "\npayload bytes: " + frame.byteLength +
  "\nnormalized chars: " + frame.charLength +
  "\nchecksum: " + frame.checksum +
  "\ncell: " + cell +
  "\npad: " + pad +
  "\nmembrane: " + membrane +
  "\nquadrants: 4 x 2-bit";
}
function timestampUnits(text, styleKey){
 const style = OFFICIAL_CODEC_SPEC.style_presets[styleKey] || OFFICIAL_CODEC_SPEC.style_presets.opera;
 const scale = style.freqs || OFFICIAL_CODEC_SPEC.style_presets.opera.freqs;
 const units = [];
 const alphabet = OFFICIAL_CODEC_SPEC.alphabet;
 const pmap = OFFICIAL_CODEC_SPEC.punctuation_map;
 const s = normalizeText(text);
 for(let i=0;i<s.length;i++){
  const ch = s[i];
  const idx = alphabet.indexOf(ch);
  if(idx >= 0){
   units.push({type:"note", freq:scale[Math.floor(idx / scale.length)], mark:ch, digit:0});
   units.push({type:"note", freq:scale[idx % scale.length], mark:ch, digit:1});
  }else if(ch === " "){
   units.push({type:"rest", mul:OFFICIAL_CODEC_SPEC.timing.space_rest_multiplier, mark:"space"});
  }else if(Object.prototype.hasOwnProperty.call(pmap, ch)){
   units.push({type:"rest", mul:OFFICIAL_CODEC_SPEC.timing.punct_rest_multiplier, mark:"punct"});
   units.push({type:"note", freq:scale[pmap[ch]], mark:ch, digit:"punct"});
  }
 }
 return units;
}
function renderAudioSamples(payload, styleKey){
 const style = OFFICIAL_CODEC_SPEC.style_presets[styleKey] || OFFICIAL_CODEC_SPEC.style_presets.podcast;
 const units = timestampUnits(payload.normalized, styleKey);
 const sr = 22050;
 const baseCell = Math.max(0.045, (style.dur || 0.135) * OFFICIAL_CODEC_SPEC.timing.cell_ratio);
 const step = baseCell * 0.92;
 let totalSeconds = 0;
 for(let i=0;i<units.length;i++){
  totalSeconds += units[i].type === "rest" ? baseCell * (units[i].mul || 1) : step;
 }
 totalSeconds += 0.12;
 const sampleCount = Math.max(1, Math.ceil(totalSeconds * sr));
 const pcm = new Float32Array(sampleCount);
 let t = 0;
 for(let i=0;i<units.length;i++){
  const unit = units[i];
  if(unit.type === "rest"){
   t += baseCell * (unit.mul || 1);
   continue;
  }
  const freq = unit.freq || 220;
  const start = Math.floor(t * sr);
  const dur = step;
  const end = Math.min(sampleCount, Math.floor((t + dur) * sr));
  for(let j=start;j<end;j++){
   const rel = (j - start) / Math.max(1, end - start);
   const env = Math.sin(Math.PI * rel);
   const time = j / sr;
   const wobble = 1 + 0.003 * Math.sin(2 * Math.PI * 5 * time);
   pcm[j] += Math.sin(2 * Math.PI * freq * wobble * time) * env * 0.22;
  }
  t += step;
 }
 return {sampleRate:sr, pcm:pcm, units:units, seconds:totalSeconds};
}
function pcmToWavBlob(pcm, sampleRate){
 const bytesPerSample = 2;
 const blockAlign = bytesPerSample;
 const dataSize = pcm.length * bytesPerSample;
 const buffer = new ArrayBuffer(44 + dataSize);
 const view = new DataView(buffer);
 let off = 0;
 function writeStr(s){ for(let i=0;i<s.length;i++) view.setUint8(off++, s.charCodeAt(i)); }
 function writeU16(n){ view.setUint16(off, n, true); off += 2; }
 function writeU32(n){ view.setUint32(off, n, true); off += 4; }
 writeStr("RIFF");
 writeU32(36 + dataSize);
 writeStr("WAVE");
 writeStr("fmt ");
 writeU32(16);
 writeU16(1);
 writeU16(1);
 writeU32(sampleRate);
 writeU32(sampleRate * blockAlign);
 writeU16(blockAlign);
 writeU16(16);
 writeStr("data");
 writeU32(dataSize);
 for(let i=0;i<pcm.length;i++){
  const sample = clamp(pcm[i], -1, 1);
  view.setInt16(off, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  off += 2;
 }
 return new Blob([buffer], {type:"audio/wav"});
}
function stopAudio(){
 for(let i=0;i<activeOscillators.length;i++){
  try{ activeOscillators[i].stop(); }catch(err){}
 }
 for(let i=0;i<activeNodes.length;i++){
  try{ activeNodes[i].disconnect(); }catch(err){}
 }
 activeOscillators = [];
 activeNodes = [];
 $("audio-status").textContent = "audio idle";
}
async function playAudio(payload){
 stopAudio();
 if(!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
 if(audioCtx.state === "suspended") await audioCtx.resume();
 const styleKey = $("audio-style").value;
 const render = renderAudioSamples(payload, styleKey);
 const style = OFFICIAL_CODEC_SPEC.style_presets[styleKey] || OFFICIAL_CODEC_SPEC.style_presets.podcast;
 const baseCell = Math.max(0.045, (style.dur || 0.135) * OFFICIAL_CODEC_SPEC.timing.cell_ratio);
 const step = baseCell * 0.92;
 let t = audioCtx.currentTime + 0.04;
 for(let i=0;i<render.units.length;i++){
  const unit = render.units[i];
  if(unit.type === "rest"){
   t += baseCell * (unit.mul || 1);
   continue;
  }
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(unit.freq || 220, t);
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.linearRampToValueAtTime(0.16, t + step * 0.12);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + step);
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  osc.start(t);
  osc.stop(t + step);
  activeOscillators.push(osc);
  activeNodes.push(gain);
  t += step;
 }
 $("audio-status").textContent = "playing " + render.units.length + " units";
 setTimeout(function(){
  $("audio-status").textContent = "audio finished";
 }, Math.ceil(render.seconds * 1000));
}
function downloadBlob(blob, name){
 const url = URL.createObjectURL(blob);
 const a = document.createElement("a");
 a.href = url;
 a.download = name;
 document.body.appendChild(a);
 a.click();
 document.body.removeChild(a);
 setTimeout(() => URL.revokeObjectURL(url), 500);
}
function packetAnswerText(packet){
 if(!packet) return "";
 const answerTape = Array.isArray(packet.answer_tape) ? packet.answer_tape : [];
 if(answerTape.length && answerTape[0] && answerTape[0].object) return String(answerTape[0].object);
 if(packet.meta && packet.meta.meaning_text) return String(packet.meta.meaning_text);
 return "";
}
function renderPayload(payload){
 lastPayload = payload;
 $("payload-summary").textContent =
  "normalized text: " + (payload.normalized || "(empty)") +
  "\nchars: " + payload.ids.length +
  "\nbytes: " + payload.bytes.length +
  "\nchecksum: " + payload.checksum +
  "\nunique symbols: " + payload.symbolSet.length;
 $("payload-audit").textContent =
  "symbol alphabet:\n" + SOURCE_SYMBOLS +
  "\n\nsymbol ids:\n" + payload.ids.join(" ");
 $("varint-out").textContent = bytesToHex(payload.bytes);
 $("glyph-out").textContent = payload.glyphVisual + "\n\nbase glyph tape:\n" + payload.glyphBase;
 $("path-out").textContent = payload.pathTape;
 lastSvgMarkup = buildSvgScatter(payload);
 $("svg-preview").innerHTML = lastSvgMarkup;
 drawMosaic(payload);
 drawLoomPixel(payload);
 const audioRender = renderAudioSamples(payload, $("audio-style").value);
 $("audio-out").textContent =
  "style: " + $("audio-style").value +
  "\nunits: " + audioRender.units.length +
  "\nduration s: " + audioRender.seconds.toFixed(3) +
  "\npreview:\n" + audioRender.units.slice(0, 28).map(function(unit){
   if(unit.type === "rest") return "[rest x" + Number(unit.mul || 1).toFixed(2) + "]";
   return "[" + unit.mark + " " + Number(unit.freq || 0).toFixed(2) + "Hz]";
  }).join(" ");
}
function generate(){
 const text = $("source-text").value;
 renderPayload(buildPayload(text));
}
async function loadPacketFile(file){
 const text = await file.text();
 const packet = JSON.parse(text);
 loadedPacket = packet;
 $("packet-status").textContent = "packet loaded";
 $("source-text").value = packetAnswerText(packet);
}

$("load-sample").onclick = function(){
 $("source-text").value = SAMPLE_TEXT;
 generate();
};
$("generate").onclick = generate;
$("use-packet-answer").onclick = function(){
 $("source-text").value = packetAnswerText(loadedPacket);
 generate();
};
$("packet-file").addEventListener("change", async function(evt){
 const file = evt.target.files && evt.target.files[0];
 if(!file) return;
 try{
  await loadPacketFile(file);
 }catch(err){
  loadedPacket = null;
  $("packet-status").textContent = "packet load failed";
  $("payload-summary").textContent = "could not parse packet: " + ((err && err.message) || err);
 }
});
$("download-svg").onclick = function(){
 if(!lastSvgMarkup) return;
 downloadBlob(new Blob([lastSvgMarkup], {type:"image/svg+xml"}), "booth-v2-scatter.svg");
};
$("download-png").onclick = function(){
 const canvas = $("mosaic");
 canvas.toBlob(function(blob){
  if(blob) downloadBlob(blob, "booth-v2-mosaic.png");
 }, "image/png");
};
$("download-loom-png").onclick = function(){
 const canvas = $("loom-pixel");
 canvas.toBlob(function(blob){
  if(blob) downloadBlob(blob, "booth-v2-loom-pixel.png");
 }, "image/png");
};
$("play-audio").onclick = function(){
 if(lastPayload) playAudio(lastPayload);
};
$("stop-audio").onclick = stopAudio;
$("download-wav").onclick = function(){
 if(!lastPayload) return;
 if(activeWavUrl){
  URL.revokeObjectURL(activeWavUrl);
  activeWavUrl = "";
 }
 const render = renderAudioSamples(lastPayload, $("audio-style").value);
 const blob = pcmToWavBlob(render.pcm, render.sampleRate);
 downloadBlob(blob, "booth-v2-audio.wav");
};
$("audio-style").addEventListener("change", function(){
 if(lastPayload) renderPayload(lastPayload);
});
$("grid-width").addEventListener("change", function(){
 if(lastPayload) renderPayload(lastPayload);
});

$("source-text").value = SAMPLE_TEXT;
generate();
