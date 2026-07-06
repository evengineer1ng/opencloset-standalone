"""End-to-end plugin resolution: a foreign plugin fetched, hash-verified, loaded, used.

We stand a real `.tar.gz` up behind a `file://` URL — the exact path a `github:` source
takes, byte for byte (urlopen handles both). Nothing in the fetch/verify/unpack pipeline
is mocked. This is the club's bouncer doing its one job, proven.
"""
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

from oradio_engine.plugins import IntegrityError, PluginRef, PluginResolver, load_plugin
from oradio_engine.registry import build_source

# A tiny "github" plugin: when register()'d, it adds a source kind the engine can build.
PLUGIN_SRC = '''\
class ZooSource:
    def __init__(self, name):
        self.name = name
    def marker(self):
        return "zoo-from-github"

def _factory(name, **params):
    return ZooSource(name)

def register(registry):
    registry.register_source("zoo_test", _factory)
'''


# A fetchable telemetry source backed by a real engine organ — proves a foreign .oradio
# can pull a world/source it's never seen and actually run it.
FEED_SRC = '''\
from oradio_engine.live import LiveFeedOrgan, ScriptedSource

def _factory(name, **params):
    return LiveFeedOrgan(name, source=ScriptedSource([
        {"title": "lion", "body": "the lion paces", "type": "zoo", "priority": 0.6},
        {"title": "tiger", "body": "the tiger won't eat", "type": "zoo", "priority": 0.7},
    ]))

def register(registry):
    registry.register_source("zoo_feed", _factory)
'''


