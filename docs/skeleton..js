#!/usr/bin/env node
"use strict";

const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { URL, URLSearchParams } = require("url");

const CONFIG = {
port: Number(process.env.LOOM_HARNESS_PORT || 8787),
boothFile: process.env.LOOM_BOOTH_FILE || "D:\openclaw\opencloset\docs\booth-presets-query-faders-fixed.html",
maxSearchRounds: Number(process.env.LOOM_MAX_SEARCH_ROUNDS || 6),
maxResultsPerSearch: Number(process.env.LOOM_DDG_RESULTS || 5),
totalConfidenceThreshold: Number(process.env.LOOM_TOTAL_CONFIDENCE || 0.74),
wordConfidenceThreshold: Number(process.env.LOOM_WORD_CONFIDENCE || 0.62),
relationConfidenceThreshold: Number(process.env.LOOM_RELATION_CONFIDENCE || 0.58),
meaningConfidenceThreshold: Number(process.env.LOOM_MEANING_CONFIDENCE || 0.62),
evidenceConfidenceThreshold: Number(process.env.LOOM_EVIDENCE_CONFIDENCE || 0.56),
fetchTimeoutMs: Number(process.env.LOOM_FETCH_TIMEOUT_MS || 9000),
userAgent: "loom-query-harness/0.1 deterministic-search-tape"
};

const FILLERS = new Set([
"the","a","an","of","to","for","is","are","was","were","did","does","do",
"can","could","would","should","and","or","if","it","this","that","today",
"now","me","my","we","our","you","your","in","on","at","by","with","from",
"as","be","been","being","into","about","what","who","when","where","why",
"how","which","anything","something","please","tell","ask"
]);

const TRANSFORM_HINTS = [
["why", "causal"],
["because", "causal"],
["after", "sequence"],
["before", "sequence"],
["next", "sequence"],
["then", "sequence"],
["when", "time"],
["lap", "time"],
["tick", "time"],
["count", "count"],
["many", "count"],
["number", "count"],
["most", "rank"],
["highest", "rank"],
["biggest", "rank"],
["top", "rank"],
["leader", "rank"],
["best", "rank"],
["worst", "rank"],
["good", "evaluation"],
["bad", "evaluation"],
["important", "evaluation"],
["noteworthy", "evaluation"],
["recap", "summary"],
["summary", "summary"],
["overview", "summary"],
["happened", "summary"]
];

function clamp01(n) {
n = Number(n);
if (!Number.isFinite(n)) return 0;
return Math.max(0, Math.min(1, n));
}

function lower(text) {
return String(text || "").toLowerCase();
}

