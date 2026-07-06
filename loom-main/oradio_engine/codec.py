"""Shared Loom query codec helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Sequence

from oradio_engine.packet import CODEC_VERSION, AnswerPacket, CodecManifest

TIMESTAMP_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
TIMESTAMP_PUNCTUATION = {".": 0, ",": 1, "?": 2, "!": 3, ";": 4, ":": 5}
STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "opera": {"scale": "pent", "freqs": [220.00, 261.63, 293.66, 329.63, 392.00, 440.00, 523.25], "dur": 0.32},
    "rap": {"scale": "narrow", "freqs": [220.00, 233.08, 246.94], "dur": 0.17},
    "blues": {"scale": "blues", "freqs": [220.00, 261.63, 293.66, 311.13, 329.63, 392.00, 440.00], "dur": 0.38},
    "country": {"scale": "major", "freqs": [220.00, 246.94, 277.18, 329.63, 369.99, 440.00, 493.88], "dur": 0.30},
    "speak": {"scale": "narrow", "freqs": [220.00, 233.08, 246.94], "dur": 0.105},
    "podcast": {"scale": "narrow", "freqs": [220.00, 233.08, 246.94], "dur": 0.135},
}
TIMING_DEFAULTS: Dict[str, Any] = {
    "cell_ratio": 0.52,
    "space_gap_ms": 210,
    "pair_gap_ms": 170,
    "punct_gap_multiplier": 1.45,
    "punct_rest_multiplier": 3.25,
    "space_rest_multiplier": 2.35,
    "mask_floor": 0.16,
}
CHECKSUM_SPEC: Dict[str, Any] = {"algorithm": "sha256", "length": 12, "field": "render_text"}
VISUAL_RESERVED: Dict[str, Any] = {"planned_codec": "svg_glyph_strip", "enabled": False}


def checksum_text(text: str, *, length: int | None = None) -> str:
    size = int(length or CHECKSUM_SPEC["length"])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def codec_manifest() -> CodecManifest:
    return CodecManifest(
        codec_version=CODEC_VERSION,
        artifact_kind="audio_timestamp_text",
        alphabet=TIMESTAMP_ALPHABET,
        punctuation_map=dict(TIMESTAMP_PUNCTUATION),
        style_presets=json.loads(json.dumps(STYLE_PRESETS)),
        timing=json.loads(json.dumps(TIMING_DEFAULTS)),
        checksum=dict(CHECKSUM_SPEC),
        visual_reserved=dict(VISUAL_RESERVED),
    )


def codec_manifest_dict() -> Dict[str, Any]:
    return codec_manifest().as_dict()


def packet_render_text(packet: AnswerPacket | Dict[str, Any]) -> str:
    if hasattr(packet, "answer_tape"):
        answer_tape = getattr(packet, "answer_tape")
    else:
        answer_tape = (packet or {}).get("answer_tape", [])
    if answer_tape:
        row = answer_tape[0]
        if hasattr(row, "object"):
            return str(getattr(row, "object") or getattr(row, "claim", "") or "").strip()
        return str(row.get("object") or row.get("claim") or "").strip()
    if hasattr(packet, "query"):
        return str(getattr(packet, "query") or "").strip()
    return str((packet or {}).get("query") or "").strip()


def normalized_audio_text(text: str, spec: CodecManifest | Dict[str, Any] | None = None) -> str:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    allowed = set(resolved["alphabet"]) | set(resolved["punctuation_map"]) | {" "}
    out = []
    for ch in str(text or "").lower():
        if ch in allowed:
            out.append(ch)
        elif ch.isspace():
            out.append(" ")
    return " ".join("".join(out).split())


def encode_text(packet: AnswerPacket | Dict[str, Any], spec: CodecManifest | Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    text = packet_render_text(packet)
    return {
        "artifact_kind": "text_receipt",
        "codec_version": resolved["codec_version"],
        "text": text,
        "checksum": checksum_text(text, length=resolved["checksum"]["length"]),
    }


def decode_text(artifact: Dict[str, Any], spec: CodecManifest | Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    text = str((artifact or {}).get("text") or "")
    checksum = str((artifact or {}).get("checksum") or "")
    expected = checksum_text(text, length=resolved["checksum"]["length"])
    return {
        "text": text,
        "checksum": checksum,
        "expected_checksum": expected,
        "checksum_ok": checksum == expected,
        "codec_version": resolved["codec_version"],
    }


def text_to_timestamp_units(text: str, style_key: str = "opera", spec: CodecManifest | Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    presets = resolved["style_presets"]
    style = presets.get(style_key, presets["opera"])
    scale = style["freqs"]
    units: List[Dict[str, Any]] = []
    for ch in str(text or "").lower():
        idx = resolved["alphabet"].find(ch)
        if idx >= 0:
            units.append({"type": "note", "freq": scale[idx // len(scale)], "char": ch, "digit": 0})
            units.append({"type": "note", "freq": scale[idx % len(scale)], "char": ch, "digit": 1})
        elif ch.isspace():
            units.append({"type": "rest", "multiplier": resolved["timing"]["space_rest_multiplier"], "mark": "space"})
        elif ch in resolved["punctuation_map"]:
            units.append({"type": "rest", "multiplier": resolved["timing"]["punct_rest_multiplier"], "mark": "punct"})
            units.append({"type": "note", "freq": scale[resolved["punctuation_map"][ch]], "mark": ch, "digit": "punct"})
    return units


def decode_timestamp_bins(events_or_measurements: Sequence[Dict[str, Any]] | Sequence[int], spec: CodecManifest | Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    alphabet = resolved["alphabet"]
    punct_rev = {v: k for k, v in resolved["punctuation_map"].items()}
    bins: List[Any] = []
    for item in events_or_measurements:
        if isinstance(item, dict):
            if item.get("type") == "space":
                bins.append(" ")
            elif item.get("type") == "punct":
                bins.append(punct_rev.get(int(item.get("bin", 0)), "?"))
            else:
                bins.append(int(item.get("bin", 0)))
        else:
            bins.append(item)
    out: List[str] = []
    pending: int | None = None
    for item in bins:
        if item == " ":
            if pending is not None:
                out.append("?")
                pending = None
            out.append(" ")
            continue
        if isinstance(item, str):
            if pending is not None:
                out.append("?")
                pending = None
            out.append(item)
            continue
        if pending is None:
            pending = int(item)
            continue
        code = pending * 7 + int(item)
        if 0 <= code < len(alphabet):
            out.append(alphabet[code])
        elif code == 26:
            out.append(" ")
        elif code in punct_rev:
            out.append(punct_rev[code])
        else:
            out.append("?")
        pending = None
    if pending is not None:
        out.append("?")
    text = "".join(out)
    return {"text": text, "checksum": checksum_text(text), "codec_version": resolved["codec_version"]}


def encode_audio(packet: AnswerPacket | Dict[str, Any], spec: CodecManifest | Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    playback = {}
    if hasattr(packet, "render_intent"):
        playback = getattr(packet, "render_intent").playback
    elif isinstance(packet, dict):
        playback = ((packet.get("render_intent") or {}).get("playback") or {})
    style = str(playback.get("style") or "opera")
    text = normalized_audio_text(packet_render_text(packet), resolved)
    return {
        "artifact_kind": "audio_timestamp_text",
        "codec_version": resolved["codec_version"],
        "style": style,
        "text": text,
        "checksum": checksum_text(text, length=resolved["checksum"]["length"]),
        "units": text_to_timestamp_units(text, style, resolved),
        "playback": playback,
    }


def decode_audio(events_or_measurements: Any, spec: CodecManifest | Dict[str, Any] | None = None) -> Dict[str, Any]:
    resolved = spec.as_dict() if hasattr(spec, "as_dict") else (spec or codec_manifest_dict())
    if isinstance(events_or_measurements, dict) and "decoded_text" in events_or_measurements:
        text = str(events_or_measurements.get("decoded_text") or "")
        return {
            "text": text,
            "checksum": checksum_text(text, length=resolved["checksum"]["length"]),
            "codec_version": resolved["codec_version"],
        }
    if isinstance(events_or_measurements, dict) and "units" in events_or_measurements:
        bins: List[Dict[str, Any]] = []
        for unit in events_or_measurements.get("units", []):
            if unit.get("type") == "rest" and unit.get("mark") == "space":
                bins.append({"type": "space"})
            elif unit.get("digit") == "punct":
                freq = float(unit.get("freq", 0))
                style = resolved["style_presets"].get(events_or_measurements.get("style") or "opera", resolved["style_presets"]["opera"])
                try:
                    bin_index = style["freqs"].index(freq)
                except ValueError:
                    bin_index = 0
                bins.append({"type": "punct", "bin": bin_index})
            elif unit.get("type") == "note":
                freq = float(unit.get("freq", 0))
                style = resolved["style_presets"].get(events_or_measurements.get("style") or "opera", resolved["style_presets"]["opera"])
                try:
                    bin_index = style["freqs"].index(freq)
                except ValueError:
                    bin_index = 0
                bins.append({"bin": bin_index})
        return decode_timestamp_bins(bins, resolved)
    if isinstance(events_or_measurements, Iterable):
        return decode_timestamp_bins(list(events_or_measurements), resolved)
    return {"text": "", "checksum": "", "codec_version": resolved["codec_version"]}


def js_codec_snippet(var_name: str = "OFFICIAL_CODEC_SPEC") -> str:
    payload = json.dumps(codec_manifest_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"const {var_name}={payload};"
