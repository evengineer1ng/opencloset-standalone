# Tests for api/agent/prompt.py

import json
import pytest
from unittest.mock import MagicMock

from api.agent.prompt import (
    PromptBuilder,
    PromptSection,
    PromptResult,
    build_behavior_patches_section,
    build_window_policy_section,
    build_plan_section,
    build_continuity_section,
    build_workspace_section,
    build_tool_manifest,
    build_environment_facts,
    build_run_mode_section,
    score_transient_window_policy,
    truncate_to_budget,
    default_env_facts,
    PRIORITY,
)
from api.agent.engine import Message, MessageKind


# ---------------------------------------------------------------------------
# build_plan_section
# ---------------------------------------------------------------------------

class TestBuildPlanSection:

    def test_empty_plan(self):
        """Empty plan data produces minimal header."""
        result = build_plan_section({})
        assert "## Active Plan" in result
        # No goal, no want_to_know, no handoff
        assert "Active Goal:" not in result
        assert "Need To Know:" not in result
        assert "Session Context" not in result

    def test_active_goal_only(self):
        """Active goal renders correctly."""
        result = build_plan_section({
            "active_goal": "Implement plan runtime integration",
        })
        assert "## Active Plan" in result
        assert "**Active Goal:** Implement plan runtime integration" in result
        assert "Need To Know:" not in result

    def test_want_to_know_only(self):
        """Want-to-know items render as a list."""
        result = build_plan_section({
            "want_to_know": ["Check planv2.md", "Verify test count"],
        })
        assert "## Active Plan" in result
        assert "**Need To Know:**" in result
        assert "- Check planv2.md" in result
        assert "- Verify test count" in result
        assert "Active Goal:" not in result

    def test_full_plan_with_handoff(self):
        """All fields render correctly including handoff."""
        result = build_plan_section({
            "active_goal": "Finish Phase 0",
            "want_to_know": ["Item A", "Item B"],
            "handoff": {
                "next_action": "Write tests",
                "last_state": "running",
                "note": None,  # should be skipped
            },
        })
        assert "## Active Plan" in result
        assert "**Active Goal:** Finish Phase 0" in result
        assert "- Item A" in result
        assert "- Item B" in result
        assert "**Session Context (from handoff):**" in result
        assert "**next_action**: Write tests" in result
        assert "**last_state**: running" in result
        # None values should be skipped
        assert "**note**:" not in result

    def test_want_to_know_empty_list(self):
        """Empty want_to_know list does not render section."""
        result = build_plan_section({
            "active_goal": "Do something",
            "want_to_know": [],
        })
        assert "Active Goal" in result
        assert "Need To Know:" not in result

    def test_handoff_not_dict(self):
        """Non-dict handoff is ignored safely."""
        result = build_plan_section({
            "active_goal": "Test",
            "handoff": "not a dict",
        })
        assert "Active Goal" in result
        assert "Session Context" not in result

    def test_single_item_want_to_know(self):
        """Single want-to-know item renders correctly."""
        result = build_plan_section({
            "want_to_know": ["Only item"],
        })
        assert "- Only item" in result


# ---------------------------------------------------------------------------
# build_tool_manifest
# ---------------------------------------------------------------------------

class TestBuildToolManifest:

    def test_empty_tools(self):
        assert build_tool_manifest([]) == ""

    def test_single_tool(self):
        tools = [{"name": "read", "description": "Read a file", "input_schema": {}}]
        result = build_tool_manifest(tools)
        assert "**read**" in result
        assert "Read a file" in result

    def test_multiple_tools(self):
        tools = [
            {"name": "read", "description": "Read", "input_schema": {}},
            {"name": "write", "description": "Write", "input_schema": {}},
        ]
        result = build_tool_manifest(tools)
        assert "## Available Tools" in result
        assert "**read**" in result
        assert "**write**" in result

    def test_compact_mode_omits_descriptions(self):
        tools = [
            {"name": "read", "description": "Read a file", "input_schema": {}},
            {"name": "write", "description": "Write a file", "input_schema": {}},
        ]
        result = build_tool_manifest(tools, mode="compact")
        assert "## Available Tools" in result
        assert "**read**" in result
        assert "**write**" in result
        assert "Read a file" not in result
        assert "Write a file" not in result


