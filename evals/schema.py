from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


@dataclass
class EvalFileSeed:
    path: str
    content: str


@dataclass
class EvalSetup:
    context_window: int = 65536
    workspace_id: str | None = None
    build_project_id: str | None = None
    tool_policy: dict[str, Any] | None = None
    files: list[EvalFileSeed] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    fixture_dir: str | None = None
    use_temp_workspace: bool = False
    temp_workspace_name: str = "workspace"
    temp_workspace_root: str | None = None
    patch_mode: bool = False


@dataclass
class EvalProfile:
    provider: str = "llamacpp"
    model: str = ""
    harness_profile: str = "default"


@dataclass
class EvalTurn:
    role: str = "user"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRule:
    type: str
    turn: int | None = None
    value: str | int | float | bool | None = None
    path: str | None = None
    command: str | None = None
    workdir: str | None = None
    forbidden_tool_names: list[str] = field(default_factory=list)
    forbidden_shell_patterns: list[str] = field(default_factory=list)


@dataclass
class EvalChecks:
    rules: list[EvalRule] = field(default_factory=list)


@dataclass
class EvalRubricDimension:
    name: str
    description: str = ""
    weight: float = 1.0


@dataclass
class EvalJudge:
    enabled: bool = False
    provider: str = ""
    model: str = ""
    rubric: list[str] = field(default_factory=list)


@dataclass
class EvalScenario:
    id: str
    title: str
    category: str
    goal: str = ""
    tags: list[str] = field(default_factory=list)
    path: str = ""
    notes_path: str | None = None
    suite_ids: list[str] = field(default_factory=list)
    profile: EvalProfile = field(default_factory=EvalProfile)
    setup: EvalSetup = field(default_factory=EvalSetup)
    turns: list[EvalTurn] = field(default_factory=list)
    checks: EvalChecks = field(default_factory=EvalChecks)
    judge: EvalJudge = field(default_factory=EvalJudge)
    forbidden: dict[str, Any] = field(default_factory=dict)
    expected_behavior: list[str] = field(default_factory=list)
    forbidden_behavior: list[str] = field(default_factory=list)
    scoring_rubric: list[EvalRubricDimension] = field(default_factory=list)
    required_observations: list[str] = field(default_factory=list)
    max_turns: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalSuite:
    id: str
    title: str
    scenario_ids: list[str] = field(default_factory=list)
    description: str = ""
    path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalCheckResult:
    type: str
    passed: bool
    details: str
    turn: int | None = None


@dataclass
class EvalTurnResult:
    turn_index: int
    role: str
    content: str
    run_id: str
    message_id: str
    status: str
    finish_reason: str
    final_text: str
    transient_text: str
    error: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    provider_route: dict[str, Any] | None = None
    run_events: list[dict[str, Any]] = field(default_factory=list)
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRunArtifact:
    schema_version: str
    artifact_id: str
    created_at: str
    api_base_url: str
    scenario: dict[str, Any]
    profile: dict[str, Any]
    baseline: dict[str, Any]
    session: dict[str, Any]
    summary: dict[str, Any]
    turns: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    metrics: dict[str, Any]
    judge: dict[str, Any]
    self_critique: dict[str, Any]
    replay: dict[str, Any]
    paths: dict[str, Any]
    trace: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, Any]] = field(default_factory=list)
    session_events: list[dict[str, Any]] = field(default_factory=list)


def dataclass_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_to_dict(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: dataclass_to_dict(val) for key, val in value.items()}
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    return value