def _make_tarball(tmp: Path, modname: str, src: str) -> tuple[str, str]:
    """Build a github-style archive (wrapped in <mod>-main/) and return (file_url, sha256)."""
    tar_path = tmp / f"{modname}.tar.gz"
    data = src.encode("utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(f"{modname}-main/{modname}.py")   # github wraps in <repo>-<ref>/
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return tar_path.as_uri(), hashlib.sha256(tar_path.read_bytes()).hexdigest()


def _make_plugin_tarball(tmp: Path) -> tuple[str, str]:
    return _make_tarball(tmp, "zoo", PLUGIN_SRC)


@pytest.fixture
def isolated_club(tmp_path, monkeypatch):
    monkeypatch.setenv("ORADIO_CLUB_DIR", str(tmp_path / "club"))
    yield tmp_path
    # don't leak imported plugins / registered kinds into other tests
    for mod in ("zoo", "zoo_feed"):
        sys.modules.pop(mod, None)
    from oradio_engine.registry import SOURCE_KINDS, SOURCE_META
    for kind in ("zoo_test", "zoo_feed"):
        SOURCE_KINDS.pop(kind, None)
        SOURCE_META.pop(kind, None)


def test_parse_ref_string_and_dict():
    a = PluginRef.parse("zoo", "github:evan/zoo@v1#abc123")
    assert (a.source, a.ref, a.sha256) == ("github:evan/zoo", "v1", "abc123")
    b = PluginRef.parse("zoo", {"source": "github:evan/zoo", "ref": "main", "sha256": "d"})
    assert b.is_external and b.ref == "main"
    assert PluginRef.parse("forkuniverse").is_external is False  # built-in, no source


def test_github_ref_resolves_to_codeload_url():
    ref = PluginRef(name="zoo", source="github:evan/zoo", ref="v2")
    assert ref.tarball_url() == "https://codeload.github.com/evan/zoo/tar.gz/v2"


def test_fetch_verify_unpack_load_and_use(isolated_club):
    url, digest = _make_plugin_tarball(isolated_club)
    ref = PluginRef(name="zoo", source=url, ref="main", sha256=digest)

    resolver = PluginResolver(allow_network=True)   # host opted in to fetching
    resolved = resolver.resolve(ref)          # fetch + sha256 verify + unpack to cache
    assert resolved is not None and resolved.verified
    assert os.path.isfile(os.path.join(resolved.path, "zoo.py"))

    load_plugin(resolved)                      # import + register the kind
    organ = build_source("zoo_test", "z")      # the engine can now build it
    assert organ.marker() == "zoo-from-github"


def test_hash_mismatch_is_rejected(isolated_club):
    url, _digest = _make_plugin_tarball(isolated_club)
    ref = PluginRef(name="zoo", source=url, ref="main", sha256="0" * 64)  # wrong pin
    with pytest.raises(IntegrityError):
        PluginResolver(allow_network=True).resolve(ref)


def test_unpinned_external_plugin_is_refused_before_fetch(isolated_club):
    # no sha256 = no pin = no run. We must refuse BEFORE opening the tarball, even with the
    # opener available — an unpinned shared .oradio can never import arbitrary remote code.
    url, _digest = _make_plugin_tarball(isolated_club)
    opened = []
    ref = PluginRef(name="zoo", source=url, ref="main", sha256=None)
    resolver = PluginResolver(allow_network=True, opener=lambda u: opened.append(u))
    with pytest.raises(IntegrityError):
        resolver.resolve(ref)
    assert opened == []                        # refused before a single byte was fetched


def test_network_off_by_default_so_decoding_never_auto_fetches(isolated_club):
    url, digest = _make_plugin_tarball(isolated_club)
    ref = PluginRef(name="zoo", source=url, ref="main", sha256=digest)
    assert PluginResolver().resolve(ref) is None   # default: no network, no silent import


def test_cache_hit_needs_no_network(isolated_club):
    url, digest = _make_plugin_tarball(isolated_club)
    ref = PluginRef(name="zoo", source=url, ref="main", sha256=digest)
    PluginResolver(allow_network=True).resolve(ref)   # populate cache

    def _no_network(_url):
        raise AssertionError("must not hit the network on a cache hit")

    offline = PluginResolver(allow_network=False, opener=_no_network)
    resolved = offline.resolve(ref)            # served from ~/.oradio_club cache
    assert resolved is not None and os.path.isfile(os.path.join(resolved.path, "zoo.py"))


def test_missing_with_no_source_returns_none(isolated_club):
    # a built-in name we don't have locally and that declares no source -> graceful None
    ref = PluginRef(name="totally_unknown_organ")
    assert PluginResolver(allow_network=False).resolve(ref) is None


def test_open_foreign_missing_plugin_is_calm_not_a_crash(isolated_club):
    # the ftb-crash scenario, generalized: a world naming a plugin we lack, no source.
    from oradio_engine.loader import open_oradio
    desc = {"oradio": "x", "world": {"organ": "totally_unknown_organ"}}
    result = open_oradio(desc)                      # must NOT raise
    assert result.ok is False and result.engine is None
    assert any("totally_unknown_organ" in m for m in result.report.missing_required)


def test_open_foreign_oradio_fetches_plugin_and_runs_with_consent(isolated_club):
    # a stranger's .oradio names a source it carries by github coordinate; WITH consent
    # (allow_sensitive=the UI's "yes, fetch it") the club fetches it, verifies the hash,
    # loads it, and the simulation actually runs. The zoo dream — opted into.
    from oradio_engine.loader import open_oradio
    url, digest = _make_tarball(isolated_club, "zoo_feed", FEED_SRC)
    desc = {
        "oradio": "zoo",
        "telemetry": [{"source": "zoo_feed", "name": "zoo", "plugin": url, "sha256": digest}],
    }
    result = open_oradio(desc, allow_sensitive=True)
    assert result.ok, result.report.summary()
    result.engine.run(steps=4)
    assert any(c.type == "zoo" for c in result.engine.bus)   # the lion/tiger are on the bus


def test_open_foreign_oradio_withholds_unconsented_remote_code(isolated_club):
    # the security fix: the SAME foreign .oradio, opened WITHOUT consent, must not fetch or
    # import the remote code. It's advertised, withheld, and degraded — never silently run.
    from oradio_engine.loader import open_oradio
    url, digest = _make_tarball(isolated_club, "zoo_feed", FEED_SRC)
    desc = {
        "oradio": "zoo",
        "telemetry": [{"source": "zoo_feed", "name": "zoo", "plugin": url, "sha256": digest}],
    }
    result = open_oradio(desc)                       # no allow_sensitive → no consent
    assert result.ok is False and result.engine is None
    assert result.plugins and result.plugins[0].sensitive
    assert result.plugins_withheld and "zoo_feed" not in sys.modules
