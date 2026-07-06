/**
 * harness.js — Confidence-aware query orchestrator for loom/booth.
 *
 * Architecture:
 *   1. Score each query word against source tape → per-word confidence
 *   2. Score query-to-tape relation confidence
 *   3. Find items below threshold
 *   4. Search DuckDuckGo for the lowest-confidence item → parse into search tape
 *   5. Re-score everything (new tape may cover other low-conf items)
 *   6. Repeat until total confidence >= threshold OR max searches reached
 *   7. Feed query + source tape + search tapes → booth → final answer
 *
 * No LLM. No external API keys. Deterministic. Small.
 */

const http = require("http");
const https = require("https");
const { URL } = require("url");

// ─── Config ───────────────────────────────────────────────────────────────
const CONFIG = {
  confidenceThreshold: 0.65,      // minimum total confidence to skip search
  perWordMinConfidence: 0.35,     // individual word floor before it triggers search
  maxSearchRounds: 5,             // safety cap on search iterations
  searchTimeoutMs: 4000,
  boothUrl: "http://127.0.0.1:3000",  // where the booth HTML is served (optional)
  logLevel: "info"                // "debug" | "info" | "quiet"
};

// ─── Fillers (same as booth's QUESTION_MATH_FILLERS) ─────────────────────
const FILLERS = [
  "the","a","an","of","to","for","is","are","was","were",
  "did","does","do","can","could","would","should","and","or",
  "if","it","this","that","today","now","in","on","at","by","with"
];

// ─── Token utilities (mirroring booth functions) ──────────────────────────
function lowerQuery(text) {
  return String(text || "").toLowerCase().replace(/[?!.]+$/, " ").replace(/\s+/g, " ").trim();
}

function splitAlphaNum(text) {
  return lowerQuery(text || "").split(/[^a-z0-9]+/).filter(Boolean);
}

function uniqueLowerTokens(tokens) {
  const out = [], seen = {};
  for (let i = 0; i < (tokens || []).length; i++) {
    const token = String(tokens[i] || "").toLowerCase();
    if (!token || seen[token]) continue;
    seen[token] = true;
    out.push(token);
  }
  return out;
}

/** Tokens that carry semantic weight (not fillers). */
function contentTokens(query) {
  return splitAlphaNum(query).filter(function(t) {
    return FILLERS.indexOf(t) < 0 && t.length > 1;
  });
}

// ─── Char trigram affinity (from booth's tokenAffinity) ──────────────────
function charTrigrams(token) {
  var text = String(token || "").toLowerCase();
  if (text.length < 3) return [text];
  var out = [];
  for (var i = 0; i <= text.length - 3; i++) out.push(text.slice(i, i + 3));
  return out;
}

function tokenAffinity(left, right) {
  var a = String(left || "").toLowerCase();
  var b = String(right || "").toLowerCase();
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.indexOf(b) >= 0 || b.indexOf(a) >= 0) return 0.92;

  // Prefix score
  var prefix = 0;
  while (prefix < a.length && prefix < b.length && a.charAt(prefix) === b.charAt(prefix)) prefix++;
  var prefixScore = prefix >= 4
    ? Math.min(0.88, 0.45 + (prefix / Math.max(a.length, b.length)) * 0.4)
    : 0;

  // Trigram Jaccard
  var ag = charTrigrams(a), bg = charTrigrams(b);
  var seen = {}, both = {}, union = 0, intersect = 0;
  for (var i = 0; i < ag.length; i++) {
    if (!seen[ag[i]]) { seen[ag[i]] = 1; union++; }
    both[ag[i]] = 1;
  }
  for (var i = 0; i < bg.length; i++) {
    if (!seen[bg[i]]) { seen[bg[i]] = 1; union++; }
    if (both[bg[i]]) intersect++;
  }
  var trigramScore = union ? intersect / union : 0;
  return Math.max(prefixScore, trigramScore >= 0.34 ? trigramScore : 0);
}

function bestTokenAffinity(token, candidates) {
  var best = 0;
  for (var i = 0; i < (candidates || []).length; i++) {
    best = Math.max(best, tokenAffinity(token, candidates[i]));
  }
  return best;
}

// ─── Tape vocabulary extraction ───────────────────────────────────────────
/**
 * Extract all vocabulary tokens from a tape (array of event objects).
 * Each event has: actor, action, object, valence, lap, priority, ...
 */