# ---------------------------------------------------------------------------
# build_environment_facts
# ---------------------------------------------------------------------------

class TestBuildEnvironmentFacts:

    def test_basic_facts(self):
        env = {"os": "windows", "shell": "powershell"}
        result = build_environment_facts(env)
        assert "## Environment" in result
        assert "**os**: `windows`" in result
        assert "**shell**: `powershell`" in result

    def test_empty_env(self):
        result = build_environment_facts({})
        assert "## Environment" in result


# ---------------------------------------------------------------------------
# build_workspace_section
# ---------------------------------------------------------------------------

class TestBuildWorkspaceSection:

    def test_empty_workspace_data(self):
        assert build_workspace_section({}) == ""

    def test_workspace_only(self):
        result = build_workspace_section({
            "workspace": {
                "name": "OpenCloset",
                "kind": "software",
                "status": "active",
                "description": "Main build workspace",
            }
        })
        assert "## Active Workspace" in result
        assert "**Workspace Name:** OpenCloset" in result
        assert "**Workspace Kind:** software" in result
        assert "**Workspace Status:** active" in result
        assert "Main build workspace" in result
        assert "## Active Build Project" not in result

    def test_workspace_with_build_project(self):
        result = build_workspace_section({
            "workspace": {
                "name": "OpenCloset",
                "kind": "software",
                "status": "maintenance",
            },
            "build_project": {
                "name": "Prompt Wiring",
                "status": "active",
                "description": "Inject workspace context into prompt assembly",
            },
        })
        assert "## Active Workspace" in result
        assert "## Active Build Project" in result
        assert "**Build Project Name:** Prompt Wiring" in result
        assert "**Build Project Status:** active" in result
        assert "Inject workspace context into prompt assembly" in result


class TestBuildContinuitySection:

    def test_empty_continuity_data(self):
        assert build_continuity_section({}) == ""

    def test_continuity_fields_render(self):
        result = build_continuity_section({
            "rolling_synopsis": "Rolling summary of the current harness-hardening thread.",
            "latest_user": "Continue fixing the harness continuity issue.",
            "latest_assistant": "Added null-safe execute response handling.",
            "latest_tool": "Shell build passed.",
            "open_threads": ["Verify continuity persists across the next turn."],
        })
        assert "## Session Continuity" in result
        assert "Rolling Synopsis" in result
        assert "Latest User Request" in result
        assert "Latest Assistant State" in result
        assert "Latest Tool/Runtime Result" in result
        assert "Open Threads To Preserve" in result


class TestBuildBehaviorPatchesSection:

    def test_empty_behavior_patches(self):
        assert build_behavior_patches_section([]) == ""

    def test_behavior_patches_render(self):
        result = build_behavior_patches_section([
            {
                "title": "Patch title",
                "patch": "Inspect existing files before rewriting them.",
                "scope": "workspace",
            }
        ])
        assert "## Approved Behavior Patches" in result
        assert "Inspect existing files before rewriting them." in result
        assert "scope=workspace" in result


# ---------------------------------------------------------------------------
# build_run_mode_section
# ---------------------------------------------------------------------------

class TestBuildRunModeSection:

    def test_fresh(self):
        result = build_run_mode_section("fresh")
        assert "fresh session" in result

    def test_continuation(self):
        result = build_run_mode_section("continuation")
        assert "Continuing from a prior run" in result

    def test_rollover(self):
        result = build_run_mode_section("rollover")
        assert "Resuming after context rollover" in result

    def test_unknown_mode(self):
        result = build_run_mode_section("custom_mode")
        assert "Run mode: custom_mode" in result

    def test_extra_text(self):
        result = build_run_mode_section("fresh", extra="Test extra")
        assert "Test extra" in result


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------

