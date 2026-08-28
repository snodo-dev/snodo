"""Canary gate test to prevent GraphBuilder method shadowing over its mixins.

FILE: tests/engine/test_graph_builder_mro_gate.py
"""

from snodo.engine.loop import GraphBuilder


def test_graph_builder_does_not_shadow_mixin_methods():
    """Fail if any method defined in GraphBuilder's own __dict__ is also in a mixin's __dict__."""
    mixins = [cls for cls in GraphBuilder.__mro__ if cls not in (GraphBuilder, object)]

    gb_dict = GraphBuilder.__dict__
    shadowed_methods = []

    for name, val in gb_dict.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if not callable(val) and not isinstance(val, (staticmethod, classmethod)):
            continue

        for mixin in mixins:
            if name in mixin.__dict__:
                shadowed_methods.append((name, mixin.__name__))
                break

    assert not shadowed_methods, (
        f"GraphBuilder redefines/shadows methods already defined in its mixins: {shadowed_methods}. "
        "Move the live method body to the mixin or remove the duplicate from GraphBuilder."
    )
