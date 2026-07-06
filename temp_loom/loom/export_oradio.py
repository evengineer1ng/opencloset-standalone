#!/usr/bin/env python3
"""Bake an existing stations/<X> folder into a portable .oradio — headless, no Studio GUI.

Identity (docs/CONVERGENCE.md §G): a .oradio is the *container*, not the dependencies. By default we
do NOT ship voice models — we reference them by name and let the machine-level "club"
(provisioning.py + oradio_resolver.py) find them on the target. That keeps the artifact tiny, which
buys us headroom for the nice stuff: cover art + media-style metadata.

    python export_oradio.py BasketballFM                      # tiny, voices referenced, auto cover art
    python export_oradio.py BasketballFM --cover art.jpg      # use your own cover image
    python export_oradio.py BasketballFM --polyglot           # the file IS its cover JPEG *and* a .oradio
    python export_oradio.py BasketballFM --bundle-voices      # fat: embed the voice models
    python export_oradio.py --all

Polyglot note: a .oradio is a ZIP (read from the end); a JPEG is read from the start. Prepending the
cover JPEG makes one file that is BOTH a valid image and a valid .oradio. Any content-sniffing viewer
(or a rename to .jpg) shows the cover; our resolver still reads the package. Foreign apps that key off
the .oradio *extension* won't engage without a thumbnail handler — that's a separate, later piece.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
STATIONS = BASE / "stations"
EXPORTS = BASE / "exports"

from loom import radio_os_studio as studio  # reuse the Studio's packaging core (no GUI needed to import)
import oradio_resolver

COVER_ARCNAME = "cover.jpg"


def _load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _basename(ref) -> str:
    try:
        return Path(str(ref)).name or str(ref)
    except Exception:
        return str(ref)


# ---------------------------------------------------------------------------
# Voices: reference-only by default (tiny artifact, club resolves on target)
# ---------------------------------------------------------------------------
def dehydrate_voices(manifest, assets, lock):
    assets = [(src, arc) for (src, arc) in assets
              if not str(arc).replace("\\", "/").startswith("assets/voices/")]
    lock = dict(lock)
    lock["voices"] = [
        {"role": v.get("role", ""), "ref": _basename(v.get("ref", "")), "bundled": False}
        for v in (lock.get("voices") or []) if isinstance(v, dict)
    ]
    manifest = dict(manifest)
    vmap = manifest.get("voices") if isinstance(manifest.get("voices"), dict) else {}
    manifest["voices"] = {role: _basename(val) for role, val in vmap.items()}
    return manifest, assets, lock


# ---------------------------------------------------------------------------
# Cover art + media-style metadata
# ---------------------------------------------------------------------------
def find_station_cover(station_dir: Path):
    for name in ("cover.jpg", "cover.jpeg", "cover.png", "art/cover.jpg", "art/cover.png"):
        p = station_dir / name
        if p.is_file():
            return p
    return None


def make_default_cover(title: str, accent: str = "#66d9ef", bg: str = "#272822") -> bytes | None:
    """A simple branded cover so EVERY .oradio has art ('load a random one, it has a cover')."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    W = H = 512
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, W - 24, H - 24], outline=accent, width=3)
    name = (title or "Radio OS")[:28]
    try:
        d.text((40, H // 2 - 24), name, fill="#f8f8f2")
        d.text((40, H - 64), "RADIO OS", fill=accent)
    except Exception:
        pass
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def build_metadata(manifest, cover: bool) -> dict:
    station = manifest.get("station", {}) if isinstance(manifest.get("station"), dict) else {}
    title = str(station.get("name") or station.get("id") or "Radio OS Station")
    # Media-style keys (a nod to MP3/MP4 metadata — "we're a real format, right here"). Our own
    # surfaces read these; foreign apps won't, but the intent + shape is standard.
    return {
        "title": title,
        "artist": str(manifest.get("author") or station.get("author") or "Radio OS"),
        "album": "Radio OS Stations",
        "genre": str(manifest.get("genre") or "Live Narrated Radio"),
        "comment": str(manifest.get("description") or station.get("tagline") or ""),
        "player": "Radio OS",
        "artwork": COVER_ARCNAME if cover else "",
    }


def finalize(out: Path, cover_bytes, metadata: dict, polyglot: bool) -> None:
    """Re-pack the written .oradio to inject metadata into oradio.json + embed the cover, and
    optionally make the whole file a JPEG+ZIP polyglot. Keeps the Studio packager untouched."""
    with zipfile.ZipFile(out, "r") as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    # inject metadata into the descriptor
    try:
        descriptor = json.loads(members.get("oradio.json", b"{}") or b"{}")
    except Exception:
        descriptor = {}
    descriptor["metadata"] = metadata
    members["oradio.json"] = json.dumps(descriptor, indent=2, ensure_ascii=False).encode("utf-8")
    if cover_bytes:
        members[COVER_ARCNAME] = cover_bytes

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    zip_bytes = buf.getvalue()

    if polyglot and cover_bytes:
        # JPEG first (read from start), ZIP appended (read from end) → one file, both valid.
        out.write_bytes(cover_bytes + zip_bytes)
    else:
        out.write_bytes(zip_bytes)


def bake(station_dir: Path, out: Path, *, bundle_voices: bool = False,
         cover_path: Path | None = None, polyglot: bool = False) -> Path:
    manifest = yaml.safe_load((station_dir / "manifest.yaml").read_text(encoding="utf-8")) or {}
    signature = _load_json(station_dir / "signature.json")
    spec = _load_json(station_dir / "meta_plugin_spec.json")
    assets, lock = studio.collect_station_assets(manifest, station_dir)
    if not bundle_voices:
        manifest, assets, lock = dehydrate_voices(manifest, assets, lock)
    out.parent.mkdir(parents=True, exist_ok=True)
    studio.write_oradio_package(out, manifest, signature=signature, spec=spec, assets=assets, lock=lock, station_dir=station_dir)

    # Cover: explicit > station cover > auto-generated branded cover.
    cover_bytes = None
    src = cover_path or find_station_cover(station_dir)
    if src and Path(src).is_file():
        cover_bytes = Path(src).read_bytes()
    else:
        cover_bytes = make_default_cover(str((manifest.get("station") or {}).get("name") or station_dir.name))
    finalize(out, cover_bytes, build_metadata(manifest, cover=bool(cover_bytes)), polyglot)
    return out


def _report(out: Path) -> None:
    size = out.stat().st_size
    human = f"{size / 1024:.1f} KB" if size < 5 * 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
    print(f"\n  baked: {out}")
    print(f"  size:  {human}")
    try:
        pkg = oradio_resolver.load_oradio(out)
        meta = pkg.get("oradio", {}).get("metadata", {})
        if meta:
            print(f"  cover: {meta.get('artwork') or '(none)'}  ·  title: {meta.get('title')}  ·  genre: {meta.get('genre')}")
        with zipfile.ZipFile(out) as z:
            has_cover = COVER_ARCNAME in z.namelist()
        is_jpeg = out.read_bytes()[:2] == b"\xff\xd8"
        print(f"  cover embedded: {has_cover}  ·  file-is-also-JPEG (polyglot): {is_jpeg}")
        res = oradio_resolver.resolve_station(out)
        print("  " + oradio_resolver.readiness_report(res).replace("\n", "\n  "))
    except Exception as exc:
        print(f"  (post-check skipped: {exc})")


def main(argv) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    bundle_voices = "--bundle-voices" in argv
    polyglot = "--polyglot" in argv
    cover_path = None
    if "--cover" in argv:
        i = argv.index("--cover")
        cover_path = Path(argv[i + 1]) if i + 1 < len(argv) else None
        del argv[i:i + 2]
    argv = [a for a in argv if a not in ("--bundle-voices", "--polyglot")]
    if not argv:
        print(__doc__)
        return 2

    if argv[0] == "--all":
        made = 0
        for station_dir in sorted(p for p in STATIONS.iterdir() if (p / "manifest.yaml").is_file()):
            try:
                _report(bake(station_dir, EXPORTS / f"{station_dir.name}.oradio",
                             bundle_voices=bundle_voices, polyglot=polyglot))
                made += 1
            except Exception as exc:
                print(f"  ! {station_dir.name}: {exc}")
        print(f"\nbaked {made} station(s) into {EXPORTS}")
        return 0

    name = argv[0]
    station_dir = Path(name) if Path(name).is_dir() else STATIONS / name
    if not (station_dir / "manifest.yaml").is_file():
        print(f"No manifest.yaml in {station_dir}", file=sys.stderr)
        return 2
    out = Path(argv[1]) if len(argv) > 1 and not argv[1].startswith("--") else EXPORTS / f"{station_dir.name}.oradio"
    _report(bake(station_dir, out, bundle_voices=bundle_voices, cover_path=cover_path, polyglot=polyglot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