class TestPromptBuilder:

    def test_assemble_basic(self):
        builder = PromptBuilder(
            base_identity="You are a helpful assistant.",
            env_facts={"os": "windows"},
        )
        result = builder.assemble(transcript=[])
        assert isinstance(result, PromptResult)
        assert result.messages[0]["role"] == "system"
        assert "You are a helpful assistant" in result.messages[0]["content"]
        assert "windows" in result.messages[0]["content"]

    def test_assemble_with_plan(self):
        builder = PromptBuilder(
            base_identity="Test identity",
        )
        plan = {
            "active_goal": "Implement feature X",
            "want_to_know": ["Check docs"],
        }
        result = builder.assemble(transcript=[], plan_data=plan)
        system_content = result.messages[0]["content"]
        assert "## Active Plan" in system_content
        assert "Implement feature X" in system_content
        assert "- Check docs" in system_content

    def test_assemble_without_plan(self):
        builder = PromptBuilder(base_identity="Test")
        result = builder.assemble(transcript=[])
        system_content = result.messages[0]["content"]
        assert "## Active Plan" not in system_content

    def test_assemble_with_plan_empty(self):
        """Empty plan_data dict is falsy, so plan section is not injected."""
        builder = PromptBuilder(base_identity="Test")
        result = builder.assemble(transcript=[], plan_data={})
        system_content = result.messages[0]["content"]
        assert "## Active Plan" not in system_content

    def test_assemble_with_transcript(self):
        builder = PromptBuilder(base_identity="Test")
        transcript = [
            Message(role="user", content="Hello", token_estimate=2),
            Message(role="assistant", content="Hi there", token_estimate=2),
        ]
        result = builder.assemble(transcript=transcript)
        # System message + 2 transcript messages
        assert len(result.messages) == 3
        assert result.messages[0]["role"] == "system"
        assert result.messages[1]["role"] == "user"
        assert result.messages[2]["role"] == "assistant"

    def test_assemble_caps_tool_result_content_before_prompt(self):
        builder = PromptBuilder(base_identity="Test")
        large_output = "A" * 5000
        transcript = [
            Message(
                role="tool",
                kind=MessageKind.TOOL_RESULT,
                content=json.dumps({
                    "tool_id": "call_1",
                    "tool_name": "exec",
                    "status": "success",
                    "content": large_output,
                    "error": None,
                }),
                token_estimate=1250,
            )
        ]

        result = builder.assemble(transcript=transcript)

        assert len(result.messages) == 2
        payload = json.loads(result.messages[1]["content"])
        assert payload["tool_id"] == "call_1"
        assert payload["tool_name"] == "exec"
        assert payload["content_truncated_for_prompt"] is True
        assert len(payload["content"]) < len(large_output)
        assert "tool output truncated for prompt" in payload["content"]

    def test_assemble_plan_priority(self):
        """Plan section should stay below workspace/run_mode and above handoff."""
        assert PRIORITY["workspace"] == 4
        assert PRIORITY["run_mode"] == 5
        assert PRIORITY["continuity"] == 6
        assert PRIORITY["plan"] == 7
        assert PRIORITY["handoff"] == 8
        # Plan should be kept longer than handoff under pressure
        assert PRIORITY["plan"] < PRIORITY["handoff"]

    def test_assemble_maintenance_priority(self):
        assert PRIORITY["maintenance"] == 9
        assert PRIORITY["handoff"] == 8
        assert PRIORITY["maintenance"] < PRIORITY["memory"]

    def test_assemble_with_continuity_context(self):
        builder = PromptBuilder(base_identity="Test identity")
        continuity = {
            "latest_user": "Keep moving on the harness continuity bug.",
            "latest_assistant": "The previous turn added execute-path hardening.",
            "latest_tool": "UI build passed cleanly.",
            "open_threads": ["Make sure the next turn preserves active context."],
        }
        result = builder.assemble(transcript=[], continuity_data=continuity)
        system_content = result.messages[0]["content"]
        assert "## Session Continuity" in system_content
        assert "Keep moving on the harness continuity bug." in system_content
        assert "UI build passed cleanly." in system_content

    def test_assemble_with_workspace_context(self):
        builder = PromptBuilder(base_identity="Test identity")
        workspace = {
            "workspace": {
                "name": "OpenCloset",
                "kind": "software",
                "status": "active",
                "description": "Main build workspace",
            },
            "build_project": {
                "name": "Prompt Wiring",
                "status": "active",
            },
        }
        result = builder.assemble(transcript=[], workspace_data=workspace)
        system_content = result.messages[0]["content"]
        assert "## Active Workspace" in system_content
        assert "**Workspace Name:** OpenCloset" in system_content
        assert "## Active Build Project" in system_content
        assert "**Build Project Name:** Prompt Wiring" in system_content

    def test_assemble_with_behavior_patches(self):
        builder = PromptBuilder(base_identity="Test identity")
        result = builder.assemble(
            transcript=[],
            behavior_patches=[
                {
                    "title": "Trust the existing artifact",
                    "patch": "Inspect existing files before broad rewrites.",
                    "scope": "workspace",
                }
            ],
        )
        system_content = result.messages[0]["content"]
        assert "## Approved Behavior Patches" in system_content
        assert "Inspect existing files before broad rewrites." in system_content

    def test_assemble_all_sections(self):
        builder = PromptBuilder(
            base_identity="Identity",
            env_facts={"os": "linux"},
        )
        plan = {"active_goal": "Build it", "want_to_know": ["Test"]}
        result = builder.assemble(
            transcript=[],
            handoff_text="Rollover context",
            maintenance_artifacts=[{
                "artifact_type": "micro-summary",
                "content": "Idle micro-summary:\n- user: hello",
                "start_position": 1,
                "end_position": 2,
            }],
            memory_results="Memory hit",
            tool_defs=[{"name": "read", "description": "Read", "input_schema": {}}],
            plan_data=plan,
            run_mode="fresh",
        )
        system_content = result.messages[0]["content"]
        assert "Identity" in system_content
        assert "linux" in system_content
        assert "## Active Plan" in system_content
        assert "Build it" in system_content
        assert "Rollover context" in system_content
        assert "## Session Maintenance" in system_content
        assert "Derived micro-summary" in system_content
        assert "Idle micro-summary" in system_content
        assert "Memory hit" in system_content
        assert "## Available Tools" in system_content
        assert "fresh session" in system_content

    def test_assemble_with_window_policy(self):
        builder = PromptBuilder(base_identity="Test identity")
        policy = score_transient_window_policy(
            "Compare the top 10 models in a sortable table with filters",
            open_window_count=0,
            pinned_window_count=0,
        )

        result = builder.assemble(transcript=[], window_policy=policy)
        system_content = result.messages[0]["content"]

        assert "Window heuristic:" in system_content
        assert "text_plus_window" in system_content
        assert "score" in system_content

    def test_get_bootstrap_estimate_without_plan(self):
        builder = PromptBuilder(
            base_identity="x " * 50,  # ~50 tokens
            env_facts={"os": "test"},
        )
        estimate = builder.get_bootstrap_estimate()
        assert estimate > 50

    def test_get_bootstrap_estimate_with_plan(self):
        builder = PromptBuilder(base_identity="x " * 20)
        plan = {
            "active_goal": "A long goal description with many words",
            "want_to_know": ["Item 1", "Item 2", "Item 3"],
        }
        estimate_with = builder.get_bootstrap_estimate(plan_data=plan)
        estimate_without = builder.get_bootstrap_estimate()
        assert estimate_with > estimate_without

    def test_get_bootstrap_estimate_with_workspace(self):
        builder = PromptBuilder(base_identity="x " * 20)
        workspace = {
            "workspace": {
                "name": "OpenCloset",
                "kind": "software",
                "status": "active",
            }
        }
        estimate_with = builder.get_bootstrap_estimate(workspace_data=workspace)
        estimate_without = builder.get_bootstrap_estimate()
        assert estimate_with > estimate_without


