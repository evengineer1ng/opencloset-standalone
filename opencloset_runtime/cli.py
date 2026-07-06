from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from evals.cli import main as evals_main
from opencloset_runtime.booth_feed_baker import (
    bake_spec_file,
    process_spec_queue,
    write_agent_event_payload,
)


DEFAULT_API_BASE = os.environ.get("OPENCLOSET_API_BASE", "http://127.0.0.1:5000")


def _request(method: str, path: str, *, payload: dict | None = None, base_url: str = DEFAULT_API_BASE):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {"error": body or exc.reason}
        return exc.code, payload


def _print_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _load_payload(args) -> dict:
    if getattr(args, "payload", None):
        return json.loads(args.payload)
    if getattr(args, "payload_file", None):
        with open(args.payload_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _cmd_agent_start(args) -> int:
    status, payload = _request(
        "POST",
        "/api/runtime/agents",
        payload={
            "name": args.name,
            "mode": args.mode,
            "domain": args.domain,
            "objective": args.objective,
            "workspace_id": args.workspace_id,
            "provider": args.provider,
            "model": args.model,
        },
        base_url=args.api_base,
    )
    _print_json(payload)
    return 0 if status < 400 else 1


def _cmd_agent_send(args) -> int:
    status, payload = _request(
        "POST",
        f"/api/runtime/agents/{args.name}/send",
        payload={
            "content": args.content,
            "source": args.source,
            "priority": args.priority,
            "sync": args.sync,
        },
        base_url=args.api_base,
    )
    _print_json(payload)
    return 0 if status < 400 else 1


def _cmd_agent_event(args) -> int:
    payload = _load_payload(args)
    status, body = _request(
        "POST",
        f"/api/runtime/agents/{args.name}/events",
        payload={
            "type": args.type,
            "source": args.source,
            "text": args.text,
            "priority": args.priority,
            "sync": args.sync,
            "payload": payload,
        },
        base_url=args.api_base,
    )
    _print_json(body)
    return 0 if status < 400 else 1


def _cmd_agent_status(args) -> int:
    status, payload = _request("GET", f"/api/runtime/agents/{args.name}", base_url=args.api_base)
    _print_json(payload)
    return 0 if status < 400 else 1


def _cmd_agent_stop(args) -> int:
    status, payload = _request("POST", f"/api/runtime/agents/{args.name}/stop", payload={}, base_url=args.api_base)
    _print_json(payload)
    return 0 if status < 400 else 1


def _cmd_agent_schedule(args) -> int:
    payload: dict[str, object] = {"enabled": not args.disable}
    if args.cooldown_seconds is not None:
        payload["cooldown_seconds"] = args.cooldown_seconds
    if args.next_run_at:
        payload["next_run_at"] = args.next_run_at
    if args.payload:
        payload["payload"] = json.loads(args.payload)
    status, body = _request(
        "POST",
        f"/api/runtime/agents/{args.name}/schedule",
        payload=payload,
        base_url=args.api_base,
    )
    _print_json(body)
    return 0 if status < 400 else 1


def _cmd_agent_subscribe(args) -> int:
    url = urllib.parse.urljoin(
        args.api_base.rstrip("/") + "/",
        f"api/runtime/agents/{args.name}/stream",
    )
    request = urllib.request.Request(url, headers={"Accept": "text/event-stream"}, method="GET")
    with urllib.request.urlopen(request) as response:
        event_name = "message"
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    parsed = {"raw": data}
                sys.stdout.write(json.dumps({"event": event_name, "data": parsed}, sort_keys=True))
                sys.stdout.write("\n")
                sys.stdout.flush()
    return 0


def _cmd_eval(args) -> int:
    forwarded: list[str] = [args.eval_command]
    if args.eval_command == "run":
        if args.suite:
            forwarded.extend(["--suite", args.suite])
        if args.scenario:
            forwarded.extend(["--scenario", args.scenario])
        if args.provider:
            forwarded.extend(["--provider", args.provider])
        if args.model:
            forwarded.extend(["--model", args.model])
        if args.harness_profile:
            forwarded.extend(["--harness-profile", args.harness_profile])
        if args.judge:
            forwarded.append("--judge")
        if args.judge_provider:
            forwarded.extend(["--judge-provider", args.judge_provider])
        if args.judge_model:
            forwarded.extend(["--judge-model", args.judge_model])
        if args.repeat is not None:
            forwarded.extend(["--repeat", str(args.repeat)])
        if args.no_stream:
            forwarded.append("--no-stream")
        if args.keep_session:
            forwarded.append("--keep-session")
        forwarded.extend(["--api-base-url", args.api_base.rstrip("/") + "/api"])
    elif args.eval_command == "compare":
        if args.baseline:
            forwarded.extend(["--baseline", args.baseline])
        if args.current:
            forwarded.extend(["--current", args.current])
        if args.scenario:
            forwarded.extend(["--scenario", args.scenario])
        if args.suite:
            forwarded.extend(["--suite", args.suite])
        if args.provider:
            forwarded.extend(["--provider", args.provider])
        if args.model:
            forwarded.extend(["--model", args.model])
        if args.harness_profile:
            forwarded.extend(["--harness-profile", args.harness_profile])
        if args.json:
            forwarded.append("--json")
    return int(evals_main(forwarded))


def _cmd_booth_bake_feeds(args) -> int:
    artifact = bake_spec_file(
        args.spec,
        out_path=args.out,
        inline_js_out=args.inline_js_out,
        name=args.name,
    )
    _print_json(
        {
            "status": "ok",
            "artifact_path": args.out,
            "inline_js_out": args.inline_js_out,
            "tape_key": artifact["tape_key"],
            "item_count": artifact["snapshot"]["item_count"],
            "plugins": artifact["snapshot"]["plugins"],
        }
    )
    return 0


def _cmd_booth_bake_queue(args) -> int:
    payload = process_spec_queue(
        args.inbox,
        out_dir=args.out_dir,
        archive_dir=args.archive_dir,
        write_inline_js=not args.no_inline_js,
    )
    _print_json(payload)
    return 0


def _cmd_booth_agent_payload(args) -> int:
    payload = write_agent_event_payload(args.artifact, args.out)
    _print_json({"status": "ok", "payload_path": args.out, "event_type": payload["type"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oc", description="OpenCloset runtime CLI")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="OpenCloset API base URL")

    subparsers = parser.add_subparsers(dest="command", required=True)
    agent_parser = subparsers.add_parser("agent", help="Manage headless agent channels")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)

    start_parser = agent_subparsers.add_parser("start", help="Start or resume an agent channel")
    start_parser.add_argument("name")
    start_parser.add_argument("--mode", default="ambient")
    start_parser.add_argument("--domain", default="general")
    start_parser.add_argument("--objective", default="")
    start_parser.add_argument("--workspace-id")
    start_parser.add_argument("--provider", default="auto")
    start_parser.add_argument("--model")
    start_parser.set_defaults(func=_cmd_agent_start)

    send_parser = agent_subparsers.add_parser("send", help="Send a user message into an agent channel")
    send_parser.add_argument("name")
    send_parser.add_argument("content")
    send_parser.add_argument("--source", default="user")
    send_parser.add_argument("--priority", type=int, default=50)
    send_parser.add_argument("--sync", action="store_true")
    send_parser.set_defaults(func=_cmd_agent_send)

    event_parser = agent_subparsers.add_parser("event", help="Ingest a structured runtime event")
    event_parser.add_argument("name")
    event_parser.add_argument("--type", required=True)
    event_parser.add_argument("--source", default="external")
    event_parser.add_argument("--text", default="")
    event_parser.add_argument("--priority", type=int, default=0)
    event_parser.add_argument("--payload")
    event_parser.add_argument("--payload-file")
    event_parser.add_argument("--sync", action="store_true")
    event_parser.set_defaults(func=_cmd_agent_event)

    status_parser = agent_subparsers.add_parser("status", help="Show channel status")
    status_parser.add_argument("name")
    status_parser.set_defaults(func=_cmd_agent_status)

    subscribe_parser = agent_subparsers.add_parser("subscribe", help="Stream channel events as SSE")
    subscribe_parser.add_argument("name")
    subscribe_parser.set_defaults(func=_cmd_agent_subscribe)

    stop_parser = agent_subparsers.add_parser("stop", help="Stop an agent channel")
    stop_parser.add_argument("name")
    stop_parser.set_defaults(func=_cmd_agent_stop)

    schedule_parser = agent_subparsers.add_parser("schedule", help="Configure a channel self-tick schedule")
    schedule_parser.add_argument("name")
    schedule_parser.add_argument("--cooldown-seconds", type=int)
    schedule_parser.add_argument("--next-run-at")
    schedule_parser.add_argument("--payload")
    schedule_parser.add_argument("--disable", action="store_true")
    schedule_parser.set_defaults(func=_cmd_agent_schedule)

    eval_parser = subparsers.add_parser("eval", help="Run OpenCloset behavioral evals")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)

    eval_run = eval_subparsers.add_parser("run", help="Run one scenario or a named suite")
    eval_run.add_argument("--suite")
    eval_run.add_argument("--scenario")
    eval_run.add_argument("--provider")
    eval_run.add_argument("--model")
    eval_run.add_argument("--harness-profile")
    eval_run.add_argument("--judge", action="store_true")
    eval_run.add_argument("--judge-provider")
    eval_run.add_argument("--judge-model")
    eval_run.add_argument("--repeat", type=int, default=1)
    eval_run.add_argument("--no-stream", action="store_true")
    eval_run.add_argument("--keep-session", action="store_true")
    eval_run.set_defaults(func=_cmd_eval)

    eval_compare = eval_subparsers.add_parser("compare", help="Compare eval artifacts")
    eval_compare.add_argument("--baseline")
    eval_compare.add_argument("--current")
    eval_compare.add_argument("--scenario")
    eval_compare.add_argument("--suite")
    eval_compare.add_argument("--provider")
    eval_compare.add_argument("--model")
    eval_compare.add_argument("--harness-profile")
    eval_compare.add_argument("--json", action="store_true")
    eval_compare.set_defaults(func=_cmd_eval)

    booth_parser = subparsers.add_parser("booth", help="Bake deterministic booth feed artifacts")
    booth_subparsers = booth_parser.add_subparsers(dest="booth_command", required=True)

    booth_bake = booth_subparsers.add_parser("bake-feeds", help="Bake one Radio-OS-style feed spec into a booth artifact")
    booth_bake.add_argument("--spec", required=True, help="Path to a feed spec JSON file with a feeds object")
    booth_bake.add_argument("--out", required=True, help="Where to write the baked artifact JSON")
    booth_bake.add_argument("--inline-js-out", help="Optional path to write a booth inline JS snippet")
    booth_bake.add_argument("--name", help="Optional tape key override")
    booth_bake.set_defaults(func=_cmd_booth_bake_feeds)

    booth_queue = booth_subparsers.add_parser("bake-queue", help="Process a directory of booth feed specs")
    booth_queue.add_argument("--inbox", required=True, help="Directory containing *.json feed specs")
    booth_queue.add_argument("--out-dir", required=True, help="Directory to receive baked artifacts")
    booth_queue.add_argument("--archive-dir", help="Optional directory to copy processed specs into")
    booth_queue.add_argument("--no-inline-js", action="store_true", help="Skip writing .inline.js snippets")
    booth_queue.set_defaults(func=_cmd_booth_bake_queue)

    booth_payload = booth_subparsers.add_parser("agent-payload", help="Create a runtime event payload from a baked booth artifact")
    booth_payload.add_argument("--artifact", required=True, help="Path to a baked booth artifact JSON")
    booth_payload.add_argument("--out", required=True, help="Where to write the runtime event payload JSON")
    booth_payload.set_defaults(func=_cmd_booth_agent_payload)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
