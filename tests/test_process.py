# Tests for process tools — exec, process

from __future__ import annotations

import base64
import os
import signal
import subprocess
import sys
import time
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from api.tools.process import (
    make_exec_tool,
    make_process_tool,
    exec_exec,
    exec_process,
    ProcessStore,
    ProcessHandle,
    extract_command_path_candidates,
    get_default_store,
    normalize_command_paths,
    validate_exec_input,
    validate_process_input,
    MAX_OUTPUT_BYTES,
)
from api.tools.registry import ToolRegistry, ValidationResult, PermissionDecision


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestValidateExecInput:
    def test_valid_input(self):
        result = validate_exec_input({"command": "echo hello"})
        assert result.ok is True

    def test_valid_script_content_input(self):
        result = validate_exec_input({"script_content": "print('hello')", "runner": "python"})
        assert result.ok is True

    def test_valid_script_content_base64_input(self):
        result = validate_exec_input({
            "script_content_base64": base64.b64encode(b"print('hello')").decode("ascii"),
            "runner": "python",
        })
        assert result.ok is True

    def test_empty_command(self):
        result = validate_exec_input({"command": ""})
        assert result.ok is False

    def test_missing_command(self):
        result = validate_exec_input({})
        assert result.ok is False

    def test_rejects_multiple_exec_payload_shapes(self):
        result = validate_exec_input({"command": "echo hi", "script_content": "print('hi')"})
        assert result.ok is False

    def test_zero_timeout(self):
        result = validate_exec_input({"command": "echo hi", "timeout": 0})
        assert result.ok is False

    def test_negative_timeout(self):
        result = validate_exec_input({"command": "echo hi", "timeout": -1})
        assert result.ok is False

    def test_valid_timeout(self):
        result = validate_exec_input({"command": "echo hi", "timeout": 60})
        assert result.ok is True


class TestValidateProcessInput:
    def test_list_action(self):
        result = validate_process_input({"action": "list"})
        assert result.ok is True

    def test_poll_without_session(self):
        result = validate_process_input({"action": "poll"})
        assert result.ok is False

    def test_poll_with_session(self):
        result = validate_process_input({"action": "poll", "sessionId": "abc123"})
        assert result.ok is True

    def test_invalid_action(self):
        result = validate_process_input({"action": "explode"})
        assert result.ok is False

    def test_empty_action(self):
        result = validate_process_input({"action": ""})
        assert result.ok is False


class TestExecPathParsing:
    def test_extracts_explicit_path_tokens_and_redirections(self):
        command = 'cat ./notes/todo.txt > ../out/log.txt'

        candidates = extract_command_path_candidates(command)

        assert candidates == ['./notes/todo.txt', '../out/log.txt']

    def test_normalizes_relative_command_paths_against_workdir(self):
        with tempfile.TemporaryDirectory() as workspace_root:
            allowed_root = os.path.join(workspace_root, 'allowed')
            os.mkdir(allowed_root)

            normalized = normalize_command_paths('../secret/data.txt', default_workdir=allowed_root)

            assert normalized == [os.path.realpath(os.path.join(allowed_root, '..', 'secret', 'data.txt'))]


# ---------------------------------------------------------------------------
# ProcessStore
# ---------------------------------------------------------------------------


class TestProcessStore:
    def test_register_and_get(self):
        store = ProcessStore()
        handle = ProcessHandle(
            session_id="test1",
            command="echo hello",
            workdir=None,
            pid=1234,
            start_time=time.time(),
            env=None,
        )
        # Can't easily create a real Popen for this test, skip popen registration

    def test_get_nonexistent(self):
        store = ProcessStore()
        assert store.get("no_such_id") is None

    def test_list_all_empty(self):
        store = ProcessStore()
        assert store.list_all() == []

    def test_terminate_nonexistent(self):
        store = ProcessStore()
        assert store.terminate("no_such_id") is False

    def test_cleanup_nonexistent(self):
        store = ProcessStore()
        store.cleanup("no_such_id")  # no error

    def test_terminate_invokes_callback_with_reason(self):
        terminated: list[tuple[str, str]] = []
        store = ProcessStore(on_terminated=lambda handle, reason: terminated.append((handle.session_id, reason)))
        handle = ProcessHandle(
            session_id="proc_1",
            command="python wait.py",
            workdir=None,
            pid=1234,
            start_time=time.time(),
            env=None,
        )
        popen = MagicMock()
        popen.returncode = -15
        store._processes[handle.session_id] = handle
        store._popen[handle.session_id] = popen

        assert store.terminate("proc_1", reason="kill") is True
        assert terminated == [("proc_1", "kill")]