function tapeVocabulary(events) {
  var tokenFreq = {};
  for (var i = 0; i < (events || []).length; i++) {
    var e = events[i];
    var fields = [
      String(e.actor || ""),
      String(e.action || ""),
      String(e.object || ""),
      String(e.headline || ""),
      String(e.source_domain || "")
    ];
    var tokens = uniqueLowerTokens(fields.map(splitAlphaNum).reduce(function(a, b) { return a.concat(b); }, []));
    for (var j = 0; j < tokens.length; j++) {
      tokenFreq[tokens[j]] = (tokenFreq[tokens[j]] || 0) + 1;
    }
  }
  return tokenFreq;
}

function tapeVocabKeys(events) {
  return Object.keys(tapeVocabulary(events));
}

// ─── Confidence scoring ───────────────────────────────────────────────────

/**
 * Score each query word against the source tape vocabulary.
 * Returns array of { word, confidence, status }.
 *   status: "known" (>= threshold), "partial" (between 0.2 and threshold), "unknown" (< 0.2)
 */
function scoreQueryWords(query, sourceTape) {
  var words = contentTokens(query);
  var vocab = tapeVocabKeys(sourceTape);
  var results = [];

  for (var i = 0; i < words.length; i++) {
    var word = words[i];
    var conf = bestTokenAffinity(word, vocab);
    var status = conf >= CONFIG.confidenceThreshold ? "known"
                 : conf >= 0.2 ? "partial" : "unknown";
    results.push({ word: word, confidence: conf, status: status });
  }
  return results;
}

/**
 * Score how well the query as a whole relates to the source tape.
 * Uses overlap ratio of query tokens vs tape tokens.
 */
function scoreRelation(query, sourceTape) {
  var queryTokens = uniqueLowerTokens(contentTokens(query));
  var tapeTokens = tapeVocabKeys(sourceTape);
  if (!queryTokens.length || !tapeTokens.length) return 0;

  var total = 0;
  for (var i = 0; i < queryTokens.length; i++) {
    total += bestTokenAffinity(queryTokens[i], tapeTokens);
  }
  return total / queryTokens.length;
}

/**
 * Aggregate confidence: weighted blend of per-word avg and relation score.
 */
function totalConfidence(wordScores, relationScore) {
  var wordAvg = 0;
  var n = wordScores.length || 1;
  for (var i = 0; i < wordScores.length; i++) wordAvg += wordScores[i].confidence;
  wordAvg = wordAvg / n;
  // 60% word-level, 40% relation-level
  return wordAvg * 0.6 + relationScore * 0.4;
}

// ─── Low-confidence item detection ────────────────────────────────────────
/**
 * Return items below perWordMinConfidence, sorted ascending (lowest first).
 * Includes both individual words and the relation score if it's low.
 */
function findLowConfidenceItems(wordScores, relationScore) {
  var items = [];
  for (var i = 0; i < wordScores.length; i++) {
    if (wordScores[i].confidence < CONFIG.perWordMinConfidence) {
      items.push({
        kind: "word",
        term: wordScores[i].word,
        confidence: wordScores[i].confidence
      });
    }
  }
  if (relationScore < CONFIG.perWordMinConfidence) {
    items.push({
      kind: "relation",
      term: "query-source relation",
      confidence: relationScore
    });
  }
  items.sort(function(a, b) { return a.confidence - b.confidence; });
  return items;
}

// ─── DuckDuckGo search (no API key) ───────────────────────────────────────
/**
 * Fetch DuckDuckGo HTML search results for a query term.
 * Returns a "search tape" — array of event-like objects derived from results.
 */
function searchDuckDuckGo(term) {
  return new Promise(function(resolve, reject) {
    var urlStr = "https://html.duckduckgo.com/html/?q=" + encodeURIComponent(term);

    var timer = setTimeout(function() {
      reject(new Error("search timeout for: " + term));
    }, CONFIG.searchTimeoutMs);

    https.get(urlStr, function(res) {
      clearTimeout(timer);
      if (res.statusCode === 404 || res.statusCode === 403 || res.statusCode >= 400) {
        // DDG blocks some requests; fall back gracefully
        resolve(buildFallbackSearchTape(term));
        return;
      }
      var chunks = [];
      res.on("data", function(chunk) { chunks.push(chunk); });
      res.on("end", function() {
        var html = Buffer.concat(chunks).toString("utf8");
        resolve(parseDDGResults(html, term));
      });
    }).on("error", function(err) {
      clearTimeout(timer);
      log("warn", "DDG request error for '" + term + "': " + err.message);
      resolve(buildFallbackSearchTape(term));
    });
  });
}