# ---------------------------------------------------------------------------
# truncate_to_budget
# ---------------------------------------------------------------------------

class TestTruncateToBudget:

    def test_no_truncation_needed(self):
        sections = [
            PromptSection(name="a", text="x" * 100, priority=1, token_estimate=10),
            PromptSection(name="b", text="x" * 100, priority=2, token_estimate=10),
        ]
        pruned, truncated = truncate_to_budget(
            sections, budget_tokens=100, transcript_tokens=0,
        )
        # Budget 100, overhead 50 => available 50. Total 20 <= 50.
        assert len(pruned) == 2
        assert truncated == []

    def test_truncate_lowest_priority(self):
        """Low-priority section is dropped first; high-priority remains."""
        sections = [
            PromptSection(name="high", text="x" * 100, priority=1, token_estimate=20),
            PromptSection(name="low", text="x" * 100, priority=10, token_estimate=30),
        ]
        pruned, truncated = truncate_to_budget(
            sections, budget_tokens=100, transcript_tokens=0,
        )
        # Budget 100, overhead 50, transcript 0 => available 50
        # Total estimates = 50, fits exactly, so nothing truncated
        assert len(pruned) == 2
        assert len(truncated) == 0

    def test_truncate_lowest_priority_over_budget(self):
        """When over budget, lowest-priority section is removed first."""
        sections = [
            PromptSection(name="high", text="x" * 100, priority=1, token_estimate=20),
            PromptSection(name="low", text="x" * 100, priority=10, token_estimate=30),
        ]
        pruned, truncated = truncate_to_budget(
            sections, budget_tokens=60, transcript_tokens=0,
        )
        # Budget 60, overhead 50 => available 10. Total 50 > 10.
        # Remove "low" (priority 10, estimate 30) => total becomes 20 > 10
        # Remove "high" (priority 1, estimate 20) => total 0 <= 10
        # Both removed, but "low" removed first
        assert len(pruned) == 0
        assert "low" in truncated
        assert "high" in truncated

    def test_empty_sections(self):
        pruned, truncated = truncate_to_budget(
            [], budget_tokens=100, transcript_tokens=0,
        )
        assert pruned == []
        assert truncated == []


