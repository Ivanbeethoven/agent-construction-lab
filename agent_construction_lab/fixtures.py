"""Public task specifications recovered from the archived split manifest.

No scores, execution traces, or query labels are loaded here.
"""
import json
from pathlib import Path
from .contracts import TaskSpec


def load_fixture_splits():
    raw = json.loads(Path(__file__).with_name("task_fixtures.json").read_text(encoding="utf-8"))
    return {role: [TaskSpec.from_dict(task) for task in tasks] for role, tasks in raw.items()}


def get_tasks(split):
    return load_fixture_splits()[split]