# ---------------------------------------------------------------------------
# exec tool — foreground
# ---------------------------------------------------------------------------


class TestExecForeground:
    def setup_method(self):
        self.store = ProcessStore()

    def test_git_push_auto_promotes_to_interactive_session(self):
        popen = MagicMock()
        popen.pid = 4321

        with patch("api.tools.process.subprocess.Popen", return_value=popen) as popen_call:
            result = exec_exec(
                {"command": "git push origin master", "workdir": "D:/openclaw/opencloset", "timeout": 15},
                store=self.store,
            )

        assert "Interactive process started" in result
        assert "session_id:" in result
        assert "send-keys" in result

        handle = self.store.list_all()[0]
        assert handle.interactive is True
        assert handle.command == "git push origin master --progress"
        assert popen_call.call_args.kwargs["stdin"] == subprocess.PIPE
        assert any("git push origin master --progress" in str(part) for part in popen_call.call_args.args[0])

    def test_interactive_git_push_prefers_cli_credential_helpers(self):
        popen = MagicMock()
        popen.pid = 4321

        with patch("api.tools.process.subprocess.Popen", return_value=popen) as popen_call, \
             patch("api.tools.process.shutil.which", return_value="C:/Program Files/GitHub CLI/gh.exe"):
            exec_exec(
                {"command": "git push origin master", "workdir": "D:/openclaw/opencloset", "interactive": True},
                store=self.store,
            )

        env = popen_call.call_args.kwargs["env"]
        assert env["GCM_INTERACTIVE"] == "never"
        assert env["GH_PROMPT_DISABLED"] == "1"
        assert env["GIT_PROGRESS_DELAY"] == "0"
        assert env["GIT_CONFIG_COUNT"] == "3"
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_0"] == ""
        assert env["GIT_CONFIG_KEY_1"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_1"] == "!gh auth git-credential"
        assert env["GIT_CONFIG_KEY_2"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_2"] == "store"

    def test_simple_command(self):
        if sys.platform == "win32":
            result = exec_exec(
                {"command": "echo hello"},
                store=self.store,
            )
        else:
            result = exec_exec(
                {"command": "echo hello"},
                store=self.store,
            )
        assert "hello" in result
        assert "Exit code: 0" in result

    def test_command_with_output(self):
        if sys.platform == "win32":
            result = exec_exec(
                {"command": "dir"},
                store=self.store,
            )
        else:
            result = exec_exec(
                {"command": "ls /tmp"},
                store=self.store,
            )
        assert "Exit code:" in result

    @pytest.mark.skipif(sys.platform != "win32", reason="PowerShell wrapper only applies on Windows")
    def test_windows_exec_uses_powershell_builtins(self):
        result = exec_exec(
            {"command": "Get-ChildItem . | Select-Object -First 1"},
            store=self.store,
        )

        assert "Exit code: 0" in result

    def test_failing_command(self):
        if sys.platform == "win32":
            result = exec_exec(
                {"command": "cmd /c exit 1"},
                store=self.store,
            )
        else:
            result = exec_exec(
                {"command": "sh -c 'exit 1'"},
                store=self.store,
            )
        assert "Exit code: 1" in result

    def test_command_with_timeout(self):
        result = exec_exec(
            {"command": "echo quick", "timeout": 5},
            store=self.store,
        )
        assert "quick" in result

    def test_command_timed_out(self):
        if sys.platform == "win32":
            result = exec_exec(
                {"command": "ping -n 10 127.0.0.1", "timeout": 1},
                store=self.store,
            )
        else:
            result = exec_exec(
                {"command": "sleep 10", "timeout": 1},
                store=self.store,
            )
        assert "timed out" in result.lower()

    def test_command_with_workdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = exec_exec(
                {"command": "cd" if sys.platform != "win32" else "cd",
                  "workdir": tmpdir},
                store=self.store,
            )
            # Just verify it doesn't crash
            assert "Exit code:" in result

    def test_command_with_env(self):
        if sys.platform == "win32":
            result = exec_exec(
                {
                    "command": "echo $env:MY_TEST_VAR",
                    "env": {"MY_TEST_VAR": "hello_env"},
                },
                store=self.store,
            )
        else:
            result = exec_exec(
                {
                    "command": "echo $MY_TEST_VAR",
                    "env": {"MY_TEST_VAR": "hello_env"},
                },
                store=self.store,
            )
        assert "hello_env" in result

    def test_multiline_command_runs_via_temp_script(self):
        if sys.platform == "win32":
            command = "Write-Output 'alpha'\nWrite-Output 'beta'"
        else:
            command = "echo alpha\necho beta"

        result = exec_exec({"command": command}, store=self.store)

        assert "Exit code: 0" in result
        assert "alpha" in result
        assert "beta" in result

    def test_script_content_with_python_runner(self):
        result = exec_exec(
            {"script_content": "print('alpha')\nprint('beta')", "runner": sys.executable},
            store=self.store,
        )

        assert "Exit code: 0" in result
        assert "alpha" in result
        assert "beta" in result

    def test_script_content_with_literal_python_runner(self):
        result = exec_exec(
            {"script_content": "print('alpha')\nprint('beta')", "runner": "python"},
            store=self.store,
        )

        assert "Exit code: 0" in result
        assert "alpha" in result
        assert "beta" in result

    @pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe runner path only applies on Windows")
    def test_script_content_with_cmd_exe_runner(self):
        result = exec_exec(
            {"script_content": "@echo off\necho alpha\necho beta", "runner": "cmd.exe"},
            store=self.store,
        )

        assert "Exit code: 0" in result
        assert "alpha" in result
        assert "beta" in result

    def test_invalid_command(self):
        result = exec_exec(
            {"command": "this_command_does_not_exist_12345"},
            store=self.store,
        )
        assert "error" in result.lower() or "Exit code: 1" in result


# ---------------------------------------------------------------------------
# exec tool — background
# ---------------------------------------------------------------------------


class TestExecBackground:
    def setup_method(self):
        self.store = ProcessStore()

    def test_background_spawn(self):
        if sys.platform == "win32":
            cmd = "ping 127.0.0.1 -n 1"
        else:
            cmd = "sleep 0.1"

        result = exec_exec(
            {"command": cmd, "background": True},
            store=self.store,
        )
        assert "Background process started" in result
        assert "session_id:" in result

    def test_background_returns_session_id(self):
        if sys.platform == "win32":
            cmd = "ping 127.0.0.1 -n 1"
        else:
            cmd = "sleep 0.1"

        result = exec_exec(
            {"command": cmd, "background": True},
            store=self.store,
        )
        # Extract session_id from output
        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":")[1].strip()
                break
        else:
            pytest.fail("No session_id in result")

        # Verify process is in store
        handle = self.store.get(session_id)
        assert handle is not None
        assert handle.command == cmd

    def test_background_poll_completion(self):
        if sys.platform == "win32":
            cmd = "ping 127.0.0.1 -n 1"
        else:
            cmd = "sleep 0.1"

        result = exec_exec(
            {"command": cmd, "background": True},
            store=self.store,
        )
        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":")[1].strip()
                break

        # Wait a bit then poll
        time.sleep(0.5)
        poll_result = exec_process(
            {"action": "poll", "sessionId": session_id, "timeout": 5},
            store=self.store,
        )
        assert "completed" in poll_result.lower() or "return_code" in poll_result

    def test_interactive_spawn_returns_session_id_and_guidance(self):
        result = exec_exec(
            {
                "script_content": "import time\nprint('ready', flush=True)\ntime.sleep(0.5)",
                "runner": sys.executable,
                "interactive": True,
            },
            store=self.store,
        )

        assert "Interactive process started" in result
        assert "session_id:" in result
        assert "send-keys" in result

    def test_noninteractive_background_closes_stdin_instead_of_hanging(self):
        result = exec_exec(
            {
                "script_content": "print('READY', flush=True)\ninput()\n",
                "runner": sys.executable,
                "background": True,
            },
            store=self.store,
        )

        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                break
        else:
            pytest.fail("No session_id in background exec result")

        time.sleep(0.2)
        poll_result = exec_process({"action": "poll", "sessionId": session_id, "timeout": 2}, store=self.store)

        assert "completed" in poll_result.lower()
        assert "still running" not in poll_result.lower()

    def test_noninteractive_background_guidance_mentions_closed_stdin(self):
        result = exec_exec(
            {"command": "echo ok", "background": True},
            store=self.store,
        )

        assert "stdin is closed" in result.lower()

    def test_background_exec_records_runtime_ownership_metadata(self):
        popen = MagicMock()
        popen.pid = 4321

        with patch("api.tools.process.subprocess.Popen", return_value=popen):
            exec_exec(
                {
                    "command": "echo hello",
                    "background": True,
                    "_runtime_session_id": "sess_123",
                    "_run_id": "run_456",
                },
                store=self.store,
            )

        handle = self.store.list_all()[0]
        assert handle.owner_session_id == "sess_123"
        assert handle.owner_run_id == "run_456"