/**
 * Parse DDG HTML results into a search tape.
 * Each result becomes an event:
 *   actor="search", action="define", object=term, source_domain=result.domain, headline=result.title
 */
function parseDDGResults(html, term) {
  var tape = [];
  var lap = 1;

  // Extract result blocks: <a class="result__a" href="...">Title</a>
  // and related definitions from .inline__defs
  var resultRegex = /<a class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)<\/a>/g;
  var m;
  while ((m = resultRegex.exec(html)) !== null) {
    var href = m[1];
    var title = decodeHtml(m[2].trim());
    if (!title || title.length < 3) continue;

    var domain = "";
    try { domain = new URL(href).hostname; } catch(e) { /* skip */ }

    tape.push({
      actor: "search",
      action: "find",
      object: title,
      valence: "calm",
      lap: lap++,
      priority: 0.7,
      source_domain: domain,
      headline: title,
      event_id: "ddg|" + domain + "|" + title,
      search_term: term
    });

    if (lap > 20) break; // cap results
  }

  // Extract inline definitions (DDG shows them at top)
  var defRegex = /class="inline__def__term"[^>]*>([^<]*)<\/a>.*?<span[^>]*>([^<]*)</gs;
  var d;
  while ((d = defRegex.exec(html)) !== null) {
    var defTerm = decodeHtml(d[1].trim());
    var defText = decodeHtml(d[2].trim());
    if (defTerm && defText) {
      tape.push({
        actor: "search",
        action: "define",
        object: defTerm,
        valence: "calm",
        lap: lap++,
        priority: 0.9,
        source_domain: "duckduckgo.com",
        headline: defTerm + ": " + defText.substring(0, 120),
        event_id: "ddg|define|" + defTerm,
        search_term: term,
        definition: defText
      });
    }
  }

  log("info", "DDG search for '" + term + "' returned " + tape.length + " results");
  return tape;
}

/** Minimal HTML entity decoder. */
function decodeHtml(s) {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, function(_, n) { return String.fromCharCode(+n); });
}

/** If DDG blocks us, return a minimal tape acknowledging the gap. */
function buildFallbackSearchTape(term) {
  return [{
    actor: "search",
    action: "attempt",
    object: "could not resolve: " + term,
    valence: "alarm",
    lap: 1,
    priority: 0.3,
    source_domain: "",
    headline: "search unavailable for: " + term,
    event_id: "fallback|" + term,
    search_term: term
  }];
}

// ─── Search tape merging & re-scoring ─────────────────────────────────────
/**
 * Merge search tapes into the vocabulary pool and re-score everything.
 * Returns updated wordScores and relationScore.
 */
function recalculateConfidence(query, sourceTape, searchTapes) {
  // Combine all tapes into one vocabulary
  var allEvents = sourceTape.slice();
  for (var i = 0; i < (searchTapes || []).length; i++) {
    allEvents = allEvents.concat(searchTapes[i]);
  }

  var wordScores = scoreQueryWords(query, allEvents);
  var relationScore = scoreRelation(query, allEvents);
  return { wordScores: wordScores, relationScore: relationScore, combinedTape: allEvents };
}

// ─── Main orchestration loop ──────────────────────────────────────────────
/**
 * Process a query against a source tape, searching for low-confidence gaps.
 * Returns { answer, audit } where audit contains full confidence breakdown.
 */
