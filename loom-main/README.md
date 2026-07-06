# Oracle Radio

A domain-blind **simulation codec**. An `.oradio` file is to a simulation what a
`.png` is to an image: a tiny (KB) declaration that a general engine *decodes* and
runs without knowing the domain. Heavy capability (LLMs, voices, theme clips, live
data) is resolved at the **endpoint** by the Club — never shipped in the artifact.

This repo ships three things:

| Ship target | What it is | Where it lives here |
|---|---|---|
| **loom** | The act of authoring an `.oradio` (a word — verb/noun/instrument, like *dream*: "I loom a world"), through a 4-question I/O contract — **Q1 world · Q2 inputs · Q3 theme+voice · Q4 outputs** — where the surface is just *where you loom*. | `loom/` (studio, exporter, shim/antenna generator, broadcast grammar), `docs/THE_LOOM.md` (surface still in progress) |
| **the `.oradio` format** | The KB declaration: a world, its inputs, a skin+voice, its outputs — all references, no content. That low-level-ness is *why* it's a portable file. | `spec/examples/*.oradio`, `spec/ORADIO_FORMAT.md`, `spec/ORADIO_SCHEMA_V2.md`, `oradio_engine/descriptor.py` |
| **the club** | Machine-level capability resolver + the host the engine runs in (the bouncer): configure once, reuse forever; ask only when new/changed/vanished; install missing plugins from wherever they live. | `oradio_engine/club.py`, `provisioning.py` |

## The booth — a tape speaks (no model, no GPU)

A tape (a finished F1 race, a heart-rate log, an RSS feed) becomes language you read or hear,
**deterministically**. A small grammar gives it a voice; the loom pulls causal threads; the inquiry
layer asks questions; the mixer is the faders you ride live. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```bash
python -m tools.bake_f1 --year 2026 --round 7 --out data/f1.json   # ingest a tape (one-time)
python -m tools.loom_report --tape data/f1.json --grammar data/grammars/intern.json \
    --verbs data/english/irregular_verbs.json --rules data/f1_causal_rules.json \
    --inquiry data/inquiry/f1.json --out transcripts/f1.txt          # read it (~25 ms)
python loom_booth.py --tape f1=data/f1.json                          # play it: ride the faders
```

The whole narration stack — speech · threads · inquiry · mixer · antenna — is ~800 LOC + a few KB of
JSON declarations, deterministic, stdlib.

**Touch booth (any browser, incl. a phone):** `python loom_serve.py` → open `http://127.0.0.1:8765`
— faders, antenna toggles, keep-a-mixtape; the browser speaks. Playback needs no model or GPU
(see [`docs/ANDROID.md`](docs/ANDROID.md)).

**Measured** ([`docs/BENCHMARK.md`](docs/BENCHMARK.md)): narrating structured events, this is ~5
orders of magnitude faster than a local LLM and 0% confabulation vs a steelmanned 8B's 34% — the
LLM's only edge is fluency. The lesson: use the model to *author* the renderer (compile time), not
to *narrate* (runtime). Reproduce: `python -m tools.benchmark --n 50`.

## The boundary (why this repo is small)

- **Engine core** (`oradio_engine/*.py`) — pure Python stdlib. The decoder is
  domain-blind. The only hard third-party dep is **PyYAML**, used solely to parse
  `.oradio` files.
- **Shims** (`oradio_engine/shims/`) — adapter *contracts*. They consume an organ's
  *output* (a state object handed in, or a read-only path to its emitted artifact —
  e.g. `league.sqlite`), never the organ's source. So no organ runtime is vendored
  here.
- **Endpoint** — the Club config (`~/.oradio_club/club.json`), resolved heavy assets,
  and the live data the shims point at. None of it is in the repo.

The simulation *organs* (ForkUniverse, Neikos, FTB, Oracle, ATL, MoCo) stay in their
own homes; the engine points at their emitted data. Oracle Radio is the decoder + the
format + the examples — not the organ host.

## Repo layout (decided)

```
oradio_engine/   the .oradio DECODER + the contract (5-verb organ protocol, dipole,
                 lens, binding, evidence, index, club). Pure: importable on stdlib +
                 PyYAML alone — enforced by tests/test_engine_purity.py. This IS the
                 governed core; there is deliberately no separate contract/ folder.
spec/            the format — examples + ORADIO_FORMAT / SCHEMA docs.
loom/            authoring — studio, exporter, narration, antenna/shim generator.
plugins/organs/  pre-stocked light reference organs (oracle, neikos, forkuniverse).
*.py (root)      the player + club runtime (player UI, resolver, provisioning,
                 ribbon, voice, theme, visual rasterization callers…). The endpoint
                 layer. Flat for now.
```

**The one rule that matters: the decoder stays pure** (the guard test fails the build
if a heavy dep — PIL/numpy/cv2/tkinter/audio — creeps into `import oradio_engine`,
because that would fork the file format). Everything else is code-organization taste:
folding the flat root runtime into a `club/` package is an optional later cleanup, not
a format concern, and is **not** pursued mid-development to avoid churn. The earlier
"contract-centric 4-folder" plan is retired in favor of this — the engine package
already *is* the contract.

## Run

```bash
pip install -e .          # core (stdlib + PyYAML)
pip install -e ".[runtime]"  # + audio runtime (numpy/requests/sounddevice/soundfile)

python -m oradio_engine open spec/examples/me.oradio --steps 5
python -m oradio_engine club            # endpoint capability status

pytest                    # engine + shim + format test suite
```

The suite runs headless (no `tkinter` needed). The desktop GUIs are optional — to launch
those, install Tk: `apt install python3-tk` (Debian/Ubuntu) or `brew install python-tk` (macOS).
Windows ships Tk with the python.org installer.

## The 5-verb organ contract

Every organ federates through one interface (`oradio_engine/contract.py`):
`identity · advance · observe · read_truth · apply_input`, with an explicit
`DETERMINISTIC` vs `LIVE` determinism class. A run is `world(t) = f(seed, tape[0..t])`
— deterministic organs replay byte-identical; live organs record to an immutable
intake tape so replay is byte-identical too.

See `docs/SIMULATION_ENGINE.md` (canonical), `docs/CANON.md` (current-canon index),
and `docs/THE_LOOM.md`.

## License

Dual-scoped (see [`LICENSING.md`](LICENSING.md)): the **format/spec** (`spec/`) is **Apache-2.0**
so anyone can implement `.oradio`/`.loom` freely; the **engine, booth, and apps** are **AGPL-3.0**
(no closed SaaS forks). Commercial dual-licensing is available — copyright is held in one place.
