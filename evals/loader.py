from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

import yaml

from evals.schema import (
    EvalChecks,
    EvalFileSeed,
    EvalJudge,
    EvalProfile,
    EvalRubricDimension,
    EvalRule,
    EvalScenario,
    EvalSetup,
    EvalSuite,
    EvalTurn,
)


def _expand_placeholders(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return Template(value).safe_substitute(variables)
    if isinstance(value, list):
        return [_expand_placeholders(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _expand_placeholders(item, variables) for key, item in value.items()}
    return value


def _load_yaml_object(path: Path, *, repo_root: str) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML file must contain an object: {path}")
    variables = {
        "repo_root": str(Path(repo_root).resolve()),
        "scenario_dir": str(path.parent),
        "evals_root": str((Path(repo_root).resolve() / "evals")),
    }
    return _expand_placeholders(raw, variables)


def _coerce_turns(data: dict[str, Any]) -> list[EvalTurn]:
    raw_turns = list(data.get("turns") or [])
    if raw_turns:
        return [EvalTurn(**item) for item in raw_turns]
    user_prompt = data.get("user_prompt")
    if isinstance(user_prompt, str) and user_prompt.strip():
        return [EvalTurn(role="user", content=user_prompt.strip())]
    return []


def _coerce_rubric(data: dict[str, Any]) -> list[EvalRubricDimension]:
    raw_rubric = data.get("scoring_rubric") or []
    dimensions: list[EvalRubricDimension] = []
    for item in raw_rubric:
        if isinstance(item, str):
            dimensions.append(EvalRubricDimension(name=item))
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            dimensions.append(
                EvalRubricDimension(
                    name=name,
                    description=str(item.get("description") or "").strip(),
                    weight=float(item.get("weight") or 1.0),
                )
            )
    return dimensions


def load_scenario(path: str | Path, *, repo_root: str) -> EvalScenario:
    scenario_path = Path(path).resolve()
    data = _load_yaml_object(scenario_path, repo_root=repo_root)

    profile = EvalProfile(**dict(data.get("profile") or {}))

    setup_raw = dict(data.get("setup") or data.get("setup_state") or {})
    files = [EvalFileSeed(**item) for item in setup_raw.pop("files", [])]
    setup = EvalSetup(files=files, **setup_raw)
    turns = _coerce_turns(data)
    rules = [EvalRule(**item) for item in list((data.get("checks") or {}).get("rules", []) or [])]
    checks = EvalChecks(rules=rules)
    judge = EvalJudge(**dict(data.get("judge") or {}))

    notes_path = scenario_path.with_suffix(".md")
    return EvalScenario(
        id=str(data.get("id") or scenario_path.stem),
        title=str(data.get("title") or scenario_path.stem),
        category=str(data.get("category") or "uncategorized"),
        goal=str(data.get("goal") or ""),
        tags=list(data.get("tags") or []),
        path=str(scenario_path),
        notes_path=str(notes_path) if notes_path.exists() else None,
        suite_ids=list(data.get("suite_ids") or []),
        profile=profile,
        setup=setup,
        turns=turns,
        checks=checks,
        judge=judge,
        forbidden=dict(data.get("forbidden") or {}),
        expected_behavior=[str(item) for item in list(data.get("expected_behavior") or []) if str(item).strip()],
        forbidden_behavior=[str(item) for item in list(data.get("forbidden_behavior") or []) if str(item).strip()],
        scoring_rubric=_coerce_rubric(data),
        required_observations=[str(item) for item in list(data.get("required_observations") or []) if str(item).strip()],
        max_turns=(int(data["max_turns"]) if data.get("max_turns") is not None else None),
        raw=data,
    )


def load_suite(path: str | Path, *, repo_root: str) -> EvalSuite:
    suite_path = Path(path).resolve()
    data = _load_yaml_object(suite_path, repo_root=repo_root)
    scenario_ids = [str(item) for item in list(data.get("scenario_ids") or data.get("scenarios") or []) if str(item).strip()]
    return EvalSuite(
        id=str(data.get("id") or suite_path.stem),
        title=str(data.get("title") or suite_path.stem),
        scenario_ids=scenario_ids,
        description=str(data.get("description") or ""),
        path=str(suite_path),
        raw=data,
    )


def find_scenario_paths_by_ids(
    scenario_ids: list[str],
    *,
    repo_root: str,
    scenarios_dir: str | Path | None = None,
) -> list[Path]:
    root = Path(scenarios_dir or (Path(repo_root).resolve() / "evals" / "scenarios")).resolve()
    available = {path.stem: path for path in root.rglob("*.yaml")}
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in available]
    if missing:
        raise FileNotFoundError(f"Unknown scenario ids: {', '.join(missing)}")
    return [available[scenario_id] for scenario_id in scenario_ids]
