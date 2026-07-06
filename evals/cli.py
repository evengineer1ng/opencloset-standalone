from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.schema import dataclass_to_dict
from evals.loader import find_scenario_paths_by_ids, load_scenario, load_suite
from evals.reporting import write_suite_reports
from evals.runner import ApiHarnessEvalRunner, compare_artifacts, compare_suite_artifacts, find_matching_artifacts, load_artifact


def _default_api_base_url() -> str:
    return "http://127.0.0.1:5000/api"


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_scenarios_dir() -> Path:
    return _default_repo_root() / "evals" / "scenarios"


def _default_suites_dir() -> Path:
    return _default_repo_root() / "evals" / "suites"


def _scenario_paths(target: str) -> list[Path]:
    candidate = Path(target)
    if candidate.is_dir():
        return sorted(candidate.rglob("*.yaml"))
    if any(char in target for char in "*?[]"):
        return sorted(Path().glob(target))
    return [candidate]


def _print_run_summary(artifact: dict) -> None:
    summary = artifact["summary"]
    metrics = artifact["metrics"]
    judge = artifact.get("judge") or {}
    print("")
    print(f"Scenario: {artifact['scenario']['id']}")
    print(f"Status:   {summary['status']} ({summary['finish_reason']})")
    print(f"Hard pass:{summary['hard_pass']}")
    print(f"Latency:  {metrics['latency_ms_total']}ms total / {metrics['latency_ms_avg']}ms avg")
    print(f"Tokens:   in {metrics['input_tokens_total']} / out {metrics['output_tokens_total']}")
    print(f"Tools:    {metrics['tool_call_count']}")
    if judge.get("status") == "scored":
        print(f"Judge:    {judge.get('overall_score')} ({judge.get('verdict')})")
        print(f"Patch:    {judge.get('recommended_patch_target') or 'n/a'}")
    else:
        print(f"Judge:    {judge.get('status')}")
    print(f"Artifact: {artifact['paths']['artifact']}")


def _print_suite_summary(artifacts: list[dict], *, report_paths: dict[str, str] | None = None) -> None:
    print("")
    print("Scenario".ljust(34) + "Status".ljust(14) + "HardPass".ljust(10) + "Judge".ljust(8) + "Patch Target")
    print("-" * 92)
    for artifact in artifacts:
        scenario_id = str(artifact["scenario"]["id"])[:32]
        status = str(artifact["summary"]["status"])[:12]
        hard_pass = "yes" if artifact["summary"]["hard_pass"] else "no"
        judge_score = str((artifact.get("judge") or {}).get("overall_score") or "")
        patch_target = str((artifact.get("judge") or {}).get("recommended_patch_target") or "")[:24]
        print(scenario_id.ljust(34) + status.ljust(14) + hard_pass.ljust(10) + judge_score.ljust(8) + patch_target)
    if report_paths:
        print("")
        print(f"Summary report:  {report_paths['summary']}")
        print(f"Trace report:    {report_paths['trace']}")
        print(f"Failure report:  {report_paths['failures']}")


def _print_compare_summary(diff: dict) -> None:
    if "compared_count" in diff:
        summary = diff.get("summary") or {}
        print("")
        print(f"Compared: {diff.get('compared_count')} / {diff.get('suite_size')} scenarios")
        print(f"Skipped:  {diff.get('skipped_count')}")
        print(f"Hard pass improved:  {summary.get('hard_pass_improved')}")
        print(f"Hard pass regressed: {summary.get('hard_pass_regressed')}")
        print(f"Status changed:      {summary.get('status_changed')}")
        print(f"Avg latency delta:   {summary.get('avg_latency_delta_ms')} ms")
        print(f"Avg judge delta:     {summary.get('avg_judge_score_delta')}")
        return
    print("")
    print(f"Scenario: {diff['scenario_id']}")
    print(f"Baseline: {diff['artifact_a']}")
    print(f"Current:  {diff['artifact_b']}")
    print("")
    print("Field".ljust(24) + "Baseline".ljust(16) + "Current".ljust(16) + "Delta")
    print("-" * 72)
    print("status".ljust(24) + str(diff["status"]["a"]).ljust(16) + str(diff["status"]["b"]).ljust(16))
    print("hard_pass".ljust(24) + str(diff["hard_pass"]["a"]).ljust(16) + str(diff["hard_pass"]["b"]).ljust(16))
    print("judge_overall".ljust(24) + str(diff["judge_overall_score"]["a"]).ljust(16) + str(diff["judge_overall_score"]["b"]).ljust(16) + str(diff["judge_overall_score"]["delta"]))
    for key, value in (diff.get("metrics") or {}).items():
        print(key.ljust(24) + str(value.get("a")).ljust(16) + str(value.get("b")).ljust(16) + str(value.get("delta")))
    print("patch_target".ljust(24) + str(diff.get("patch_target", {}).get("a")).ljust(16) + str(diff.get("patch_target", {}).get("b")).ljust(16))