# ---------------------------------------------------------------------------
# default_env_facts
# ---------------------------------------------------------------------------

class TestDefaultEnvFacts:

    def test_contains_os(self):
        facts = default_env_facts()
        assert "os" in facts
        assert "shell" in facts
        assert "python" in facts
        assert "workspace" in facts

    def test_custom_workspace(self):
        facts = default_env_facts(workspace="/custom/path")
        assert facts["workspace"] == "/custom/path"

    def test_windows_defaults_shell_to_powershell(self, monkeypatch):
        monkeypatch.delenv("SHELL", raising=False)
        monkeypatch.setattr("api.agent.prompt.sys.platform", "win32")

        facts = default_env_facts()

        assert facts["shell"] == "powershell"


# ---------------------------------------------------------------------------
# transient window policy heuristic
# ---------------------------------------------------------------------------

class TestTransientWindowPolicy:

    def test_scores_text_only_for_explicit_plain_text_request(self):
        policy = score_transient_window_policy(
            "Just answer in plain text: what is 2 + 2?",
            open_window_count=0,
            pinned_window_count=0,
        )

        assert policy["recommendation"] == "text_only"
        assert policy["score"] < 2

    def test_scores_offer_for_moderate_structure_need_with_saturation(self):
        policy = score_transient_window_policy(
            "Compare these two approaches and tell me the tradeoffs.",
            open_window_count=3,
            pinned_window_count=1,
        )

        assert policy["recommendation"] == "text_plus_offer"
        assert 2 <= policy["score"] < 4

    def test_scores_window_for_explicit_visual_comparison_request(self):
        policy = score_transient_window_policy(
            "Compare the top 10 chefs in a ranked table with filters and notes by cuisine.",
            open_window_count=0,
            pinned_window_count=0,
        )

        assert policy["recommendation"] == "text_plus_window"
        assert policy["score"] >= 4

    def test_build_window_policy_section_includes_runtime_recommendation(self):
        section = build_window_policy_section({
            "recommendation": "text_plus_offer",
            "score": 3,
            "open_window_count": 2,
            "pinned_window_count": 1,
            "reasons": ["comparison cues suggest a structured view (+2)"],
        })

        assert "Window heuristic:" in section
        assert "text_plus_offer" in section
        assert "comparison cues suggest a structured view (+2)" in section