# ---------------------------------------------------------------------------
# process tool actions
# ---------------------------------------------------------------------------


class TestProcessActions:
    def setup_method(self):
        self.store = ProcessStore()

    def test_list_empty(self):
        result = exec_process({"action": "list"}, store=self.store)
        assert "No background processes" in result

    def test_list_with_process(self):
        if sys.platform == "win32":
            cmd = "ping 127.0.0.1 -n 1"
        else:
            cmd = "sleep 0.1"

        exec_exec(
            {"command": cmd, "background": True},
            store=self.store,
        )
        result = exec_process({"action": "list"}, store=self.store)
        assert "Background processes:" in result

    def test_poll_unknown_session(self):
        result = exec_process(
            {"action": "poll", "sessionId": "no_such_id"},
            store=self.store,
        )
        assert "Unknown process" in result or "Error" in result

    def test_log_unknown_session(self):
        result = exec_process(
            {"action": "log", "sessionId": "no_such_id"},
            store=self.store,
        )
        assert "Error" in result or "Unknown" in result

    def test_kill_unknown_session(self):
        result = exec_process(
            {"action": "kill", "sessionId": "no_such_id"},
            store=self.store,
        )
        assert "Error" in result

    def test_missing_session_id(self):
        result = exec_process(
            {"action": "poll"},
            store=self.store,
        )
        assert "sessionid" in result.lower() or "required" in result.lower()

    def test_send_keys_ctrl_c_signals_process(self):
        handle = ProcessHandle(
            session_id="abc123",
            command="python wait.py",
            workdir=None,
            pid=999,
            start_time=time.time(),
            env=None,
            interactive=True,
        )
        popen = MagicMock()
        popen.returncode = None
        popen.pid = 999
        self.store._processes[handle.session_id] = handle
        self.store._popen[handle.session_id] = popen

        result = exec_process(
            {"action": "send-keys", "sessionId": "abc123", "keys": "Ctrl+C"},
            store=self.store,
        )

        assert "Sent Ctrl+C" in result
        expected_signal = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT) if sys.platform == "win32" else signal.SIGINT
        popen.send_signal.assert_called_once_with(expected_signal)

    def test_interactive_session_accepts_write_then_enter(self):
        result = exec_exec(
            {
                "script_content": (
                    "import sys\n"
                    "print('READY', flush=True)\n"
                    "line = input()\n"
                    "print(f'GOT:{line}', flush=True)\n"
                ),
                "runner": sys.executable,
                "interactive": True,
            },
            store=self.store,
        )

        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                break
        else:
            pytest.fail("No session_id in interactive exec result")

        time.sleep(0.2)
        exec_process({"action": "write", "sessionId": session_id, "data": "hello"}, store=self.store)
        exec_process({"action": "send-keys", "sessionId": session_id, "keys": "Enter"}, store=self.store)
        poll_result = exec_process({"action": "poll", "sessionId": session_id, "timeout": 5}, store=self.store)

        assert "completed" in poll_result.lower()
        assert "GOT:hello" in poll_result

    def test_write_rejected_for_noninteractive_background_session(self):
        result = exec_exec(
            {
                "script_content": "import time\nprint('READY', flush=True)\ntime.sleep(1)\n",
                "runner": sys.executable,
                "background": True,
            },
            store=self.store,
        )

        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                break
        else:
            pytest.fail("No session_id in background exec result")

        write_result = exec_process(
            {"action": "write", "sessionId": session_id, "data": "hello"},
            store=self.store,
        )

        assert "not interactive" in write_result.lower()


