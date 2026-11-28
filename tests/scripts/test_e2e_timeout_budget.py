"""Keep the e2e queue journey budget above the ordinary gate timeout."""

import ast
from pathlib import Path


def test_queue_journeys_keep_timeout_above_gate_limit():
    journey_file = Path(__file__).parents[2] / "tests" / "e2e" / "test_queue_run.py"
    tree = ast.parse(journey_file.read_text(encoding="utf-8"))

    timeout_seconds = None
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in statement.targets):
            continue
        markers = statement.value.elts if isinstance(statement.value, (ast.List, ast.Tuple)) else [statement.value]
        for marker in markers:
            if (
                isinstance(marker, ast.Call)
                and isinstance(marker.func, ast.Attribute)
                and marker.func.attr == "timeout"
                and marker.args
                and isinstance(marker.args[0], ast.Constant)
                and isinstance(marker.args[0].value, (int, float))
            ):
                timeout_seconds = marker.args[0].value

    assert timeout_seconds is not None and timeout_seconds > 60, (
        "queue journey tests must retain a timeout marker above the gate's 60-second limit"
    )