# ---------------------------------------------------------------------------
# extract_plan_slice
# ---------------------------------------------------------------------------

class TestExtractPlanSlice:

    def test_full_record(self):
        plan = {
            "id": "abc",
            "session_id": "abc",
            "active_goal": "Build prompt builder",
            "want_to_know": ["Check tests", "Verify coverage"],
            "context_guard": {"tokens_used": 1000},
            "handoff": {"next_action": "Run tests"},
            "status": "active",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }
        result = PromptBuilder.extract_plan_slice(plan)
        assert result["active_goal"] == "Build prompt builder"
        assert result["want_to_know"] == ["Check tests", "Verify coverage"]
        assert result["handoff"] == {"next_action": "Run tests"}
        # Internal fields should not leak
        assert "context_guard" not in result
        assert "status" not in result
        assert "id" not in result

    def test_empty_record(self):
        result = PromptBuilder.extract_plan_slice({})
        assert result is None

    def test_goal_only(self):
        plan = {"active_goal": "Do stuff"}
        result = PromptBuilder.extract_plan_slice(plan)
        assert result["active_goal"] == "Do stuff"
        assert "want_to_know" not in result

    def test_handoff_not_dict(self):
        plan = {"active_goal": "X", "handoff": "string"}
        result = PromptBuilder.extract_plan_slice(plan)
        assert result["active_goal"] == "X"
        assert "handoff" not in result

    def test_handoff_none(self):
        plan = {"active_goal": "X", "handoff": None}
        result = PromptBuilder.extract_plan_slice(plan)
        assert result["active_goal"] == "X"
        assert "handoff" not in result

    def test_want_to_know_none(self):
        plan = {"active_goal": "X", "want_to_know": None}
        result = PromptBuilder.extract_plan_slice(plan)
        assert result["active_goal"] == "X"
        assert "want_to_know" not in result


# ---------------------------------------------------------------------------
# Integration: PlanningManager → PromptBuilder
# ---------------------------------------------------------------------------

class TestPlanRuntimeIntegration:
    """End-to-end: plan record flows through builder into assembled prompt."""

    def test_full_integration(self):
        """Plan data from a simulated PlanningManager record makes it into the prompt."""
        # Simulate what PlanningManager.get_plan() returns
        plan_record = {
            "id": "sess_1",
            "session_id": "sess_1",
            "active_goal": "Implement plan runtime integration",
            "want_to_know": ["Wire prompt builder", "Add tests"],
            "context_guard": {"tokens_used": 500},
            "handoff": None,
            "status": "active",
            "created_at": "2026-01-01",
            "updated_at": "2026-01-01",
        }

        plan_data = PromptBuilder.extract_plan_slice(plan_record)
        assert plan_data is not None

        builder = PromptBuilder(
            base_identity="You are an agent.",
        )
        result = builder.assemble(transcript=[], plan_data=plan_data)
        system_content = result.messages[0]["content"]

        assert "## Active Plan" in system_content
        assert "Implement plan runtime integration" in system_content
        assert "- Wire prompt builder" in system_content
        assert "- Add tests" in system_content
        # Internal plan fields should NOT appear
        assert "context_guard" not in system_content
        assert "tokens_used" not in system_content

    def test_no_plan_record(self):
        """When no plan exists, prompt assembles without plan section."""
        plan_data = PromptBuilder.extract_plan_slice({})
        assert plan_data is None

        builder = PromptBuilder(base_identity="You are an agent.")
        result = builder.assemble(transcript=[], plan_data=plan_data)
        system_content = result.messages[0]["content"]
        assert "## Active Plan" not in system_content

    def test_plan_in_truncation_order(self):
        """Plan section stays below workspace/run_mode and above handoff."""
        from api.agent.prompt import PRIORITY
        assert PRIORITY["workspace"] < PRIORITY["run_mode"]
        assert PRIORITY["run_mode"] < PRIORITY["plan"]
        # Plan is higher priority than handoff, so plan survives longer
        assert PRIORITY["plan"] < PRIORITY["handoff"]