async function processQuery(query, sourceTape, boothCallback) {
  var audit = {
    query: query,
    sourceTapeName: sourceTape.name || "unnamed",
    rounds: 0,
    initialWordScores: null,
    initialRelationScore: 0,
    initialTotalConfidence: 0,
    searchTapes: [],
    finalWordScores: null,
    finalRelationScore: 0,
    finalTotalConfidence: 0,
    lowConfidenceItems: [],
    searched: [],
    searching: false
  };

  // Round 0: score against source tape only
  var wordScores = scoreQueryWords(query, sourceTape);
  var relationScore = scoreRelation(query, sourceTape);
  var totalConf = totalConfidence(wordScores, relationScore);

  audit.initialWordScores = wordScores;
  audit.initialRelationScore = relationScore;
  audit.initialTotalConfidence = totalConf;
  audit.rounds = 0;

  log("info", "Initial confidence: " + totalConf.toFixed(3) +
      " (words avg=" + (wordScores.reduce(function(s,w){return s+w.confidence;},0)/Math.max(1,wordScores.length)).toFixed(3) +
      ", relation=" + relationScore.toFixed(3) + ")");

  var searchTapes = [];
  var searchedTerms = {};

  // Search loop: find and fill low-confidence gaps
  for (var round = 1; round <= CONFIG.maxSearchRounds; round++) {
    // Re-score with all tapes collected so far
    var recalculated = recalculateConfidence(query, sourceTape, searchTapes);
    wordScores = recalculated.wordScores;
    relationScore = recalculated.relationScore;
    totalConf = totalConfidence(wordScores, relationScore);

    // Check if we meet threshold
    if (totalConf >= CONFIG.confidenceThreshold) {
      log("info", "Round " + round + ": confidence " + totalConf.toFixed(3) +
            " >= threshold " + CONFIG.confidenceThreshold + ". Search complete.");
      break;
    }

    // Find lowest-confidence item
    var lowItems = findLowConfidenceItems(wordScores, relationScore);
    if (!lowItems.length) {
      log("info", "Round " + round + ": no low-confidence items found. Stopping search.");
      break;
    }

    var target = lowItems[0]; // lowest first
    var searchTerm = target.term;

    // Skip if already searched (relation items always searched)
    if (target.kind === "word" && searchedTerms[searchTerm]) {
      log("info", "Round " + round + ": already searched '" + searchTerm + "'. Stopping.");
      break;
    }

    searchedTerms[searchTerm] = true;
    audit.searched.push(searchTerm);
    audit.searching = true;

    log("info", "Round " + round + ": searching '" + searchTerm +
          "' (confidence=" + target.confidence.toFixed(3) + ")");

    var searchTape = await searchDuckDuckGo(searchTerm);
    searchTape.name = "search:" + searchTerm;
    searchTapes.push(searchTape);
    audit.searchTapes.push(searchTape);
    audit.rounds = round;

    audit.finalWordScores = wordScores;
    audit.finalRelationScore = relationScore;
    audit.finalTotalConfidence = totalConfidence(recalculated.wordScores, recalculated.relationScore);
  }

  audit.searching = false;

  // Final confidence
  var finalRecalc = recalculateConfidence(query, sourceTape, searchTapes);
  audit.finalWordScores = finalRecalc.wordScores;
  audit.finalRelationScore = finalRecalc.relationScore;
  audit.finalTotalConfidence = totalConfidence(finalRecalc.wordScores, finalRecalc.relationScore);
  audit.lowConfidenceItems = findLowConfidenceItems(finalRecalc.wordScores, finalRecalc.relationScore);

  log("info", "Final confidence: " + audit.finalTotalConfidence.toFixed(3) +
      " after " + audit.rounds + " search round(s), " + audit.searched.length + " term(s)");

  // Call booth for final answer
  var answer = null;
  if (boothCallback) {
    answer = await boothCallback(query, finalRecalc.combinedTape);
  }

  return { answer: answer, audit: audit };
}

// ─── HTTP Server ──────────────────────────────────────────────────────────
function createServer() {
  return http.createServer(function(req, res) {
    var parsedUrl = new URL(req.url, "http://localhost:3001");

    // CORS headers
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    if (req.method === "OPTIONS") { res.writeHead(204); return res.end(); }

    // POST /search — process query with confidence orchestration
    if (req.method === "POST" && parsedUrl.pathname === "/search") {
      var body = "";
      req.on("data", function(chunk) { body += chunk; });
      req.on("end", async function() {
        try {
          var input = JSON.parse(body);
          var query = input.query || "";
          var sourceTape = input.tape || [];
          sourceTape.name = input.tapeName || "provided";

          var result = await processQuery(query, sourceTape, input.boothUrl ? function() {
            // External booth integration (optional)
            return Promise.resolve(null);
          } : null);

          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify(result, null, 2));
        } catch (err) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
      return;
    }

    // GET /score — score query words against tape (no search)
    if (req.method === "POST" && parsedUrl.pathname === "/score") {
      var body2 = "";
      req.on("data", function(chunk) { body2 += chunk; });
      req.on("end", function() {
        try {
          var input = JSON.parse(body2);
          var query = input.query || "";
          var sourceTape = input.tape || [];
          var wordScores = scoreQueryWords(query, sourceTape);
          var relationScore = scoreRelation(query, sourceTape);
          var total = totalConfidence(wordScores, relationScore);
          var lowItems = findLowConfidenceItems(wordScores, relationScore);

          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({
            wordScores: wordScores,
            relationScore: relationScore,
            totalConfidence: total,
            lowConfidenceItems: lowItems,
            meetsThreshold: total >= CONFIG.confidenceThreshold
          }, null, 2));
        } catch (err) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
      return;
    }

    // GET /health
    if (parsedUrl.pathname === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", service: "loom-harness" }));
      return;
    }

    // GET / — status page
    if (parsedUrl.pathname === "/" || parsedUrl.pathname === "") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><html><head><title>loom harness</title>" +
        "<style>body{background:#0b0c10;color:#eef1f4;font:14px Consolas,monospace;padding:24px}</style>" +
        "</head><body>" +
        "<h1>loom harness</h1>" +
        "<p>Confidence-aware query orchestrator.</p>" +
        "<h2>Endpoints</h2>" +
        "<ul>" +
        "<li><code>POST /score</code> — score query confidence against a tape</li>" +
        "<li><code>POST /search</code> — full orchestration: score, search gaps, re-score, answer</li>" +
        "<li><code>GET /health</code> — health check</li>" +
        "</ul>" +
        "<h2>Config</h2>" +
        "<pre>" + JSON.stringify(CONFIG, null, 2) + "</pre>" +
        "</body></html>");
      return;
    }

    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
  });
}

