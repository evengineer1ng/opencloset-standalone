"""Plugin resolution — pip-lite over GitHub, pure stdlib.

An `.oradio` names a plugin by a *thin reference*: a name/kind + where to get it
(`github:owner/repo@ref`) + a `sha256` to pin it. The Club resolves it:

    already in the local cache or pre-stocked?  ->  use it.
    missing but it declares a source?           ->  fetch the tarball (urllib),
                                                     verify the hash, unpack into
                                                     the cache, make it importable.

No new dependencies — GitHub distribution rides on stdlib ``urllib`` + ``hashlib`` +
``tarfile``. (The decoder stays pure; see tests/test_engine_purity.py.)

Trust is SEEDED, not decreed: the pre-stocked foundational plugins are trusted and
present; an external plugin is **hash-pinned** (you run exactly the bytes the author
saw) and isolated in the cache. Reputation/evidence-of-survival comes later — trust
emerges, it isn't a wall here.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import os
import sys
import tarfile
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib.request import urlopen

GITHUB_PREFIX = "github:"


class IntegrityError(Exception):
    """A fetched plugin's bytes did not match its declared sha256."""


class MissingPlugin(Exception):
    """A required plugin isn't installed and couldn't be fetched (no source, or fetch failed).

    Carries enough to tell the human calmly what's missing — never a raw traceback.
    """

    def __init__(self, name: str, kind: str, source: Optional[str] = None) -> None:
        self.name = name
        self.kind = kind
        self.source = source
        if source:
            msg = f"plugin '{kind}' (as {name!r}) couldn't be fetched from {source}"
        else:
            msg = f"plugin '{kind}' (as {name!r}) is not installed and declares no source to fetch from"
        super().__init__(msg)


@dataclass(frozen=True)
class PluginRef:
    """A thin, pinned reference to a plugin — the only thing the .oradio carries."""

    name: str                       # the kind/name the .oradio uses, e.g. "zoo"
    source: Optional[str] = None    # "github:owner/repo" | a tarball URL | None (built-in)
    ref: str = "main"               # tag / branch / commit sha
    sha256: Optional[str] = None     # integrity pin (recommended for anything external)

    @classmethod
    def parse(cls, name: str, spec: object = None) -> "PluginRef":
        """From a dict {source, ref, sha256} or a string 'github:o/r@ref#sha' (or bare name)."""
        if spec is None or isinstance(spec, (int, float)):
            return cls(name=name)
        if isinstance(spec, str):
            rest, _, sha = spec.partition("#")
            src, _, ref = rest.partition("@")
            return cls(name=name, source=src or None, ref=ref or "main", sha256=sha or None)
        if isinstance(spec, dict):
            return cls(
                name=name,
                source=spec.get("source"),
                ref=str(spec.get("ref", "main")),
                sha256=spec.get("sha256"),
            )
        return cls(name=name)

    @property
    def is_external(self) -> bool:
        return bool(self.source)

    def tarball_url(self) -> str:
        """Resolve the source to a downloadable .tar.gz URL."""
        if not self.source:
            raise ValueError(f"plugin {self.name!r} has no source to fetch from")
        if self.source.startswith(GITHUB_PREFIX):
            owner_repo = self.source[len(GITHUB_PREFIX):]
            # codeload is the direct (no-redirect) archive endpoint
            return f"https://codeload.github.com/{owner_repo}/tar.gz/{self.ref}"
        return self.source  # already a URL (incl. file:// for tests / local mirrors)


@dataclass
class ResolvedPlugin:
    ref: PluginRef
    path: str            # importable directory on disk (cache slot or pre-stocked dir)
    verified: bool       # True if hash-pinned + matched, or trusted local


def cache_dir() -> str:
    base = os.environ.get("ORADIO_CLUB_DIR") or os.path.join(os.path.expanduser("~"), ".oradio_club")
    d = os.path.join(base, "plugins")
    os.makedirs(d, exist_ok=True)
    return d


