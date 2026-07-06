"""Runtime bootstrap helpers shared outside pytest.

The real organs in this repo are vendored under ``plugins/organs`` and imported by
their bare module names (for example ``import neikos``). Tests made that work by
mutating ``sys.path`` in ``conftest.py``; runtime entrypoints need the same bootstrap.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import app_paths


def repo_root() -> Path:
    return app_paths.bundle_root()


def ensure_repo_plugin_paths() -> None:
    """Make vendored organ modules importable in normal runtime entrypoints."""

    root = repo_root()
    plugin_roots = [
        str(root),
        str(root / "plugins"),
        str(root / "plugins" / "organs"),
    ]
    for path in reversed(plugin_roots):
        if path not in sys.path:
            sys.path.insert(0, path)

    # Keep the player/runtime environment aligned with the vendored plugin roots.
    os.environ.setdefault("RADIO_OS_ROOT", str(root))
    os.environ.setdefault("RADIO_OS_PLUGINS", str(root / "plugins"))
