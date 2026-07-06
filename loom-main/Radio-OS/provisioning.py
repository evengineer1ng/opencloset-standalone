"""
Radio OS — LLM provisioning ("Tune-In" / club membership).

Live, LLM-narrated radio is the medium, not a fallback. A station does not silently
downgrade to deterministic text; instead, the *first* time you open a .oradio on a PC you
set up an LLM provider ONCE, it is saved at the machine level (the shared global config),
and every future station is already tuned in. "Do it once, you're in the club."

This module is the engine for that flow. It is stdlib-only (urllib/subprocess/json) and
standalone-testable, in the same spirit as plugins/antenna_http.py — run it directly to
print the current machine's membership status:

    python provisioning.py

It is NOT the UI and it does NOT edit any preserved runtime file. It reads/writes the same
global config (`%APPDATA%/RadioOS/config.json` on Windows, `~/.radioOS/config.json` else)
that shell_bookmark.py and radio_os_studio.py already share. The Studio wires a "Tune-In"
button to these functions; shell_bookmark's settings tab is left untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Keys mirror the schema shell_bookmark.py writes under "default_models".
KEY_FIELD = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google": "google_api_key",
}


# ----------------------------------------------------------------------------
# Shared global config (same file shell_bookmark.py / radio_os_studio.py use)
# ----------------------------------------------------------------------------
def global_config_path() -> Path:
    if os.name == "nt":
        root = Path(os.getenv("APPDATA", str(Path.home()))) / "RadioOS"
    else:
        root = Path.home() / ".radioOS"
    root.mkdir(parents=True, exist_ok=True)
    return root / "config.json"


def read_global_config() -> Dict[str, Any]:
    path = global_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def write_global_config(cfg: Dict[str, Any]) -> None:
    path = global_config_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Tiny HTTP helper (stdlib only)
# ----------------------------------------------------------------------------
def _http(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 6.0,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted local/user endpoints)
        return resp.status, resp.read()


def _ollama_base(endpoint: str) -> str:
    """Reduce a configured endpoint (e.g. .../api/generate) to its scheme://host:port base."""
    ep = (endpoint or "http://127.0.0.1:11434/api/generate").strip().rstrip("/")
    for suffix in ("/api/generate", "/api/chat", "/api"):
        if ep.endswith(suffix):
            return ep[: -len(suffix)]
    return ep


# ----------------------------------------------------------------------------
# Ollama (local)
# ----------------------------------------------------------------------------
def check_ollama(endpoint: str = "http://127.0.0.1:11434/api/generate", timeout: float = 3.0) -> Dict[str, Any]:
    """Is a local Ollama reachable? What models does it have?"""
    base = _ollama_base(endpoint)
    out: Dict[str, Any] = {"available": False, "base": base, "models": [], "error": None}
    try:
        status, body = _http(f"{base}/api/tags", timeout=timeout)
        if status == 200:
            data = json.loads(body or b"{}")
            out["available"] = True
            out["models"] = sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
        else:
            out["error"] = f"HTTP {status}"
    except urllib.error.URLError as e:
        out["error"] = f"not reachable ({getattr(e, 'reason', e)})"
    except Exception as e:  # pragma: no cover - defensive
        out["error"] = str(e)
    out["binary"] = shutil.which("ollama") or ""
    return out


def ollama_has_model(endpoint: str, model: str) -> bool:
    if not model:
        return False
    info = check_ollama(endpoint)
    names = info.get("models", [])
    # Tolerate ":latest" elision (e.g. "rnj-1:8b" vs "rnj-1:8b").
    return any(n == model or n.split(":")[0] == model.split(":")[0] and model.split(":")[-1] in n for n in names)