class PluginResolver:
    """Local-first, then fetch-from-source. The Club's bouncer for code.

    ``allow_network`` defaults to **False**: decoding a *shared* `.oradio` must never reach
    out and import remote code on its own. The host opts in explicitly (``allow_network=True``,
    which ``open_oradio`` grants only once the fetch is consented — the one front door).

    ``opener`` is injectable so tests drive the full fetch/verify/unpack path with a
    ``file://`` tarball — the real ``https://`` path is byte-for-byte identical.
    """

    def __init__(
        self,
        prestocked_dirs: Optional[List[str]] = None,
        allow_network: bool = False,
        opener: Callable[[str], object] = urlopen,
    ) -> None:
        self.prestocked = list(prestocked_dirs or [])
        self.allow_network = allow_network
        self._open = opener

    # -- local (cache + pre-stocked) -------------------------------------- #
    def _slot(self, ref: PluginRef) -> str:
        return os.path.join(cache_dir(), f"{ref.name}@{ref.ref}")

    def local_path(self, ref: PluginRef) -> Optional[str]:
        slot = self._slot(ref)
        if os.path.isdir(slot):
            return _plugin_root(slot)
        for d in self.prestocked:
            if os.path.exists(os.path.join(d, ref.name)) or os.path.exists(os.path.join(d, ref.name + ".py")):
                return d
        return None

    # -- fetch (the bouncer installs) ------------------------------------- #
    def fetch(self, ref: PluginRef) -> ResolvedPlugin:
        # No pin, no run: an external plugin MUST be hash-pinned before we fetch a single byte.
        # This is the invariant this module promises ("you run exactly the bytes the author saw")
        # — enforced, not just documented, so an unpinned shared .oradio can't import remote code.
        if not ref.sha256:
            raise IntegrityError(
                f"plugin {ref.name!r} from {ref.source!r} declares no sha256 — refusing to fetch "
                f"and import unpinned remote code. Pin it: 'github:owner/repo@ref#<sha256>'."
            )
        url = ref.tarball_url()
        with self._open(url) as resp:               # urlopen handles https + file://
            data = resp.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != ref.sha256:
            raise IntegrityError(
                f"plugin {ref.name!r} hash mismatch: declared {ref.sha256}, got {digest}"
            )
        slot = self._slot(ref)
        if os.path.isdir(slot):
            import shutil
            shutil.rmtree(slot)
        os.makedirs(slot, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(slot, filter="data")      # py3.12+ safe extraction
        # record what we pinned, for audit (the receipt)
        with open(os.path.join(slot, ".oradio_pin"), "w", encoding="utf-8") as f:
            f.write(f"{ref.source}@{ref.ref}\nsha256={digest}\n")
        return ResolvedPlugin(ref=ref, path=_plugin_root(slot), verified=True)

    def resolve(self, ref: PluginRef) -> Optional[ResolvedPlugin]:
        """Local first; fetch if external + network allowed; else None (caller reports gracefully)."""
        local = self.local_path(ref)
        if local is not None:
            return ResolvedPlugin(ref=ref, path=local, verified=True)
        if ref.is_external and self.allow_network:
            return self.fetch(ref)
        return None


def declared_external_plugins(descriptor: object) -> List[PluginRef]:
    """The external (remote) plugins a descriptor's worlds/telemetry would fetch + import.

    These are *code*, not data — so opening a shared `.oradio` must consent-gate them the same
    way it gates sensitive telemetry (see ``Club.plugin_manifest`` / ``open_oradio``).
    """
    refs: List[PluginRef] = []
    nodes = list(getattr(descriptor, "worlds", []) or []) + list(getattr(descriptor, "telemetry", []) or [])
    for node in nodes:
        params = getattr(node, "params", {}) or {}
        if not params.get("plugin"):
            continue
        kind = getattr(node, "organ", None) or getattr(node, "source", None)
        ref = PluginRef.parse(kind, {
            "source": params.get("plugin"),
            "ref": params.get("ref", "main"),
            "sha256": params.get("sha256"),
        })
        if ref.is_external:
            refs.append(ref)
    return refs


def _plugin_root(slot: str) -> str:
    """GitHub archives wrap everything in a single ``repo-ref/`` dir — descend into it."""
    try:
        entries = [e for e in os.listdir(slot) if not e.startswith(".")]
    except FileNotFoundError:
        return slot
    if len(entries) == 1 and os.path.isdir(os.path.join(slot, entries[0])):
        return os.path.join(slot, entries[0])
    return slot


def load_plugin(resolved: ResolvedPlugin) -> None:
    """Make a resolved plugin importable and let it register its kind(s).

    Contract (minimal): the plugin is importable as a top-level module/package named
    ``<ref.name>`` from its root, and registering its kind on import (calling
    ``register_organ`` / ``register_source``) OR exposing a ``register()`` entrypoint.
    """
    if resolved.path not in sys.path:
        sys.path.insert(0, resolved.path)
    mod = importlib.import_module(resolved.ref.name)
    register = getattr(mod, "register", None)
    if callable(register):
        from oradio_engine import registry
        register(registry)
