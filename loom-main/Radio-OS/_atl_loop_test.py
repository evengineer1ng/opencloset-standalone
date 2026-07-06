"""
The Loop v0 — benchmark against live, frozen ATL (http://127.0.0.1:8000), standalone.

Antenna (profile + candidates) -> Meta-plugin generator (spec) -> narration text.
No full runtime, no Ollama/TTS required: narration runs in deterministic template mode.
Proves the heart of Radio OS against a foreign already-running backend, zero ATL changes.
"""
import importlib.util
import os
import sys
import tempfile
import json

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


antenna = _load("antenna_http", "plugins/antenna_http.py")
generated = _load("generated_meta", "plugins/meta/generated.py")

BASE = "http://127.0.0.1:8000"
ENDPOINTS = [
    {"path": "/api/development", "source": "api_development"},
    {"path": "/api/ml", "source": "api_ml"},
    {"path": "/api/research", "source": "api_research"},
    {"path": "/api/league", "source": "api_league"},  # currently 500s — must be tolerated
]

print("=" * 70)
print("STEP 1 — ANTENNA: profile the foreign system (loud and proud)")
print("=" * 70)
eps = [(e["path"], e["source"]) for e in ENDPOINTS]
signature = antenna.build_signature(BASE, eps)
for source, prof in signature["endpoints"].items():
    if prof.get("ok"):
        fm = prof["field_map"]
        print(f"  ✓ {prof['path']:20} → '{source}': {prof['count']} item(s) at {prof['item_path']}")
        print(f"      buckets: title={fm.get('title')} body={fm.get('body')} "
              f"actor={fm.get('actor')} id={fm.get('id')} priority={fm.get('priority')}")
    else:
        print(f"  ✗ {prof['path']:20} : {prof['reason']}  (foreign source tolerated)")

print("\n" + "=" * 70)
print("STEP 2 — ANTENNA: map foreign items → candidates")
print("=" * 70)
candidates = []
for source, prof in signature["endpoints"].items():
    if not prof.get("ok"):
        continue
    data = antenna._fetch_json(BASE.rstrip("/") + prof["path"])
    _, items = antenna._best_item_list(data)
    for it in items[:6]:
        candidates.append(antenna.map_item_to_candidate(it, prof["field_map"], source, 70.0))
print(f"  emitted {len(candidates)} candidate(s). examples:")
for c in candidates[:4]:
    print(f"    [{c['source']}] heur={c['priority']:.0f} :: {c['title'][:70]}")

print("\n" + "=" * 70)
print("STEP 3 — META-PLUGIN GENERATOR: draft the authorship spec from the signature")
print("=" * 70)
spec = generated.generate_meta_plugin_spec(signature, station_name="ATLFM", voices=["host"])
print("  station:", spec["station"])
print("  sources authored:", list(spec["sources"].keys()))
for note in spec["_notes"]:
    print("   ·", note)

# write the spec where the plugin will read it (a temp STATION_DIR)
station_dir = tempfile.mkdtemp(prefix="ATLFM_")
with open(os.path.join(station_dir, "meta_plugin_spec.json"), "w", encoding="utf-8") as f:
    json.dump(spec, f, indent=2)
os.environ["STATION_DIR"] = station_dir

print("\n" + "=" * 70)
print("STEP 4 — SIMULATOR: the meta-plugin narrates (template mode, no LLM)")
print("=" * 70)
plugin = generated.GeneratedMetaPlugin()
plugin.initialize(runtime_context={"log": lambda ch, m: None}, cfg={"station": {"name": "ATLFM"}}, mem={})
segments = plugin.curate_candidates(candidates, {})
print(f"  curated {len(segments)} segment(s) → ATLFM broadcast:\n")
for seg in segments[:5]:
    pkt = plugin.generate_script(seg, {})
    print(f"  📻 [{seg['source']}]")
    print(f"     HOST: {pkt['host_intro']}")
    if pkt["summary"]:
        print(f"           {pkt['summary'][:160]}")
    print()

print("=" * 70)
print("RESULT: foreign ATL backend → profiled → authored → narrated, zero ATL changes.")
print(f"signature.json + meta_plugin_spec.json would live in the station dir: {station_dir}")
print("=" * 70)