// ─── Logging ──────────────────────────────────────────────────────────────
function log(level, msg) {
  if (CONFIG.logLevel === "quiet") return;
  var tags = { debug: "DBG", info: "INF", warn: "WRN", error: "ERR" };
  var tag = tags[level] || "???";
  var ts = new Date().toISOString().substring(11, 23);
  console.log("[" + ts + "] [" + tag + "] " + msg);
}

// ─── CLI mode: score a query against a tape JSON file ─────────────────────
if (require.main === module) {
  var tapePath = process.argv[2];
  var queryText = process.argv[3];

  if (!tapePath || !queryText) {
    // Start server
    var port = parseInt(process.env.HARNESS_PORT || "3001", 10);
    var server = createServer();
    server.listen(port, function() {
      console.log("loom harness listening on port " + port);
    });
    return;
  }

  // CLI: score a query against a tape file
  var fs = require("fs");
  var tapeData = JSON.parse(fs.readFileSync(tapePath, "utf8"));
  var events = Array.isArray(tapeData) ? tapeData : (tapeData.beats || tapeData.events || []);

  var wordScores = scoreQueryWords(queryText, events);
  var relationScore = scoreRelation(queryText, events);
  var total = totalConfidence(wordScores, relationScore);

  console.log("\nQuery: " + queryText);
  console.log("Tape: " + tapePath + " (" + events.length + " events)\n");
  console.log("Word confidence:");
  for (var i = 0; i < wordScores.length; i++) {
    var bar = "#".repeat(Math.round(wordScores[i].confidence * 20)) + ".".repeat(20 - Math.round(wordScores[i].confidence * 20));
    console.log("  " + wordScores[i].word + "  " + bar + " " + wordScores[i].confidence.toFixed(3) + " (" + wordScores[i].status + ")");
  }
  console.log("\nRelation confidence: " + relationScore.toFixed(3));
  console.log("Total confidence:    " + total.toFixed(3));
  console.log("Threshold:           " + CONFIG.confidenceThreshold);
  console.log("Meets threshold:     " + (total >= CONFIG.confidenceThreshold ? "YES" : "NO"));

  var lowItems = findLowConfidenceItems(wordScores, relationScore);
  if (lowItems.length) {
    console.log("\nWould search (ascending confidence):");
    for (var j = 0; j < lowItems.length; j++) {
      console.log("  " + lowItems[j].term + "  (" + lowItems[j].confidence.toFixed(3) + ")");
    }
  }
}

// ─── Exports (for require() usage) ────────────────────────────────────────
module.exports = {
  CONFIG: CONFIG,
  scoreQueryWords: scoreQueryWords,
  scoreRelation: scoreRelation,
  totalConfidence: totalConfidence,
  findLowConfidenceItems: findLowConfidenceItems,
  searchDuckDuckGo: searchDuckDuckGo,
  recalculateConfidence: recalculateConfidence,
  processQuery: processQuery,
  contentTokens: contentTokens,
  tokenAffinity: tokenAffinity,
  bestTokenAffinity: bestTokenAffinity,
  tapeVocabulary: tapeVocabulary,
  tapeVocabKeys: tapeVocabKeys,
  createServer: createServer
};
