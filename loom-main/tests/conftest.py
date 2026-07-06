import importlib.util


def _missing(mod: str) -> bool:
    return importlib.util.find_spec(mod) is None


# Audio/visual ENDPOINT tests need heavy deps (numpy / Pillow). The deterministic substrate is
# stdlib-pure (enforced by test_engine_purity), so when those deps are absent we skip these modules
# gracefully instead of aborting the whole suite at collection time.
# To enable them:  pip install numpy pillow soundfile
_OPTIONAL = {
    "test_sampler.py": ["numpy"],
    "test_voicesynth.py": ["numpy"],
    "test_visual_tape.py": ["PIL"],
    "test_loom_narration.py": ["numpy", "PIL"],
    "test_loom_studio.py": ["numpy", "PIL"],
}

collect_ignore = [name for name, mods in _OPTIONAL.items() if any(_missing(m) for m in mods)]