function decodeHtml(text) {
return String(text || "")
.replace(/&/g, "&")
.replace(/"/g, """)
.replace(/'/g, "'")
.replace(/'/g, "'")
.replace(/</g, "<")
.replace(/>/g, ">")
.replace(/ /g, " ")
.replace(/&#(\d+);/g, (*, n) => String.fromCharCode(Number(n)))
.replace(/&#x([0-9a-f]+);/gi, (*, n) => String.fromCharCode(parseInt(n, 16)));
}

function stripTags(text) {
return decodeHtml(String(text || "").replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
}

function splitAlphaNum(text) {
return lower(text).split(/[^a-z0-9]+/).filter(Boolean);
}

function unique(tokens) {
const seen = new Set();
const out = [];
for (const token of tokens || []) {
const t = lower(token).trim();
if (!t || seen.has(t)) continue;
seen.add(t);
out.push(t);
}
return out;
}

function questionTokens(query) {
return unique(splitAlphaNum(query).filter(t => !FILLERS.has(t)));
}

function charTrigrams(token) {
const text = lower(token);
if (text.length < 3) return text ? [text] : [];
const out = [];
for (let i = 0; i <= text.length - 3; i++) out.push(text.slice(i, i + 3));
return out;
}

function tokenAffinity(left, right) {
const a = lower(left);
const b = lower(right);
if (!a || !b) return 0;
if (a === b) return 1;
if (a.includes(b) || b.includes(a)) return 0.92;

let prefix = 0;
while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
const prefixScore = prefix >= 4 ? Math.min(0.88, 0.45 + (prefix / Math.max(a.length, b.length)) * 0.4) : 0;

const ag = charTrigrams(a);
const bg = charTrigrams(b);
const union = new Set([...ag, ...bg]);
let intersection = 0;
const bset = new Set(bg);
for (const g of new Set(ag)) if (bset.has(g)) intersection++;

const trigramScore = union.size ? intersection / union.size : 0;
return Math.max(prefixScore, trigramScore >= 0.34 ? trigramScore : 0);
}

function bestTokenAffinity(token, candidates) {
let best = 0;
let bestCandidate = "";
for (const c of candidates || []) {
const score = tokenAffinity(token, c);
if (score > best) {
best = score;
bestCandidate = c;
}
}
return { score: clamp01(best), match: bestCandidate };
}

function normalizeRow(row, i, sourceKind = "source") {
if (typeof row === "string") {
return {
actor: "line",
action: "say",
object: row,
lap: i + 1,
priority: 0.5,
valence: "calm",
source_kind: sourceKind
};
}

row = row && typeof row === "object" ? row : {};
return {
actor: String(row.actor || row.source || row.title || "tape"),
action: String(row.action || row.kind || "emit"),
object: String(row.object || row.body || row.text || row.snippet || row.title || ""),
lap: Number(row.lap ?? row.rank ?? i + 1),
priority: clamp01(row.priority ?? row.score ?? 0.5),
valence: String(row.valence || "calm"),
source: String(row.source || row.url || row.href || ""),
source_domain: String(row.source_domain || domainOf(row.source || row.url || row.href || "")),
headline: String(row.headline || row.title || ""),
event_id: String(row.event_id || ""),
source_kind: String(row.source_kind || sourceKind)
};
}

function normalizeTape(tape, sourceKind = "source") {
if (!Array.isArray(tape)) return [];
return tape.map((row, i) => normalizeRow(row, i, sourceKind));
}

function rowText(row) {
return [
row.actor,
row.action,
row.object,
row.source,
row.source_domain,
row.headline,
row.event_id
].filter(Boolean).join(" ");
}

function eventTokens(row) {
return unique(splitAlphaNum(rowText(row)));
}

function tapeVocabulary(rows) {
const tokens = [];
for (const row of rows || []) tokens.push(...eventTokens(row));
return unique(tokens);
}

function inferTransform(query) {
const qTokens = questionTokens(query);
const q = lower(query);
for (const [hint, transform] of TRANSFORM_HINTS) {
if (qTokens.includes(hint) || q.includes(hint)) return transform;
}
return qTokens.length ? "describe" : "summary";
}

function scoreWords(query, rows) {
const tokens = questionTokens(query);
const candidates = tapeVocabulary(rows);
return tokens.map(token => {
const best = bestTokenAffinity(token, candidates);
return {
type: "word",
item: token,
confidence: best.score,
gap: 1 - best.score,
match: best.match,
reason: best.score >= CONFIG.wordConfidenceThreshold ? "covered by tape vocabulary" : "foreign or weakly covered by tape vocabulary"
};
});
}

function scoreRelation(query, rows) {
const tokens = questionTokens(query);
if (!tokens.length) return 0;
const wordScores = scoreWords(query, rows).map(w => w.confidence);
const avg = wordScores.reduce((a, b) => a + b, 0) / Math.max(1, wordScores.length);
const directHits = wordScores.filter(s => s >= 0.92).length / Math.max(1, wordScores.length);
const rowHits = rows.filter(row => {
const rt = eventTokens(row);
return tokens.some(t => bestTokenAffinity(t, rt).score >= 0.72);
}).length;
const rowCoverage = rows.length ? Math.min(1, rowHits / Math.min(rows.length, Math.max(3, tokens.length))) : 0;
return clamp01(avg * 0.50 + directHits * 0.20 + rowCoverage * 0.30);
}

function scoreMeaning(query, rows) {
const relation = scoreRelation(query, rows);
const transform = inferTransform(query);
const tokens = questionTokens(query);
const hasTransformCue = transform !== "describe" && transform !== "summary";
const hasAnchors = scoreWords(query, rows).some(w => w.confidence >= 0.82);
const tokenMass = Math.min(1, tokens.length / 4);
return clamp01(relation * 0.50 + (hasTransformCue ? 0.22 : 0.10) + (hasAnchors ? 0.18 : 0.05) + tokenMass * 0.10);
}

function scoreEvidence(query, rows) {
const tokens = questionTokens(query);
if (!rows.length) return [];
return rows.map((row, idx) => {
const rt = eventTokens(row);
const lexical = tokens.length
? tokens.reduce((sum, t) => sum + bestTokenAffinity(t, rt).score, 0) / tokens.length
: 0.2;
const priority = clamp01(row.priority ?? 0.5);
const sourceBonus = row.source_kind === "search" ? 0.08 : 0;
const confidence = clamp01(lexical * 0.72 + priority * 0.20 + sourceBonus);
return {
type: "evidence",
item: `lap:${row.lap}`,
confidence,
gap: 1 - confidence,
rowIndex: idx,
row,
reason: confidence >= CONFIG.evidenceConfidenceThreshold ? "evidence row matches query shape" : "evidence row weakly supports query shape"
};
}).sort((a, b) => b.confidence - a.confidence);
}

function totalConfidence(conf) {
const wordAvg = conf.words.length
? conf.words.reduce((a, w) => a + w.confidence, 0) / conf.words.length
: 0.35;
const topEvidence = conf.evidence.slice(0, 4);
const evidenceAvg = topEvidence.length
? topEvidence.reduce((a, e) => a + e.confidence, 0) / topEvidence.length
: 0;
return clamp01(
wordAvg * 0.28 +
conf.relation * 0.24 +
conf.meaning * 0.24 +
evidenceAvg * 0.24
);
}

function computeConfidence(query, rows) {
const words = scoreWords(query, rows);
const relation = scoreRelation(query, rows);
const meaning = scoreMeaning(query, rows);
const evidence = scoreEvidence(query, rows);
const conf = { words, relation, meaning, evidence };
conf.total = totalConfidence(conf);
return conf;
}

function lowConfidenceItems(query, rows, conf, searched) {
const items = [];

for (const w of conf.words) {
if (w.confidence < CONFIG.wordConfidenceThreshold && !searched.has(`word:${w.item}`)) {
items.push({
type: "word",
item: w.item,
confidence: w.confidence,
gap: 1 - w.confidence,
searchQuery: w.item,
reason: "low word confidence"
});
}
}

if (conf.relation < CONFIG.relationConfidenceThreshold && !searched.has("relation:" + query)) {
items.push({
type: "relation",
item: query,
confidence: conf.relation,
gap: 1 - conf.relation,
searchQuery: query,
reason: "low query-to-tape relation confidence"
});
}

if (conf.meaning < CONFIG.meaningConfidenceThreshold && !searched.has("meaning:" + inferTransform(query) + ":" + query)) {
items.push({
type: "meaning",
item: inferTransform(query),
confidence: conf.meaning,
gap: 1 - conf.meaning,
searchQuery: `${inferTransform(query)} meaning ${query}`,
reason: "low meaning confidence"
});
}

const weakEvidence = conf.evidence.filter(e => e.confidence < CONFIG.evidenceConfidenceThreshold).slice(0, 2);
for (const e of weakEvidence) {
const row = e.row || {};
const q = [row.headline, row.object, row.actor, row.action].filter(Boolean).join(" ").slice(0, 180);
if (q && !searched.has(`evidence:${q}`)) {
items.push({
type: "evidence",
item: q,
confidence: e.confidence,
gap: 1 - e.confidence,
searchQuery: q,
reason: "low evidence confidence"
});
}
}

items.sort((a, b) => {
if (b.gap !== a.gap) return b.gap - a.gap;
return a.type.localeCompare(b.type);
});

return items;
}

function domainOf(raw) {
try {
const u = new URL(raw);
return u.hostname.replace(/^[www./](http://www./), "");
} catch {
const m = String(raw || "").match(/\b([a-z0-9-]+(?:.[a-z0-9-]+)+)\b/i);
return m ? m[1].toLowerCase().replace(/^[www./](http://www./), "") : "";
}
}

function httpGet(url, opts = {}) {
return new Promise((resolve, reject) => {
const u = new URL(url);
const mod = u.protocol === "http:" ? http : https;
const req = mod.request({
method: "GET",
protocol: u.protocol,
hostname: u.hostname,
path: u.pathname + u.search,
headers: {
"user-agent": CONFIG.userAgent,
"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
"accept-language": "en-US,en;q=0.9"
},
timeout: opts.timeoutMs || CONFIG.fetchTimeoutMs
}, res => {
let body = "";
res.setEncoding("utf8");
res.on("data", chunk => body += chunk);
res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
});

```
req.on("timeout", () => {
  req.destroy(new Error("fetch timeout"));
});
req.on("error", reject);
req.end();
```

});
}

function parseDuckDuckGo(html) {
const results = [];
const blockRe = /<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>[\s\S]*?(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>)([\s\S]*?)(?:</a>|</div>)/gi;
let m;

while ((m = blockRe.exec(html)) && results.length < CONFIG.maxResultsPerSearch) {
let href = decodeHtml(m[1]);
const title = stripTags(m[2]);
const snippet = stripTags(m[3]);

```
try {
  const maybe = new URL(href, "https://duckduckgo.com");
  const uddg = maybe.searchParams.get("uddg");
  if (uddg) href = decodeURIComponent(uddg);
} catch {}

if (!title && !snippet) continue;
results.push({
  title,
  snippet,
  url: href,
  domain: domainOf(href)
});
```

}

if (!results.length) {
const looseRe = /<a[^>]+href="([^"]+)"[^>]*>([\s\S]{5,300}?)</a>/gi;
while ((m = looseRe.exec(html)) && results.length < CONFIG.maxResultsPerSearch) {
const title = stripTags(m[2]);
let href = decodeHtml(m[1]);
if (!title || /duckduckgo|feedback|settings/i.test(title)) continue;
try {
const maybe = new URL(href, "[https://duckduckgo.com](https://duckduckgo.com)");
const uddg = maybe.searchParams.get("uddg");
if (uddg) href = decodeURIComponent(uddg);
} catch {}
results.push({ title, snippet: "", url: href, domain: domainOf(href) });
}
}

return results;
}

async function searchDuckDuckGo(query) {
const params = new URLSearchParams({ q: query });
const url = `https://html.duckduckgo.com/html/?${params.toString()}`;
const res = await httpGet(url);
if (res.status < 200 || res.status >= 400) {
throw new Error(`DuckDuckGo returned HTTP ${res.status}`);
}
return parseDuckDuckGo(res.body);
}

function searchResultsToTape(results, searchQuery, round, itemType) {
return (results || []).map((r, i) => ({
actor: r.domain || "duckduckgo",
action: "publish",
object: [r.title, r.snippet].filter(Boolean).join(" - "),
lap: round * 100 + i + 1,
priority: clamp01(0.78 - i * 0.08),
valence: "calm",
source: r.url,
source_domain: r.domain,
headline: r.title,
event_id: `search:${itemType}:${searchQuery}:${i}`,
search_query: searchQuery,
source_kind: "search"
}));
}

function selectEvidence(query, rows, n = 5) {
return scoreEvidence(query, rows).slice(0, n).map(e => e.row);
}

function summarizeRows(rows) {
return rows.map(row => {
const actor = row.actor || row.source_domain || "source";
const action = row.action || "says";
const object = row.object || row.headline || "";
const src = row.source_domain ? ` (${row.source_domain})` : "";
return `${actor} ${action} ${object}${src}`.replace(/\s+/g, " ").trim();
});
}

function deterministicAnswer(query, rows, conf) {
const transform = inferTransform(query);
const evidence = selectEvidence(query, rows, 5);
const lines = summarizeRows(evidence);
const relation = conf.relation >= CONFIG.relationConfidenceThreshold ? "grounded" : "provisional";
const confidenceLabel =
conf.total >= 0.82 ? "high" :
conf.total >= CONFIG.totalConfidenceThreshold ? "sufficient" :
conf.total >= 0.55 ? "partial" :
"low";

if (!evidence.length) {
return {
answer: "I cannot justify an answer from the current source tape or search tape.",
confidence: confidenceLabel,
transform,
evidence: []
};
}

let lead;
if (transform === "count") {
lead = `Count-shaped answer: the strongest source tape evidence has ${evidence.length} relevant rows.`;
} else if (transform === "rank") {
lead = `Rank-shaped answer: the highest-signal thread points to ${evidence[0].actor || evidence[0].source_domain || "the top source"}.`;
} else if (transform === "sequence") {
const ordered = evidence.slice().sort((a, b) => Number(a.lap || 0) - Number(b.lap || 0));
lead = `Sequence-shaped answer: the relevant thread runs ${summarizeRows(ordered).join(" -> ")}.`;
} else if (transform === "causal") {
lead = `Causal-shaped answer: I can only cite adjacent/source-linked evidence, not prove hidden cause.`;
} else if (transform === "evaluation") {
lead = `Evaluation-shaped answer: the supported judgment is bounded by the cited source rows.`;
} else {
lead = `Summary-shaped answer: ${lines[0]}.`;
}

return {
answer: `${lead}\n\nEvidence:\n${lines.map((l, i) => `${i + 1}. ${l}`).join("\n")}`,
confidence: confidenceLabel,
transform,
evidence
};
}

async function orchestrate(query, sourceTape, options = {}) {
const sourceRows = normalizeTape(sourceTape, "source");
let rows = sourceRows.slice();
const searched = new Set();
const searchTapes = [];
const log = [];

let conf = computeConfidence(query, rows);

for (let round = 1; round <= (options.maxSearchRounds || CONFIG.maxSearchRounds); round++) {
if (conf.total >= (options.totalConfidenceThreshold || CONFIG.totalConfidenceThreshold)) break;

```
const low = lowConfidenceItems(query, rows, conf, searched);
if (!low.length) break;

const item = low[0];
const key = `${item.type}:${item.item}`;
searched.add(key);

log.push({
  round,
  status: "searching",
  item_type: item.type,
  item: item.item,
  search_query: item.searchQuery,
  confidence_before: item.confidence,
  total_before: conf.total
});

let results = [];
let tape = [];
try {
  results = await searchDuckDuckGo(item.searchQuery);
  tape = searchResultsToTape(results, item.searchQuery, round, item.type);
  rows = rows.concat(tape);
  searchTapes.push({
    round,
    item,
    query: item.searchQuery,
    rows: tape
  });
} catch (err) {
  log.push({
    round,
    status: "search_failed",
    item_type: item.type,
    item: item.item,
    search_query: item.searchQuery,
    error: err.message || String(err)
  });
}

const nextConf = computeConfidence(query, rows);
log.push({
  round,
  status: "rescored",
  item_type: item.type,
  item: item.item,
  search_rows_added: tape.length,
  total_after: nextConf.total,
  relation_after: nextConf.relation,
  meaning_after: nextConf.meaning
});

if (nextConf.total <= conf.total + 0.005 && item.type !== "relation") {
  searched.add(`stalled:${item.type}:${item.item}`);
}

conf = nextConf;
```

}

const final = deterministicAnswer(query, rows, conf);

return {
query,
answer: final.answer,
answer_confidence: final.confidence,
transform: final.transform,
confidence: {
total: conf.total,
relation: conf.relation,
meaning: conf.meaning,
words: conf.words,
evidence: conf.evidence.slice(0, 8).map(e => ({
item: e.item,
confidence: e.confidence,
gap: e.gap,
reason: e.reason,
row: e.row
}))
},
thresholds: {
total: CONFIG.totalConfidenceThreshold,
word: CONFIG.wordConfidenceThreshold,
relation: CONFIG.relationConfidenceThreshold,
meaning: CONFIG.meaningConfidenceThreshold,
evidence: CONFIG.evidenceConfidenceThreshold
},
search_tapes: searchTapes,
source_rows: sourceRows.length,
total_rows: rows.length,
log
};
}

function readBody(req) {
return new Promise((resolve, reject) => {
let body = "";
req.on("data", chunk => {
body += chunk;
if (body.length > 5_000_000) {
req.destroy();
reject(new Error("request body too large"));
}
});
req.on("end", () => resolve(body));
req.on("error", reject);
});
}

function sendJson(res, status, data) {
const body = JSON.stringify(data, null, 2);
res.writeHead(status, {
"content-type": "application/json; charset=utf-8",
"access-control-allow-origin": "*",
"access-control-allow-methods": "GET,POST,OPTIONS",
"access-control-allow-headers": "content-type"
});
res.end(body);
}

function sendText(res, status, text, contentType = "text/plain; charset=utf-8") {
res.writeHead(status, {
"content-type": contentType,
"access-control-allow-origin": "*"
});
res.end(text);
}

function clientSnippet() {
return `

<script>
(function(){
  if (window.LoomSearchHarness) return;
  window.LoomSearchHarness = {
    endpoint: "http://localhost:${CONFIG.port}/ask",
    ask: async function(query, sourceTape, options){
      const res = await fetch(this.endpoint, {
        method: "POST",
        headers: {"content-type":"application/json"},
        body: JSON.stringify({query:query, sourceTape:sourceTape, options:options||{}})
      });
      if (!res.ok) throw new Error("harness HTTP " + res.status);
      return await res.json();
    },
    installIntoBooth: function(){
      const input = document.getElementById("query-input");
      const button = document.getElementById("query-send");
      const out = document.getElementById("query-result");
      if (!input || !button || !out) return false;

      function selectedTapeRows(){
        if (typeof getSelectedTapeRows === "function") return getSelectedTapeRows();
        if (typeof currentRows === "function") return currentRows();
        if (window.TAPES) {
          const sel = document.getElementById("tape");
          const names = sel ? Array.from(sel.selectedOptions).map(o=>o.value) : Object.keys(window.TAPES).slice(0,1);
          return names.flatMap(name => window.TAPES[name] || []);
        }
        const mine = document.getElementById("mine");
        if (mine && mine.value.trim()) {
          return mine.value.trim().split(/\\n+/).map((line, i)=>({actor:"line",action:"say",object:line,lap:i+1,priority:0.5}));
        }
        return [];
      }

      button.addEventListener("click", async function(ev){
        ev.preventDefault();
        const q = input.value.trim();
        if (!q) return;
        out.textContent = "searching...";
        try {
          const ans = await window.LoomSearchHarness.ask(q, selectedTapeRows());
          out.textContent = ans.answer + "\\n\\nconfidence=" + ans.confidence.total.toFixed(3) + " relation=" + ans.confidence.relation.toFixed(3) + " meaning=" + ans.confidence.meaning.toFixed(3);
        } catch (err) {
          out.textContent = "harness error: " + (err.message || err);
        }
      }, true);

      return true;
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function(){ window.LoomSearchHarness.installIntoBooth(); });
  } else {
    window.LoomSearchHarness.installIntoBooth();
  }
})();
</script>`.trim();

}

async function handle(req, res) {
if (req.method === "OPTIONS") {
res.writeHead(204, {
"access-control-allow-origin": "*",
"access-control-allow-methods": "GET,POST,OPTIONS",
"access-control-allow-headers": "content-type"
});
res.end();
return;
}

const url = new URL(req.url, `http://${req.headers.host}`);

try {
if (req.method === "GET" && url.pathname === "/health") {
sendJson(res, 200, { ok: true, port: CONFIG.port });
return;
}

```
if (req.method === "GET" && url.pathname === "/client.js") {
  const js = clientSnippet()
    .replace(/^<script>/, "")
    .replace(/<\/script>$/, "");
  sendText(res, 200, js, "application/javascript; charset=utf-8");
  return;
}

if (req.method === "GET" && url.pathname === "/booth") {
  const html = fs.readFileSync(CONFIG.boothFile, "utf8");
  const injected = html.includes("</body>")
    ? html.replace("</body>", `${clientSnippet()}\n</body>`)
    : `${html}\n${clientSnippet()}`;
  sendText(res, 200, injected, "text/html; charset=utf-8");
  return;
}

if (req.method === "POST" && url.pathname === "/ask") {
  const body = await readBody(req);
  const payload = JSON.parse(body || "{}");
  const query = String(payload.query || "").trim();
  if (!query) {
    sendJson(res, 400, { error: "missing query" });
    return;
  }

  const sourceTape = Array.isArray(payload.sourceTape) ? payload.sourceTape : [];
  const result = await orchestrate(query, sourceTape, payload.options || {});
  sendJson(res, 200, result);
  return;
}

if (req.method === "POST" && url.pathname === "/score") {
  const body = await readBody(req);
  const payload = JSON.parse(body || "{}");
  const query = String(payload.query || "").trim();
  const sourceTape = Array.isArray(payload.sourceTape) ? payload.sourceTape : [];
  const rows = normalizeTape(sourceTape, "source");
  sendJson(res, 200, {
    query,
    confidence: computeConfidence(query, rows)
  });
  return;
}

sendJson(res, 404, {
  error: "not found",
  routes: [
    "GET /health",
    "GET /client.js",
    "GET /booth",
    "POST /score {query,sourceTape}",
    "POST /ask {query,sourceTape,options}"
  ]
});
```

} catch (err) {
sendJson(res, 500, {
error: err.message || String(err)
});
}
}

function startServer() {
const server = http.createServer(handle);
server.listen(CONFIG.port, () => {
process.stdout.write(`loom query harness listening on http://localhost:${CONFIG.port}\n`);
process.stdout.write(`booth wrapper: http://localhost:${CONFIG.port}/booth\n`);
});
}

if (require.main === module) {
startServer();
}

module.exports = {
CONFIG,
questionTokens,
tokenAffinity,
bestTokenAffinity,
normalizeTape,
computeConfidence,
lowConfidenceItems,
searchDuckDuckGo,
searchResultsToTape,
orchestrate,
deterministicAnswer,
startServer
};