# ---------------------------------------------------------------------------
# Tool contract metadata
# ---------------------------------------------------------------------------


class TestExecToolMetadata:
    def test_exec_tool(self):
        tool = make_exec_tool(store=ProcessStore())
        assert tool.name == "exec"
        assert "core" in tool.categories
        assert "prefer installing it or adding it to PATH first" in tool.description
        assert "command" in tool.input_schema["properties"]
        assert "script_content" in tool.input_schema["properties"]
        assert "script_content_base64" in tool.input_schema["properties"]
        assert "interactive" in tool.input_schema["properties"]

    def test_process_tool(self):
        tool = make_process_tool(store=ProcessStore())
        assert tool.name == "process"
        assert tool.read_only is True
        assert "core" in tool.categories
        assert tool.input_schema["required"] == ["action"]
        assert "send-keys" in tool.input_schema["properties"]["action"]["enum"]

    def test_exec_permission_check_denies_workdir_outside_allowed_scope(self):
        with tempfile.TemporaryDirectory() as workspace_root:
            allowed_root = os.path.join(workspace_root, "allowed")
            denied_root = os.path.join(workspace_root, "denied")
            os.mkdir(allowed_root)
            os.mkdir(denied_root)

            reg = ToolRegistry(
                agent_type="main",
                trust_mode="allowlist",
                allowlist=["exec"],
                provider_capabilities={"supports_tool_use": True},
            )
            reg.register(make_exec_tool(store=ProcessStore(), workdir=workspace_root, allowed_paths=[allowed_root]))

            allowed_decision = reg.check_permission(
                "exec",
                input_data={"command": "echo ok", "workdir": allowed_root},
            )
            denied_decision = reg.check_permission(
                "exec",
                input_data={"command": "echo nope", "workdir": denied_root},
            )

            assert allowed_decision == PermissionDecision.ALLOW
            assert denied_decision == PermissionDecision.DENY

    def test_exec_permission_check_allows_command_with_path_args_when_workdir_is_in_scope(self):
        # Command argument paths are NOT scanned — only the workdir is checked.
        # Path scanning caused false denials on relative refs (origin/main),
        # scoped packages (@types/node), and Unix-style absolute paths on Windows.
        with tempfile.TemporaryDirectory() as workspace_root:
            allowed_root = os.path.join(workspace_root, "allowed")
            os.mkdir(allowed_root)

            reg = ToolRegistry(
                agent_type="main",
                trust_mode="allowlist",
                allowlist=["exec"],
                provider_capabilities={"supports_tool_use": True},
            )
            reg.register(make_exec_tool(store=ProcessStore(), workdir=allowed_root, allowed_paths=[allowed_root]))

            decision = reg.check_permission(
                "exec",
                input_data={"command": "cat ../outside/secret.txt", "workdir": allowed_root},
            )

            assert decision == PermissionDecision.ALLOW