def pull_ollama_model(
    endpoint: str,
    model: str,
    on_progress: Optional[Callable[[str], None]] = None,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Stream `ollama pull` via the HTTP API, reporting progress lines. Falls back to CLI."""
    base = _ollama_base(endpoint)
    payload = json.dumps({"name": model, "stream": True}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{base}/api/pull", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    status = msg.get("status", "")
                    if msg.get("total") and msg.get("completed") is not None:
                        pct = 100.0 * msg["completed"] / max(1, msg["total"])
                        status = f"{status} {pct:.0f}%"
                    if on_progress and status:
                        on_progress(status)
                    if msg.get("error"):
                        return {"ok": False, "error": msg["error"]}
                except json.JSONDecodeError:
                    if on_progress:
                        on_progress(line)
        return {"ok": ollama_has_model(endpoint, model), "error": None}
    except Exception as e:
        # CLI fallback (if the binary is present)
        if shutil.which("ollama"):
            try:
                if on_progress:
                    on_progress(f"pulling {model} via CLI…")
                subprocess.run(["ollama", "pull", model], check=True, timeout=timeout)
                return {"ok": True, "error": None}
            except Exception as e2:
                return {"ok": False, "error": f"{e2}"}
        return {"ok": False, "error": f"{e}"}


# ----------------------------------------------------------------------------
# Hosted API providers — minimal, real validation calls
# ----------------------------------------------------------------------------
def validate_openai(key: str, timeout: float = 8.0) -> Dict[str, Any]:
    if not key:
        return {"ok": False, "error": "no API key"}
    try:
        status, body = _http(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
        )
        return {"ok": status == 200, "error": None if status == 200 else f"HTTP {status}"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} (key rejected)" if e.code in (401, 403) else f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def validate_google(key: str, timeout: float = 8.0) -> Dict[str, Any]:
    if not key:
        return {"ok": False, "error": "no API key"}
    try:
        status, _ = _http(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=timeout)
        return {"ok": status == 200, "error": None if status == 200 else f"HTTP {status}"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} (key rejected)" if e.code in (400, 401, 403) else f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def validate_anthropic(key: str, model: str = "claude-3-5-haiku-latest", timeout: float = 10.0) -> Dict[str, Any]:
    # Anthropic has no free list endpoint; a 1-token message is the cheapest real check.
    if not key:
        return {"ok": False, "error": "no API key"}
    payload = json.dumps({"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}).encode()
    try:
        status, _ = _http(
            "https://api.anthropic.com/v1/messages", method="POST", data=payload,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            timeout=timeout,
        )
        return {"ok": status == 200, "error": None if status == 200 else f"HTTP {status}"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code} (key rejected)" if e.code in (401, 403) else f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ----------------------------------------------------------------------------
# Unified status + membership write
# ----------------------------------------------------------------------------
def validate_provider(provider: str, *, endpoint: str = "", key: str = "", model: str = "") -> Dict[str, Any]:
    provider = (provider or "ollama").lower()
    if provider == "ollama":
        info = check_ollama(endpoint)
        if not info["available"]:
            return {"ok": False, "error": info["error"] or "Ollama not running", "detail": info}
        if model and not ollama_has_model(endpoint, model):
            return {"ok": False, "error": f"model '{model}' not pulled", "detail": info, "needs_pull": True}
        return {"ok": True, "error": None, "detail": info}
    if provider == "openai":
        return validate_openai(key)
    if provider == "google":
        return validate_google(key)
    if provider == "anthropic":
        return validate_anthropic(key, model or "claude-3-5-haiku-latest")
    return {"ok": False, "error": f"unknown provider '{provider}'"}


def provisioning_status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Are we in the club? Reads the saved provider from the shared global config."""
    cfg = cfg if cfg is not None else read_global_config()
    dm = cfg.get("default_models", {}) if isinstance(cfg.get("default_models"), dict) else {}
    provider = (dm.get("provider") or "ollama").lower()
    endpoint = dm.get("llm_endpoint", "http://127.0.0.1:11434/api/generate")
    key = dm.get(KEY_FIELD.get(provider, ""), "") if provider in KEY_FIELD else ""
    model = dm.get("host_model") or dm.get("producer_model") or ""
    result = validate_provider(provider, endpoint=endpoint, key=key, model=model)
    return {
        "provider": provider,
        "endpoint": endpoint,
        "model": model,
        "ready": bool(result.get("ok")),
        "error": result.get("error"),
        "needs_pull": bool(result.get("needs_pull")),
        "detail": result.get("detail"),
    }


def save_llm_membership(
    provider: str,
    *,
    endpoint: Optional[str] = None,
    key: Optional[str] = None,
    host_model: Optional[str] = None,
    producer_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist the validated provider at the machine level — this is joining the club."""
    cfg = read_global_config()
    dm = cfg.get("default_models", {}) if isinstance(cfg.get("default_models"), dict) else {}
    dm["provider"] = (provider or "ollama").lower()
    if endpoint is not None:
        dm["llm_endpoint"] = endpoint
    if key is not None and dm["provider"] in KEY_FIELD:
        dm[KEY_FIELD[dm["provider"]]] = key
    if host_model is not None:
        dm["host_model"] = host_model
    if producer_model is not None:
        dm["producer_model"] = producer_model
    cfg["default_models"] = dm
    write_global_config(cfg)
    return cfg


# ----------------------------------------------------------------------------
# Asset club — voices & Piper remembered machine-level (ask ONCE, reuse forever)
# ----------------------------------------------------------------------------
# Same "club" principle as the LLM: the first time a .oradio can't find your voice models, we ask
# once — earnestly — where they are, remember it machine-level, and every future station reuses it.
# We only ask again if the remembered location genuinely goes missing.
VOICE_SUFFIXES = (".onnx", ".pt", ".pth")


def _assets(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("assets", {}) if isinstance(cfg.get("assets"), dict) else {}


def voice_files_in(path: Any) -> int:
    """Count likely voice-model files under a directory (lenient; .onnx etc., recursive)."""
    p = Path(path)
    if not p.is_dir():
        return 0
    return sum(sum(1 for _ in p.rglob(f"*{suf}")) for suf in VOICE_SUFFIXES)


def get_voices_dirs() -> List[str]:
    """Machine-level voice directories the user has shown us before (existing ones only)."""
    dirs = _assets(read_global_config()).get("voices_dirs", [])
    return [d for d in dirs if isinstance(d, str) and Path(d).is_dir()]


def save_voices_dir(path: Any) -> Dict[str, Any]:
    """Remember a voices directory machine-level (joining the asset club). Most-recent-first, deduped."""
    p = Path(path)
    if not p.is_dir():
        return {"ok": False, "error": f"not a folder: {path}"}
    cfg = read_global_config()
    assets = _assets(cfg)
    dirs = [d for d in assets.get("voices_dirs", []) if isinstance(d, str) and d != str(p)]
    dirs.insert(0, str(p))
    assets["voices_dirs"] = dirs[:8]
    cfg["assets"] = assets
    write_global_config(cfg)
    return {"ok": True, "voices_dirs": assets["voices_dirs"], "voice_files": voice_files_in(p)}


def get_piper_bin() -> str:
    """A remembered Piper binary, if it still exists; else empty."""
    binp = _assets(read_global_config()).get("piper_bin", "")
    return binp if isinstance(binp, str) and binp and Path(binp).is_file() else ""


def save_piper_bin(path: Any) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    cfg = read_global_config()
    assets = _assets(cfg)
    assets["piper_bin"] = str(p)
    cfg["assets"] = assets
    write_global_config(cfg)
    return {"ok": True, "piper_bin": str(p)}


def assets_summary() -> str:
    dirs = get_voices_dirs()
    if dirs:
        files = voice_files_in(dirs[0])
        return f"Voices remembered ✓  ({dirs[0]}{f' · {files} files' if files else ''})"
    return "No voices location remembered yet — we'll ask once when a station needs them."


# ----------------------------------------------------------------------------
# Antenna targets — remembered machine-level (the SAME club, for what a station listens to)
# ----------------------------------------------------------------------------
# A station's antenna points at something on THIS machine — a game folder, a log, a port. We try to
# find it automatically; if we can't, we ask once and remember it here, keyed by a stable antenna key
# (so paths can differ per machine and a re-shared .oradio still "just works" after one pointing).
def get_antenna_targets() -> Dict[str, str]:
    targets = _assets(read_global_config()).get("antenna_targets", {})
    return {k: v for k, v in targets.items() if isinstance(v, str)} if isinstance(targets, dict) else {}


def get_antenna_target(key: str) -> str:
    """A remembered target for an antenna key, if the path still exists; else empty."""
    val = get_antenna_targets().get(key, "")
    return val if val and Path(val).exists() else ""


def save_antenna_target(key: str, path: Any) -> Dict[str, Any]:
    """Remember where an antenna's target lives on this machine (joining the antenna club)."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"path does not exist: {path}"}
    cfg = read_global_config()
    assets = _assets(cfg)
    targets = assets.get("antenna_targets", {}) if isinstance(assets.get("antenna_targets"), dict) else {}
    targets[str(key)] = str(p)
    assets["antenna_targets"] = targets
    cfg["assets"] = assets
    write_global_config(cfg)
    return {"ok": True, "key": str(key), "path": str(p)}


def membership_summary() -> str:
    s = provisioning_status()
    if s["ready"]:
        tail = f" · {s['model']}" if s["model"] else ""
        return f"In the club ✓  ({s['provider']}{tail})"
    hint = {
        "ollama": "start Ollama and pull a model",
        "openai": "add a valid OpenAI API key",
        "anthropic": "add a valid Anthropic API key",
        "google": "add a valid Google API key",
    }.get(s["provider"], "set up an LLM provider")
    return f"Not tuned in ✗  ({s['provider']}: {s['error'] or 'not configured'} — {hint})"


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default
    except Exception:
        pass
    print("Radio OS — provisioning / club-membership status")
    print("  config:", global_config_path())
    print(" ", membership_summary())
    oll = check_ollama()
    print("  ollama:", "up" if oll["available"] else f"down ({oll['error']})",
          "| binary:", oll["binary"] or "not found",
          "| models:", ", ".join(oll["models"]) or "none")
