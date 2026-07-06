# `.oradio` Format

## Purpose

`.oradio` is the portable station artifact produced by Radio OS Studio.

It represents a live media organism: source configuration, meaning/spec artifacts, production
settings, runtime requirements, and the bundled assets/plugins needed by the playback layer.

## Boundary

- Studio builds `.oradio`.
- Studio may preview a draft for builders, but that simulator is not the listener playback path.
- `bookmark.py` is the current playback kernel foundation.
- `shell_bookmark.py` is the library/vault/player-manager foundation.
- A standalone `.oradio` must not depend on Studio at playback time.
- Live LLM narration is required. There is no listener-facing deterministic downgrade tier.

## Current Package

The current `.oradio` export is a zip-compatible package with a `.oradio` extension.

Required files:

- `oradio.json` - package descriptor
- `manifest.yaml` - station runtime manifest
- `requirements.json` - declared station capabilities and requirements
- `requirements.lock.json` - resolved export-time assets and resolution strategy

Optional files:

- `signature.json` - source signature profile
- `meta_plugin_spec.json` - generated/human-edited meaning layer
- `plugins/*.py` - referenced feed/plugin code
- `plugins/meta/*.py` - referenced meta-plugin code
- `assets/voices/*` - bundled voice files and sidecars when resolvable

When a voice is bundled, the packaged `manifest.yaml` rewrites that role to a package-relative
path such as `assets/voices/voice.onnx`. The exported artifact must not depend on the author's
absolute local voice path.

## `oradio.json`

Current descriptor fields:

- `format`: `oradio`
- `format_version`: package format version
- `created_at`: export timestamp
- `station_id`: station identifier
- `station_name`: station display name
- `entry`: relative paths to manifest/signature/spec/requirements files
- `kernel`: playback-kernel metadata
- `library`: optional library/player-manager metadata
- `requirements`: embedded summary of declared requirements
- `portable_warnings`: known reasons the artifact may need target-machine setup

## `requirements.json`

`requirements.json` is the portable capability contract. It says what the station needs, not what
the exporting machine happened to have.

Current sections:

- `narration`: currently `live_llm`
- `llm`: provider, endpoint, models, required flag, and machine-level provisioning strategy
- `voices`: voice provider and role-to-reference map
- `piper`: whether a Piper binary is needed and how it should be resolved
- `sfx`: future bundle-or-fetch sound-effect requirements

## `requirements.lock.json`

`requirements.lock.json` records what Studio resolved at export time.

Current sections:

- `lock_version`
- `created_at`
- `narration`
- `voices`: one record per role, including bundled arcname/hash when available
- `piper`: per-machine resolution note for the binary
- `llm`: provider/models plus required machine-level Tune-In
- `sfx`
- `unresolved`: non-fatal items that must resolve on the target machine

## Resolution Ladder

The player/resolver checks requirements in this order:

1. bundled assets inside the `.oradio`
2. machine cache, such as installed voices or Piper
3. machine-level LLM Tune-In / club membership

If the LLM is not ready, the station is not downgraded. It is getting tuned in.

The current resolver implementation is `oradio_resolver.py`; machine-level LLM setup lives in
`provisioning.py`; the standalone bootstrapper is `oradio_player.py`; the first forked listener shell
is `oradio_player_ui.py`.

Bundled assets are checked against `requirements.lock.json` size/hash metadata before launch.

## Playback Goal

The portability phase turns this package into something a user can open directly and hear without
Studio installed. The unresolved design choice is how double-click playback is delivered across
operating systems: file association, bundled player, or a self-contained artifact strategy.

Current Windows seam: `python oradio_player.py --install-windows-association` installs a per-user
`.oradio` association under `HKCU\Software\Classes` and routes double-click opens through
`oradio_player.py "%1"` (the themed GUI runtime). Use `--print-windows-association` to inspect the
registry plan without changing the machine.

---

## Format v1 — Validity (`oradio_validate.py`)

"v1" formalizes the structure above: the **four required files**, the optional set, and the rules that
make a package *well-woven*. (The on-disk `format_version` field is `0.1` today and moves to `1.0` when
the milestone is cut; the validator accepts both.)

**Since the original spec, two additions:**

- `oradio.json` carries an optional **`metadata`** block (media-style: `title` / `artist` / `album` /
  `genre` / `comment` / `player` / `artwork`).
- An optional **`cover.jpg`**, and the whole package may be a **JPEG+ZIP polyglot** (the cover JPEG
  prepended) — the file is then simultaneously a valid image *and* a valid `.oradio`, and still validates.

**Validity rules (what `oradio_validate.py` enforces):**

- *Errors (fail validity — the package is malformed or self-inconsistent):* not a readable zip; a
  missing required file; `oradio.json` not JSON / `format != "oradio"` / `entry.manifest` pointing at a
  missing member; `manifest.yaml` not YAML; and — the heart of it — a **lock entry marked `bundled`
  whose `arcname` is not actually in the package.**
- *Warnings (still valid — portability/polish risks):* unknown/missing `format_version`; missing
  `station_id` / `station.id` / `station.name`; a referenced **feed/meta plugin not bundled** (relies on
  host install); `narration != "live_llm"`; a bundled asset with no `sha256`/`bytes` integrity.

**Validity vs Resolution (two separate jobs):**

- **`oradio_validate.py`** — *is this cloth well-woven?* Format correctness + internal consistency.
  Touches no machine state.
- **`oradio_resolver.py`** — *can THIS machine play it?* Club LLM, voices, Piper readiness.

A package can be **valid but not ready** (well-formed, but your machine isn't tuned in yet) — that's
the normal first-run state.

**Forward (v1.x):** a `visual_surface` block — the **ribbon-genome recipe** + **transient-surface
templates** (the Loom's visual side) — is the next addition; heavy ribbon clips resolve as **club
assets**, like voices, so the artifact stays KB-tiny.