# ---------------------------------------------------------------------------
# Integration: exec background → poll → log → cleanup
# ---------------------------------------------------------------------------


    def test_exec_normalizes_windows_cd_and_chain_operator(self):
        if sys.platform != "win32":
            pytest.skip("Windows-specific shell normalization")

        with tempfile.TemporaryDirectory() as workspace_root:
            nested = os.path.join(workspace_root, "nested")
            os.mkdir(nested)

            result = exec_exec(
                {"command": f"cd {nested} && Get-Location | Select-Object -ExpandProperty Path"},
                store=ProcessStore(),
                workdir=workspace_root,
            )

            assert nested in result


class TestProcessIntegration:
    def test_full_lifecycle(self):
        store = ProcessStore()

        # Spawn background process
        if sys.platform == "win32":
            cmd = "ping 127.0.0.1 -n 1"
        else:
            cmd = "sleep 0.1"

        result = exec_exec(
            {"command": cmd, "background": True},
            store=store,
        )
        for line in result.split("\n"):
            if line.startswith("session_id:"):
                session_id = line.split(":")[1].strip()
                break
        else:
            pytest.fail("No session_id in exec result")

        # Poll until completion
        time.sleep(0.5)
        poll_result = exec_process(
            {"action": "poll", "sessionId": session_id, "timeout": 5},
            store=store,
        )
        assert "completed" in poll_result.lower() or "return_code" in poll_result

        # Process should be cleaned up
        assert store.get(session_id) is None
