# Process tools — exec, process
#
# Not a thin subprocess.run wrapper: foreground exec with timeout,
# background process handles, output capture, kill, and diagnostics.
#
# Process subsystem:
#   - Foreground exec: run command, capture output, return result
#   - Background exec: spawn process, return handle (session id)
#   - Process management: log, write (stdin), kill, poll status

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from api.tools.registry import (
    ToolContract,
    build_tool,
    ValidationResult,
    CATEGORY_CORE,
    PermissionDecision,
)
from api.tools.filesystem import is_path_within_scope, normalize_allowed_paths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

# Maximum output capture size (256 KB)
MAX_OUTPUT_BYTES = 256 * 1024

# Maximum foreground exec timeout (30 minutes)
MAX_EXEC_TIMEOUT = 30 * 60

# Default foreground timeout (5 minutes)
DEFAULT_EXEC_TIMEOUT = 300

# Maximum background processes per session
MAX_BACKGROUND_PROCESSES = 20

# Seconds of no new output before suspecting interactive prompt
_STALL_WARN_SECONDS = 15

# Substrings that suggest a process is waiting for interactive input
_PROMPT_PATTERNS = (
    "enter passphrase",
    "enter password",
    "password:",
    "username for",
    "are you sure you want to continue",
    "(yes/no)",
    "(y/n)",
    "confirm:",
    "token:",
    "host key verification failed",
    "remote host identification has changed",
)

_NONINTERACTIVE_GIT_ENV_DEFAULTS = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GH_PROMPT_DISABLED": "1",
    "SSH_ASKPASS_REQUIRE": "never",
}

_INTERACTIVE_GIT_ENV_DEFAULTS = {
    "GCM_INTERACTIVE": "never",
    "GH_PROMPT_DISABLED": "1",
    "GIT_PROGRESS_DELAY": "0",
}

_AUTO_INTERACTIVE_EXEC_RE = re.compile(r"^\s*git\s+push\b", re.IGNORECASE)
_WINDOWS_CD_CHAIN_RE = re.compile(r"^\s*cd\s+(?P<path>.+?)\s+&&\s+(?P<rest>.+?)\s*$", re.IGNORECASE)


def _looks_like_prompt(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in _PROMPT_PATTERNS)


def _looks_like_git_or_gh_command(command: str, shell_cmd: list[str]) -> bool:
    haystack = " ".join([command, *shell_cmd]).lower()
    return "git " in haystack or haystack.strip().startswith("git") or " gh " in haystack or haystack.strip().startswith("gh")


def _should_force_interactive_exec(command: str, shell_cmd: list[str]) -> bool:
    primary = (command or "").strip()
    if primary and _AUTO_INTERACTIVE_EXEC_RE.search(primary):
        return True

    for token in shell_cmd:
        if isinstance(token, str) and _AUTO_INTERACTIVE_EXEC_RE.search(token):
            return True
    return False


def _augment_git_push_command(command: str) -> str:
    normalized = (command or "").strip()
    if not normalized or not _AUTO_INTERACTIVE_EXEC_RE.search(normalized):
        return command
    if re.search(r"(?:^|\s)--progress(?:\s|$)", normalized, re.IGNORECASE):
        return command
    return f"{command} --progress"


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalize_windows_shell_command(command: str) -> str:
    if sys.platform != "win32" or "&&" not in command:
        return command

    match = _WINDOWS_CD_CHAIN_RE.match(command)
    if match:
        raw_path = str(match.group("path") or "").strip()
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {"'", '"'}:
            raw_path = raw_path[1:-1]
        rest = str(match.group("rest") or "").strip()
        if raw_path and rest:
            return f"Set-Location -LiteralPath {_powershell_single_quote(raw_path)}; {rest}"

    return re.sub(r"\s*&&\s*", "; ", command)


