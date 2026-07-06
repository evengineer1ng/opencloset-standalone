from __future__ import annotations


def format_task(name: str, done: bool = False) -> str:
    status = "done" if done else "todo"
    return f"[{status}] {name}"


def list_tasks(tasks: list[dict]) -> list[str]:
    return [format_task(task["name"], bool(task.get("done"))) for task in tasks]
