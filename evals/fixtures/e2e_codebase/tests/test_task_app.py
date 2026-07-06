from task_app import format_task, list_tasks


def test_format_task_todo():
    assert format_task("ship eval harness") == "[todo] ship eval harness"


def test_format_task_done():
    assert format_task("ship eval harness", True) == "[done] ship eval harness"


def test_list_tasks_preserves_order():
    tasks = [
        {"name": "inspect code", "done": True},
        {"name": "write patch", "done": False},
    ]
    assert list_tasks(tasks) == [
        "[done] inspect code",
        "[todo] write patch",
    ]
