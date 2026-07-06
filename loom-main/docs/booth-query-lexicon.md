# Booth Query Lexicon

`booth-presets.html` can load a hosted companion file, `booth-query-lexicon.json`, from the same `docs/` directory.

This keeps the booth mostly self-contained while letting us ship a larger deterministic query vocabulary on GitHub Pages.

## Why this exists

- `query.json` is a large lexeme dump and is too big and too raw to inline directly in the booth page.
- `booth-query-lexicon.json` is a reduced artifact for:
  - field aliases
  - query intent aliases
  - action aliases
  - query mirror hints
- `booth-presets.html` still has inline fallback maps, so the page works even if the hosted JSON is missing.

## Build

From `loom-main/`:

```powershell
python tools/build_booth_query_lexicon.py
```

Optional custom source:

```powershell
python tools/build_booth_query_lexicon.py --source "C:\Users\evana\Downloads\query.json"
```

## GitHub Pages shape

- `docs/booth-presets.html` is the app
- `docs/booth-query-lexicon.json` is the hosted static companion

On GitHub Pages, the booth fetches `./booth-query-lexicon.json` from the same published site.

## Current limitation

The reducer is intentionally broad on the first pass. It improves recall, but some aliases will be noisy.

The next tightening step is domain-aware filtering:

- sports metrics
- race verbs
- incident / operations vocabulary
- style / persona cues