def _reset_git_config_env(env: dict[str, str]) -> None:
    stale_keys = [key for key in env if key == "GIT_CONFIG_COUNT" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_")]
    for key in stale_keys:
        env.pop(key, None)


def _set_git_config_entries(env: dict[str, str], entries: list[tuple[str, str]]) -> None:
    _reset_git_config_env(env)
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value


def _apply_interactive_git_env(env: dict[str, str], command: str, shell_cmd: list[str]) -> None:
    if not _looks_like_git_or_gh_command(command, shell_cmd):
        return

    for key, value in _INTERACTIVE_GIT_ENV_DEFAULTS.items():
        env.setdefault(key, value)

    helper_entries: list[tuple[str, str]] = [("credential.helper", "")]
    if shutil.which("gh", path=env.get("PATH")):
        helper_entries.append(("credential.helper", "!gh auth git-credential"))
    helper_entries.append(("credential.helper", "store"))
    _set_git_config_entries(env, helper_entries)


# ---------------------------------------------------------------------------
# Process handle — tracks background processes
# ---------------------------------------------------------------------------


@dataclass
class ProcessHandle:
    """Track a background process for later management."""
    session_id: str
    command: str
    workdir: str | None
    pid: int | None
    start_time: float
    env: dict[str, str] | None
    return_code: int | None = None
    terminated: bool = False
    output_path: str | None = None  # temp file for output capture
    script_path: str | None = None
    interactive: bool = False
    owner_session_id: str = ""
    owner_run_id: str = ""


# ---------------------------------------------------------------------------
# Process store — in-memory registry of background processes
# ---------------------------------------------------------------------------


class ProcessStore:
    """In-memory store for background process handles.

    Keyed by session_id. One process store per runtime/session.
    """

    def __init__(self, *, on_terminated: Callable[[ProcessHandle, str], None] | None = None):
        self._processes: dict[str, ProcessHandle] = {}
        self._popen: dict[str, subprocess.Popen] = {}
        self._on_terminated = on_terminated

    def set_termination_callback(self, callback: Callable[[ProcessHandle, str], None] | None) -> None:
        self._on_terminated = callback

    def register(self, handle: ProcessHandle, popen: subprocess.Popen) -> str:
        """Register a background process. Returns session_id."""
        if len(self._processes) >= MAX_BACKGROUND_PROCESSES:
            raise RuntimeError(
                f"Max background processes ({MAX_BACKGROUND_PROCESSES}) reached. "
                "Kill a process before starting a new one."
            )
        self._processes[handle.session_id] = handle
        self._popen[handle.session_id] = popen
        return handle.session_id

    def get(self, session_id: str) -> ProcessHandle | None:
        return self._processes.get(session_id)

    def get_popen(self, session_id: str) -> subprocess.Popen | None:
        return self._popen.get(session_id)

    def list_all(self) -> list[ProcessHandle]:
        return list(self._processes.values())

    def terminate(self, session_id: str, *, reason: str = "kill") -> bool:
        """Kill a background process."""
        popen = self._popen.get(session_id)
        handle = self._processes.get(session_id)
        if not popen or not handle:
            return False

        was_terminated = bool(handle.terminated)

        try:
            popen.terminate()
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            popen.kill()
            popen.wait(timeout=3)
        except OSError:
            pass  # already dead

        handle.return_code = popen.returncode
        handle.terminated = True
        if not was_terminated and self._on_terminated is not None:
            try:
                self._on_terminated(handle, reason)
            except Exception:
                logger.exception("Process termination callback failed for %s", session_id)
        return True

    def cleanup(self, session_id: str) -> None:
        """Remove process from store after completion."""
        handle = self._processes.get(session_id)
        if handle:
            for temp_path in (handle.output_path, handle.script_path):
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        self._processes.pop(session_id, None)
        self._popen.pop(session_id, None)


# Global default process store (for simple usage without per-session stores)
_default_store: ProcessStore | None = None


def get_default_store() -> ProcessStore:
    """Get or create the default global process store."""
    global _default_store
    if _default_store is None:
        _default_store = ProcessStore()
    return _default_store


# ---------------------------------------------------------------------------
# Semantic validators
# ---------------------------------------------------------------------------


def validate_exec_input(data: dict[str, Any]) -> ValidationResult:
    """Semantic validation for 'exec' tool."""
    content_fields = [
        field_name
        for field_name in ("command", "script_content", "script_content_base64")
        if isinstance(data.get(field_name), str) and data.get(field_name).strip()
    ]
    if not content_fields:
        return ValidationResult(valid=False, errors=["Provide command, script_content, or script_content_base64"])
    if len(content_fields) > 1:
        return ValidationResult(valid=False, errors=["Provide exactly one of command, script_content, or script_content_base64"])

    if "script_content_base64" in content_fields:
        try:
            base64.b64decode("".join(str(data.get("script_content_base64", "")).split()), validate=True)
        except (binascii.Error, ValueError):
            return ValidationResult(valid=False, errors=["script_content_base64 must be valid base64"])

    timeout = data.get("timeout", None)
    if timeout is not None:
        if timeout <= 0:
            return ValidationResult(
                valid=False,
                errors=["timeout must be positive"],
            )
        if timeout > MAX_EXEC_TIMEOUT:
            return ValidationResult(
                valid=False,
                errors=[
                    f"timeout exceeds maximum ({MAX_EXEC_TIMEOUT}s). "
                    "For long-running work, use background=True instead."
                ],
            )

    return ValidationResult(valid=True)


def validate_process_input(data: dict[str, Any]) -> ValidationResult:
    """Semantic validation for 'process' tool."""
    action = data.get("action", "")
    if not action:
        return ValidationResult(valid=False, errors=["Empty action"])

    valid_actions = {"list", "poll", "log", "write", "send-keys", "kill"}
    if action not in valid_actions:
        return ValidationResult(
            valid=False,
            errors=[
                f"Invalid action '{action}'. "
                f"Valid actions: {', '.join(sorted(valid_actions))}"
            ],
        )

    # list doesn't need session_id
    if action != "list" and not data.get("sessionId"):
        return ValidationResult(
            valid=False,
            errors=["sessionId required for this action"],
        )

    return ValidationResult(valid=True)


def normalize_workdir(raw_workdir: str | None, *, default_workdir: str | None = None) -> str | None:
    if raw_workdir:
        candidate = Path(raw_workdir)
        if not candidate.is_absolute() and default_workdir:
            candidate = Path(default_workdir) / candidate
        return str(candidate.resolve())
    if default_workdir:
        return str(Path(default_workdir).resolve())
    return None


def _build_platform_shell_command(command: str) -> list[str]:
    if sys.platform == "win32":
        shell_executable = (
            shutil.which("pwsh")
            or shutil.which("powershell")
            or shutil.which("powershell.exe")
            or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        )
        return [shell_executable, "-NoProfile", "-Command", command]
    return ["sh", "-c", command]


def _default_script_runner() -> str:
    return "powershell" if sys.platform == "win32" else "sh"


def _normalize_runner_name(runner: str | None) -> tuple[str, str]:
    raw = (runner or _default_script_runner()).strip()
    if not raw:
        raw = _default_script_runner()

    lowered = raw.lower()
    executable_name = Path(raw).name.lower()

    if executable_name in {"pwsh.exe", "pwsh"}:
        return raw, "pwsh"
    if executable_name in {"powershell.exe", "powershell"}:
        return raw, "powershell"
    if executable_name in {"cmd.exe", "cmd"}:
        return raw, "cmd"
    if executable_name in {"python.exe", "python", "py.exe", "py"}:
        return raw, "python"
    if executable_name in {"bash.exe", "bash"}:
        return raw, "bash"
    if executable_name in {"sh.exe", "sh"}:
        return raw, "sh"
    if executable_name in {"node.exe", "node"}:
        return raw, "node"
    return raw, lowered


def _script_suffix_for_runner(runner: str | None) -> str:
    _, runner_kind = _normalize_runner_name(runner)
    if runner_kind in {"powershell", "pwsh", "shell"}:
        return ".ps1" if sys.platform == "win32" else ".sh"
    if runner_kind in {"sh", "bash"}:
        return ".sh"
    if runner_kind == "python":
        return ".py"
    if runner_kind == "node":
        return ".js"
    if runner_kind == "cmd":
        return ".cmd"
    return ".tmp"


def _build_script_runner_command(script_path: str, runner: str | None = None) -> list[str]:
    normalized, runner_kind = _normalize_runner_name(runner)
    if runner_kind in {"shell", "powershell", "pwsh"}:
        if sys.platform == "win32":
            shell_executable = normalized if runner else (
                shutil.which("pwsh")
                or shutil.which("powershell")
                or shutil.which("powershell.exe")
                or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            )
            return [shell_executable, "-NoProfile", "-File", script_path]
        return ["sh", script_path]
    if runner_kind in {"sh", "bash"}:
        executable = shutil.which(normalized) or normalized
        return [executable, script_path]
    if runner_kind == "cmd":
        executable = shutil.which(normalized) or normalized
        return [executable, "/c", script_path]
    if runner_kind == "python":
        executable = shutil.which(normalized) or sys.executable or normalized
        return [executable, script_path]
    if runner_kind == "node":
        executable = shutil.which(normalized) or normalized
        return [executable, script_path]
    return [normalized, script_path]


def _resolve_exec_script(args: dict[str, Any]) -> tuple[list[str], str | None, str | None]:
    script_content = args.get("script_content")
    if isinstance(script_content, str) and script_content.strip():
        runner = args.get("runner") if isinstance(args.get("runner"), str) else None
        return _build_script_runner_command("__SCRIPT_PATH__", runner), script_content, runner

    encoded = args.get("script_content_base64")
    if isinstance(encoded, str) and encoded.strip():
        runner = args.get("runner") if isinstance(args.get("runner"), str) else None
        decoded = base64.b64decode("".join(encoded.split()), validate=True).decode("utf-8")
        return _build_script_runner_command("__SCRIPT_PATH__", runner), decoded, runner

    command = str(args.get("command", "") or "")
    if "\n" in command or "\r" in command:
        runner = args.get("runner") if isinstance(args.get("runner"), str) else None
        return _build_script_runner_command("__SCRIPT_PATH__", runner), command, runner

    return _build_platform_shell_command(command), None, None


def _materialize_script(script_content: str, *, runner: str | None = None, cwd: str | None = None) -> str:
    suffix = _script_suffix_for_runner(runner)
    fd, script_path = tempfile.mkstemp(prefix="exec_script_", suffix=suffix, dir=cwd)
    os.close(fd)
    with open(script_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(script_content)
    return script_path


_REDIRECTION_TOKENS = {">", ">>", "<", "1>", "1>>", "2>", "2>>"}


def _looks_like_path_token(token: str, *, previous_token: str | None = None) -> bool:
    if not token or token in _REDIRECTION_TOKENS:
        return False
    if "://" in token:
        return False
    if previous_token in _REDIRECTION_TOKENS:
        return True
    if token.startswith(("./", "../", ".\\", "..\\", "~/", "~\\")):
        return True
    # Bare /x and \x tokens are Windows command flags (e.g. /n, /c, \q), not paths.
    # Only treat /... or \... as a path when it contains at least one more separator.
    if token.startswith("/") and ("/" in token[1:] or "\\" in token[1:]):
        return True
    if token.startswith("\\") and ("\\" in token[1:] or "/" in token[1:]):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    # Separator must appear after the first character so bare /flag tokens don't match
    return ("/" in token[1:]) or ("\\" in token[1:])


def extract_command_path_candidates(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=(sys.platform != "win32"))
    except ValueError:
        tokens = command.split()

    candidates: list[str] = []
    previous_token: str | None = None
    for token in tokens:
        stripped = token.strip().strip('"').strip("'")
        if not stripped:
            previous_token = stripped or previous_token
            continue
        if _looks_like_path_token(stripped, previous_token=previous_token):
            candidates.append(stripped)
        previous_token = stripped
    return candidates


def normalize_command_paths(command: str, *, default_workdir: str | None = None) -> list[str]:
    base_dir = normalize_workdir(None, default_workdir=default_workdir)
    normalized: list[str] = []
    for candidate in extract_command_path_candidates(command):
        path_obj = Path(candidate).expanduser()
        if not path_obj.is_absolute() and base_dir:
            path_obj = Path(base_dir) / path_obj
        normalized.append(str(path_obj.resolve()))
    return normalized


def make_exec_permission_check(
    *,
    workdir: str | None = None,
    allowed_paths: list[str] | None = None,
):
    normalized_allowed_paths = normalize_allowed_paths(allowed_paths, workspace=workdir)

    def permission_check(*, input_data: dict[str, Any], **_: Any) -> PermissionDecision:
        if not normalized_allowed_paths:
            return PermissionDecision.ALLOW
        resolved_workdir = normalize_workdir(input_data.get("workdir"), default_workdir=workdir)
        if resolved_workdir is None:
            return PermissionDecision.DENY
        if not is_path_within_scope(resolved_workdir, normalized_allowed_paths):
            return PermissionDecision.DENY
        return PermissionDecision.ALLOW

    return permission_check


# ---------------------------------------------------------------------------
# Execute functions
# ---------------------------------------------------------------------------


def exec_exec(
    args: dict[str, Any],
    *,
    store: ProcessStore | None = None,
    workdir: str | None = None,
) -> str:
    """Execute a shell command.

    Modes:
      - Foreground (default): run to completion, capture output, return result
      - Background: spawn process, return session handle for later management

    Args:
        args: {
            "command": str,
            "workdir": str (optional),
            "timeout": int (seconds, optional),
            "background": bool (optional),
            "env": dict (optional environment overrides),
        }
        store: ProcessStore instance for background processes.
        workdir: Default working directory for relative paths.

    Returns:
        Output text or background session info.
    """
    global _default_store
    if store is None:
        store = get_default_store()

    command = str(args.get("command", "") or "")
    cmd_workdir = args.get("workdir", workdir)
    timeout = args.get("timeout", DEFAULT_EXEC_TIMEOUT)
    background = args.get("background", False)
    interactive = bool(args.get("interactive", False))
    env_overrides = args.get("env", None)

    if interactive:
        background = True

    # Build environment
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    # Resolve workdir
    cwd = normalize_workdir(cmd_workdir, default_workdir=workdir)

    if _AUTO_INTERACTIVE_EXEC_RE.search(command or ""):
        command = _augment_git_push_command(command)
    command = _normalize_windows_shell_command(command)

    exec_args = dict(args)
    if command != str(args.get("command", "") or ""):
        exec_args["command"] = command

    try:
        shell_cmd, script_content, script_runner = _resolve_exec_script(exec_args)
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        return f"Error executing command: invalid script content ({e})"

    script_path: str | None = None
    if script_content is not None:
        try:
            script_path = _materialize_script(script_content, runner=script_runner, cwd=cwd)
        except OSError as e:
            return f"Error executing command: could not create temp script ({e})"
        shell_cmd = [script_path if part == "__SCRIPT_PATH__" else part for part in shell_cmd]

    if not background and not interactive and _should_force_interactive_exec(command, shell_cmd):
        interactive = True
        background = True

    if interactive:
        _apply_interactive_git_env(env, command, shell_cmd)
    elif _looks_like_git_or_gh_command(command, shell_cmd):
        for key, value in _NONINTERACTIVE_GIT_ENV_DEFAULTS.items():
            env.setdefault(key, value)

    # -----------------------------------------------------------------------
    # Background mode
    # -----------------------------------------------------------------------
    if background:
        session_id = uuid.uuid4().hex[:12]

        # Create temp file for output capture
        output_fd, output_path = tempfile.mkstemp(
            prefix=f"proc_{session_id}_",
            suffix=".log",
        )
        os.close(output_fd)

        output_stream = None
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            output_stream = open(output_path, "w", encoding="utf-8", errors="replace", buffering=1)
            popen = subprocess.Popen(
                shell_cmd,
                shell=False,
                cwd=cwd,
                env=env,
                stdout=output_stream,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=None,
                creationflags=creationflags,
            )
        except OSError as e:
            if output_stream is not None:
                output_stream.close()
            if script_path and os.path.exists(script_path):
                os.unlink(script_path)
            os.unlink(output_path)
            return f"Error spawning process: {e}"
        finally:
            if output_stream is not None:
                output_stream.close()

        handle = ProcessHandle(
            session_id=session_id,
            command=command,
            workdir=cwd,
            pid=popen.pid,
            start_time=time.time(),
            env=env_overrides,
            output_path=output_path,
            script_path=script_path,
            interactive=interactive,
            owner_session_id=str(args.get("_runtime_session_id") or ""),
            owner_run_id=str(args.get("_run_id") or ""),
        )
        store.register(handle, popen)

        start_label = "Interactive process started." if interactive else "Background process started."
        guidance_lines = [
            f"{start_label}",
            f"session_id: {session_id}\n"
            f"pid: {popen.pid}\n"
            f"command: {command}\n"
            f"workdir: {cwd or os.getcwd()}"
        ]
        if interactive:
            guidance_lines.append(
                "Use 'process' with action=log for live output, action=write for text input, "
                "action=send-keys for Enter/Tab/Escape/Ctrl+C, and action=kill to terminate."
            )
        else:
            guidance_lines.append(
                "Use 'process' tool with action=poll/log/kill to manage. "
                "Background mode is non-interactive: stdin is closed and git/gh prompts are disabled so blocked credential flows fail fast instead of hanging."
            )
        return "\n".join(guidance_lines)

    # -----------------------------------------------------------------------
    # Foreground mode — streaming with stall/prompt detection
    # -----------------------------------------------------------------------
    # Uses Popen + a reader thread so we can:
    #   - detect stalls (process alive but producing no output → likely awaiting input)
    #   - surface partial output on timeout instead of returning nothing
    #   - always report elapsed time
    #   - detect common interactive-prompt patterns and explain the fix
    start_time = time.time()
    output_chunks: list[str] = []
    output_lock = threading.Lock()
    reader_done = threading.Event()

    try:
        proc = subprocess.Popen(
            shell_cmd,
            shell=False,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        if script_path and os.path.exists(script_path):
            try:
                os.unlink(script_path)
            except OSError:
                pass
        return f"Error executing command: {e}"

    def _reader() -> None:
        try:
            for line in proc.stdout:
                with output_lock:
                    output_chunks.append(line)
        finally:
            reader_done.set()

    threading.Thread(target=_reader, daemon=True).start()

    last_chunk_count = 0
    last_output_time = start_time
    stall_suspected = False
    prompt_hint = ""

    try:
        while True:
            now = time.time()
            elapsed = now - start_time

            if proc.poll() is not None:
                reader_done.wait(timeout=5)
                break

            if elapsed >= timeout:
                proc.kill()
                proc.wait()
                reader_done.wait(timeout=2)
                with output_lock:
                    partial = "".join(output_chunks)
                if len(partial) > MAX_OUTPUT_BYTES:
                    partial = partial[:MAX_OUTPUT_BYTES] + "\n[TRUNCATED]"
                stall_secs = now - last_output_time
                result_lines = [
                    f"Command timed out after {timeout}s.",
                    f"command: {command}",
                    f"elapsed: {elapsed:.1f}s",
                ]
                if stall_suspected:
                    result_lines.append(
                        f"Process produced no output for {stall_secs:.0f}s — "
                        "likely waiting for interactive input that can never arrive "
                        "(stdin is closed in exec foreground mode)."
                    )
                if prompt_hint:
                    result_lines.append(f"Possible prompt in output: {prompt_hint!r}")
                    result_lines.append(
                        "Fix: pre-configure credentials so no prompt appears. "
                        "For git/SSH: run `ssh-add` to load your key into the SSH agent, "
                        "or set GIT_TERMINAL_PROMPT=0 and configure git credential.helper. "
                        "For interactive confirmations: pass -y / --yes / --force flags."
                    )
                elif stall_suspected:
                    result_lines.append(
                        "If this command requires a password or confirmation, "
                        "pre-configure it (SSH agent, credential helper, -y flag) "
                        "so it runs without prompts."
                    )
                result_lines.append(
                    "For commands that legitimately take longer, use background=True "
                    "and monitor with the process tool (poll/log/kill)."
                )
                if partial.strip():
                    result_lines.append(f"Partial output captured:\n{partial}")
                else:
                    result_lines.append("No output was captured before timeout.")
                return "\n".join(result_lines)

            # Stall detection: no new output chunks for _STALL_WARN_SECONDS
            with output_lock:
                current_count = len(output_chunks)
                recent_text = "".join(output_chunks[-30:]) if output_chunks else ""

            if current_count > last_chunk_count:
                last_chunk_count = current_count
                last_output_time = now
                stall_suspected = False
                prompt_hint = ""
            else:
                stall_secs = now - last_output_time
                if stall_secs >= _STALL_WARN_SECONDS and not stall_suspected:
                    stall_suspected = True
                    if _looks_like_prompt(recent_text):
                        prompt_hint = recent_text.strip()[-300:]

            time.sleep(0.2)
    finally:
        if script_path and os.path.exists(script_path):
            try:
                os.unlink(script_path)
            except OSError:
                pass

    elapsed = time.time() - start_time
    with output_lock:
        output = "".join(output_chunks)

    if len(output) > MAX_OUTPUT_BYTES:
        output = (
            output[:MAX_OUTPUT_BYTES]
            + f"\n\n--- [TRUNCATED: output exceeds {MAX_OUTPUT_BYTES // 1024} KB] ---"
        )

    result_lines = [
        f"Exit code: {proc.returncode}",
        f"Elapsed: {elapsed:.2f}s",
    ]
    if output.strip():
        result_lines.append(f"Output:\n{output}")
    elif proc.returncode != 0:
        result_lines.append(
            "No output captured (command produced no stdout/stderr)."
        )
    return "\n".join(result_lines)


def exec_process(
    args: dict[str, Any],
    *,
    store: ProcessStore | None = None,
) -> str:
    """Manage background processes.

    Actions:
      - list: List all background processes
      - poll: Check process status (poll for completion)
      - log: Read captured output
      - write: Send data to stdin
      - kill: Terminate process

    Args:
        args: {
            "action": str,
            "sessionId": str (required except for 'list'),
            "offset": int (for 'log'),
            "limit": int (for 'log'),
            "data": str (for 'write'),
            "timeout": int (for 'poll'),
        }
        store: ProcessStore instance.

    Returns:
        Action result as text.
    """
    global _default_store
    if store is None:
        store = get_default_store()

    action = args["action"]
    session_id = args.get("sessionId", "")

    # -----------------------------------------------------------------------
    # list
    # -----------------------------------------------------------------------
    if action == "list":
        processes = store.list_all()
        if not processes:
            return "No background processes running."

        lines = ["Background processes:"]
        for p in processes:
            elapsed = time.time() - p.start_time
            status = "completed" if p.terminated else "running"
            lines.append(
                f"  [{p.session_id}] pid={p.pid} "
                f"status={status} "
                f"elapsed={elapsed:.0f}s "
                f"cmd={p.command[:60]}"
            )
        return "\n".join(lines)

    # All other actions need a valid session
    if not session_id:
        return "Error: sessionId required"

    handle = store.get(session_id)
    if not handle:
        return f"Error: Unknown process session '{session_id}'"

    # -----------------------------------------------------------------------
    # poll
    # -----------------------------------------------------------------------
    if action == "poll":
        popen = store.get_popen(session_id)
        if not popen:
            return f"Error: Process handle lost for session '{session_id}'"

        poll_timeout = args.get("timeout", 0)  # 0 = instant check

        if poll_timeout > 0:
            try:
                popen.wait(timeout=poll_timeout)
            except subprocess.TimeoutExpired:
                return (
                    f"Process {session_id} still running after {poll_timeout}s. "
                    f"pid={popen.pid}"
                )

        # Check return code
        if popen.returncode is not None:
            handle.return_code = popen.returncode
            handle.terminated = True

            # Read final output
            output = ""
            if handle.output_path and os.path.exists(handle.output_path):
                try:
                    with open(handle.output_path, "r", encoding="utf-8", errors="replace") as f:
                        output = f.read()
                    # Truncate
                    if len(output) > MAX_OUTPUT_BYTES:
                        output = output[:MAX_OUTPUT_BYTES] + "\n\n--- [TRUNCATED] ---"
                except OSError:
                    pass

            result_lines = [
                f"Process {session_id} completed.",
                f"return_code: {popen.returncode}",
                f"pid: {popen.pid}",
            ]
            if output.strip():
                result_lines.append(f"Output:\n{output}")
            store.cleanup(session_id)
            return "\n".join(result_lines)
        else:
            elapsed = time.time() - handle.start_time
            return (
                f"Process {session_id} still running. "
                f"pid={popen.pid} elapsed={elapsed:.0f}s"
            )

    # -----------------------------------------------------------------------
    # log
    # -----------------------------------------------------------------------
    if action == "log":
        if not handle.output_path or not os.path.exists(handle.output_path):
            return f"No output captured for process {session_id}."

        offset = args.get("offset", 0)
        limit = args.get("limit", None)

        try:
            with open(handle.output_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return f"Error reading log: {e}"

        # Truncate if needed
        if len(content) > MAX_OUTPUT_BYTES:
            content = content[:MAX_OUTPUT_BYTES] + "\n\n--- [TRUNCATED] ---"

        # Apply offset/limit on lines
        lines = content.split("\n")
        if offset > 0:
            lines = lines[offset:]
        if limit is not None:
            lines = lines[:limit]

        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # write (stdin)
    # -----------------------------------------------------------------------
    if action == "write":
        data = args.get("data", "")
        popen = store.get_popen(session_id)
        if not popen:
            return f"Error: Process handle lost for session '{session_id}'"

        if not handle.interactive:
            return (
                f"Error: Process {session_id} is not interactive; stdin is closed in background mode. "
                "Rerun with interactive=true if you need to send input."
            )

        if popen.returncode is not None:
            return f"Process {session_id} already completed; cannot write to stdin."

        try:
            popen.stdin.write(data)
            popen.stdin.flush()
            return f"Wrote {len(data)} bytes to stdin of process {session_id}."
        except (OSError, BrokenPipeError) as e:
            return f"Error writing to stdin: {e}"

    # -----------------------------------------------------------------------
    # send-keys
    # -----------------------------------------------------------------------
    if action == "send-keys":
        popen = store.get_popen(session_id)
        if not popen:
            return f"Error: Process handle lost for session '{session_id}'"

        if not handle.interactive:
            return (
                f"Error: Process {session_id} is not interactive; send-keys requires interactive=true on exec."
            )

        if popen.returncode is not None:
            return f"Process {session_id} already completed; cannot send keys."

        key_spec = str(args.get("keys") or args.get("data") or "").strip()
        if not key_spec:
            return "Error: keys required for send-keys action"

        normalized_key = key_spec.lower().replace(" ", "")
        if normalized_key in {"ctrl+c", "control+c", "^c"}:
            try:
                if sys.platform == "win32":
                    ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
                    if ctrl_break is None:
                        return "Error: CTRL+Break signaling is unavailable on this platform"
                    popen.send_signal(ctrl_break)
                else:
                    popen.send_signal(signal.SIGINT)
                return f"Sent Ctrl+C to process {session_id}."
            except (OSError, ValueError) as e:
                return f"Error sending Ctrl+C: {e}"

        special_keys = {
            "enter": "\n",
            "return": "\n",
            "tab": "\t",
            "escape": "\x1b",
            "esc": "\x1b",
        }
        data = special_keys.get(normalized_key, args.get("data") if args.get("data") is not None else key_spec)

        try:
            popen.stdin.write(data)
            popen.stdin.flush()
            return f"Sent keys to process {session_id}: {key_spec}"
        except (OSError, BrokenPipeError) as e:
            return f"Error sending keys: {e}"

    # -----------------------------------------------------------------------
    # kill
    # -----------------------------------------------------------------------
    if action == "kill":
        success = store.terminate(session_id, reason="kill")
        if success:
            return (
                f"Process {session_id} terminated. "
                f"return_code={handle.return_code}"
            )
        else:
            return f"Error: Could not terminate process {session_id}"

    # Fallback (shouldn't reach here given validation)
    return f"Error: Unknown action '{action}'"


# ---------------------------------------------------------------------------
# Tool contracts
# ---------------------------------------------------------------------------


def make_exec_tool(
    *,
    store: ProcessStore | None = None,
    workdir: str | None = None,
    allowed_paths: list[str] | None = None,
) -> ToolContract:
    """Build the 'exec' tool contract."""
    return build_tool(
        name="exec",
        description=(
            "Execute a shell command. Foreground mode (default) runs to completion "
            "with timeout. Background mode spawns a process handle for later management "
            "via the 'process' tool.\n\n"
            "WINDOWS SHELL RULES (this host runs PowerShell 5.1 by default):\n"
            "- && is NOT supported in PowerShell 5.1. Do NOT chain commands with &&.\n"
            "- For sequential steps, issue separate exec calls one at a time.\n"
            "- For a multi-step script, use script_content with runner='powershell'.\n"
            "- PowerShell variables ($var) expand in -Command strings — use script_content to avoid this.\n\n"
            "GIT SAFETY RULES:\n"
            "- A request to push, pull, or inspect git state does NOT imply permission to stage files, create commits, or rewrite history.\n"
            "- Use git status / git remote -v / git push as separate steps.\n"
            "- If unrelated staged or unstaged changes are present, report that blocker instead of auto-committing them.\n\n"
            "MISSING CLI RULES:\n"
            "- If the task depends on a standard CLI and it is missing (for example sqlite3), prefer installing it or adding it to PATH first.\n"
            "- Do NOT replace a standard CLI workflow with a throwaway Python script unless the user explicitly asked for that workaround.\n"
            "- On Windows, prefer a normal installer or winget for standard CLIs before inventing custom scripting.\n\n"
            "A non-zero exit code means the command failed. Read the output for the error "
            "message and retry with the corrected command rather than repeating the same call."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Short shell command to execute",
                },
                "script_content": {
                    "type": "string",
                    "description": "Script body to run through a temporary script file",
                },
                "script_content_base64": {
                    "type": "string",
                    "description": "Base64-encoded UTF-8 script body to run through a temporary script file",
                },
                "runner": {
                    "type": "string",
                    "description": "Interpreter or shell for script_content, e.g. python, node, powershell, sh",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 300)",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run in background (returns session handle)",
                },
                "interactive": {
                    "type": "boolean",
                    "description": "Run as an interactive managed session so stdin/keys can be sent via the process tool",
                },
                "env": {
                    "type": "object",
                    "description": "Environment variable overrides",
                },
            },
        },
        execute=lambda args: exec_exec(
            args, store=store, workdir=workdir
        ),
        validate_input=validate_exec_input,
        permission_check=make_exec_permission_check(workdir=workdir, allowed_paths=allowed_paths),
        categories=[CATEGORY_CORE],
    )


def make_process_tool(
    *,
    store: ProcessStore | None = None,
) -> ToolContract:
    """Build the 'process' tool contract."""
    return build_tool(
        name="process",
        description=(
            "Manage background processes. Actions: list (all processes), "
            "poll (check status), log (read output), write (send stdin data), "
            "send-keys (Enter/Tab/Escape/Ctrl+C), kill (terminate process)."
        ),
        input_schema={
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: list, poll, log, write, send-keys, kill",
                    "enum": ["list", "poll", "log", "write", "send-keys", "kill"],
                },
                "sessionId": {
                    "type": "string",
                    "description": "Process session id (required except for 'list')",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset for 'log' action",
                },
                "limit": {
                    "type": "integer",
                    "description": "Line limit for 'log' action",
                },
                "data": {
                    "type": "string",
                    "description": "Data to write to stdin for 'write' action",
                },
                "keys": {
                    "type": "string",
                    "description": "Special key sequence for 'send-keys', e.g. Enter, Tab, Escape, Ctrl+C",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Poll timeout in seconds for 'poll' action",
                },
            },
        },
        execute=lambda args: exec_process(args, store=store),
        validate_input=validate_process_input,
        read_only=True,  # management tool, doesn't modify files
        categories=[CATEGORY_CORE],
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_process_tools(
    registry,
    *,
    store: ProcessStore | None = None,
    workdir: str | None = None,
    allowed_paths: list[str] | None = None,
) -> list[ToolContract]:
    """Register process tools (exec, process) in the registry.

    Returns the list of registered ToolContracts.
    """
    gstore = store or get_default_store()
    tools = [
        make_exec_tool(store=gstore, workdir=workdir, allowed_paths=allowed_paths),
        make_process_tool(store=gstore),
    ]
    registry.register_many(tools)
    return tools
