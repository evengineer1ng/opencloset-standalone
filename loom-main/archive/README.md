# archive/

Dead code with zero references in the live tree, set aside 2026-06-14 during the pre-alpha
cleanup. Kept for **lore**, not part of the shipped shape.

- `oradio_runtime.py` — the original Radio-OS station runtime (superseded by the engine + booth).
- `ribbon_bridge.py`, `show_the_machine.py` — old experiments, unreferenced.

Note: the Radio-OS *world organs* (`plugins/` — forkuniverse, neikos, oracle) and the older
players (`oradio_player*.py`, `loom_player_ui.py`) intentionally **remain live** in the tree —
they're the proof that the `.oradio` engine is a domain-blind codec, not an F1 toy.
