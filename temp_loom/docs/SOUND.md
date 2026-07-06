# loom can sing & play — the sampler (no ML, no GPU)

A song is a tape; a voice or instrument is a **sample**. The sampler renders a note-tape into audio
by pitch-shifting + timing + concatenating ONE recorded sample. **Borrowed timbre, deterministic
arrangement** — byte-identical, no model. The loom thesis, in sound.

## Run it now (synth placeholder proves the pipeline)
```sh
python -m tools.sing --synth --notes "C4 D4 E4 F4 G4 A4 B4 C5" --out transcripts/scale.wav
```
Prints an **FFT pitch-correctness check** per note — the deterministic receipt. (Timbre realness is
your ears + an ABX test; this proves the pitch math.)

## Real timbre via Freesound (CC-licensed)
Free token at <https://freesound.org/apiv2/apply>, then `export FREESOUND_API_KEY=...`:
```sh
python -m tools.sing --query "cello single note"           --notes "C4 E4 G4 C5"   # instrument — the win
python -m tools.sing --query "opera vowel ah sustained"    --notes "C4 G4 C5"      # voice — the frontier
```
Freesound previews are CC — the author is printed on fetch; **attribute them.**

## The pure test: a voice from LOGIC, no source (`voicesynth.py`)
A recorded sample is *already baked* — it borrows the human performance, so of course it sounds
human. The pure test of the thesis is whether **deterministic logic alone** can. This is the true
analog of the grammar (generate the waveform, don't borrow it). **No source is the test.**
```sh
python -m tools.voice --notes "C4 E4 G4 C5" --vowel a --vibrato 0.02 --out transcripts/aah.wav
```
Source–filter model: a glottal source (harmonics at f0) → formant filters (each vowel = a tiny table
of resonance freqs) + vibrato (f0 × a slow sine — you dial it) + envelope + breath. Pure numpy, no
ML, no samples, **no key, no sourcing problem.** Pitch verified spectrally (8/8).

Honest: it **will** sound synthy — a human-passable pure-synth voice is the frontier (it's *why*
neural TTS exists). This is the honest substrate for your opera ABX: measure how far pure logic
climbs, with vibrato / formants / the glottal model as the knobs. Realism upgrades, in order: a
better glottal pulse (LF/Rosenberg), formant *transitions* + consonants (diction), and expression
(dynamics, jitter/shimmer). The synth is the voice fader; the sampler stays for instruments.

## Honest scope
- **Instruments are the solved case.** This is a SoundFont/sampler — a 30-year-proven, deterministic,
  human-sounding technique. Get the win here first.
- **Voice/opera is the frontier.** The POC pitch-shifts by resampling, so formants shift too
  ("chipmunk" on big jumps). The real-engine upgrade is a **formant-preserving phase vocoder** +
  **vibrato/expression**. The opera ABX test probes how far the deterministic mirror climbs.
- **The genre/voice knob** = swap the sample (a Freesound query) + expression params (vibrato, attack,
  swing). It's the grammar-voice fader, one layer down — live-swappable. Wiring it into the booth as a
  "sing" mode is the next step.