def _resolve_suite_paths(args) -> tuple[str, list[Path]]:
    if args.suite:
        suite_path = Path(args.suite)
        if not suite_path.exists():
            suite_path = _default_suites_dir() / f"{args.suite}.yaml"
        suite = load_suite(suite_path, repo_root=args.repo_root)
        return suite.id, find_scenario_paths_by_ids(suite.scenario_ids, repo_root=args.repo_root)
    if args.scenario:
        scenario_path = Path(args.scenario)
        if not scenario_path.exists():
            scenario_path = _default_scenarios_dir() / f"{args.scenario}.yaml"
        loaded = load_scenario(scenario_path, repo_root=args.repo_root)
        return loaded.id, [scenario_path]
    raise ValueError("either --suite or --scenario is required")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opencloset-eval", description="OpenCloset E2E eval harness CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one scenario or a named suite")
    run_parser.add_argument("--suite")
    run_parser.add_argument("--scenario")
    run_parser.add_argument("--api-base-url", default=_default_api_base_url())
    run_parser.add_argument("--repo-root", default=str(_default_repo_root()))
    run_parser.add_argument("--out-dir", default=str(_default_repo_root() / "evals" / "runs"))
    run_parser.add_argument("--reports-dir", default=str(_default_repo_root() / "evals" / "reports"))
    run_parser.add_argument("--provider")
    run_parser.add_argument("--model")
    run_parser.add_argument("--harness-profile")
    run_parser.add_argument("--judge", action="store_true")
    run_parser.add_argument("--judge-provider")
    run_parser.add_argument("--judge-model")
    run_parser.add_argument("--repeat", type=int, default=1)
    run_parser.add_argument("--no-stream", action="store_true")
    run_parser.add_argument("--keep-session", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="Compare suite or artifact baselines")
    compare_parser.add_argument("--baseline")
    compare_parser.add_argument("--current")
    compare_parser.add_argument("--scenario")
    compare_parser.add_argument("--suite")
    compare_parser.add_argument("--out-dir", default=str(_default_repo_root() / "evals" / "runs"))
    compare_parser.add_argument("--repo-root", default=str(_default_repo_root()))
    compare_parser.add_argument("--provider")
    compare_parser.add_argument("--model")
    compare_parser.add_argument("--harness-profile")
    compare_parser.add_argument("--json", action="store_true")

    suite_parser = subparsers.add_parser("suite", help="Legacy alias for run --suite")
    suite_parser.add_argument("target")
    suite_parser.add_argument("--api-base-url", default=_default_api_base_url())
    suite_parser.add_argument("--repo-root", default=str(_default_repo_root()))
    suite_parser.add_argument("--out-dir", default=str(_default_repo_root() / "evals" / "runs"))
    suite_parser.add_argument("--reports-dir", default=str(_default_repo_root() / "evals" / "reports"))
    suite_parser.add_argument("--provider")
    suite_parser.add_argument("--model")
    suite_parser.add_argument("--harness-profile")
    suite_parser.add_argument("--judge", action="store_true")
    suite_parser.add_argument("--judge-provider")
    suite_parser.add_argument("--judge-model")
    suite_parser.add_argument("--repeat", type=int, default=1)
    suite_parser.add_argument("--no-stream", action="store_true")
    suite_parser.add_argument("--keep-session", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"run", "suite"}:
        if args.command == "suite":
            args.suite = args.target
            args.scenario = None
        suite_id, scenario_paths = _resolve_suite_paths(args)
        runner = ApiHarnessEvalRunner(
            api_base_url=args.api_base_url,
            repo_root=args.repo_root,
            out_dir=args.out_dir,
        )
        profile_overrides = {
            "provider": args.provider,
            "model": args.model,
            "harness_profile": args.harness_profile,
        }
        artifacts: list[dict] = []
        for _ in range(max(1, int(args.repeat))):
            for path in scenario_paths:
                scenario = load_scenario(path, repo_root=args.repo_root)
                print(f"== Running {scenario.id} ==")
                artifact = runner.run_scenario(
                    scenario,
                    stream=not args.no_stream,
                    keep_session=args.keep_session,
                    profile_overrides=profile_overrides,
                    judge_override=args.judge,
                    judge_provider=args.judge_provider,
                    judge_model=args.judge_model,
                )
                artifact_dict = dataclass_to_dict(artifact)
                artifacts.append(artifact_dict)
                _print_run_summary(artifact_dict)
        report_paths = write_suite_reports(reports_dir=args.reports_dir, suite_id=suite_id, artifacts=artifacts)
        _print_suite_summary(artifacts, report_paths=report_paths)
        return 0

    if args.command == "compare":
        if args.baseline and args.current:
            diff = compare_artifacts(load_artifact(args.baseline), load_artifact(args.current))
        elif args.suite:
            suite_path = Path(args.suite)
            if not suite_path.exists():
                suite_path = _default_suites_dir() / f"{args.suite}.yaml"
            suite = load_suite(suite_path, repo_root=args.repo_root)
            diff = compare_suite_artifacts(
                args.out_dir,
                scenario_ids=suite.scenario_ids,
                provider=args.provider,
                model=args.model,
                harness_profile=args.harness_profile,
            )
        elif args.scenario:
            matches = find_matching_artifacts(
                args.out_dir,
                scenario_id=args.scenario,
                provider=args.provider,
                model=args.model,
                harness_profile=args.harness_profile,
            )
            if len(matches) < 2:
                parser.error("need at least two matching scenario artifacts to compare")
            diff = compare_artifacts(matches[-2], matches[-1])
        else:
            parser.error("compare requires either --baseline/--current, --suite, or --scenario")

        if args.json:
            print(json.dumps(diff, indent=2))
        else:
            _print_compare_summary(diff)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2
