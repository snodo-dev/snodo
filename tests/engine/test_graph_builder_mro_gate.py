"""Canary gate test to prevent GraphBuilder method shadowing over its mixins.

FILE: tests/engine/test_graph_builder_mro_gate.py
"""

import importlib
import pkgutil

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


def test_no_duplicate_class_definitions_under_engine():
    """Fail if the same class name is defined in more than one module under snodo/engine/.

    This is the general form of the #100 shadowing defect, one layer down: a
    module-level class (e.g. LoopState) defined twice in different modules and
    kept in sync by hand. The MRO gate above cannot catch it because it only
    compares GraphBuilder methods against its mixins. #103 had to add base_ref
    to both LoopState definitions, and nothing would have failed if it had been
    added to only one (Fixes #107).
    """
    import snodo.engine as engine_pkg

    defined: dict[str, list[str]] = {}
    for mod_info in pkgutil.walk_packages(
        engine_pkg.__path__, prefix=engine_pkg.__name__ + "."
    ):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:
            continue
        for name, val in vars(mod).items():
            if isinstance(val, type) and val.__module__ == mod_info.name:
                defined.setdefault(name, []).append(mod_info.name)

    duplicates = {name: mods for name, mods in defined.items() if len(mods) > 1}
    assert not duplicates, (
        f"Class name(s) defined in more than one module under snodo/engine/: "
        f"{duplicates}. A module-level class must live in exactly one module; "
        "re-export it elsewhere instead of redefining it."
    )
