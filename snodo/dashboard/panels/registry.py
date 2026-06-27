"""Panel registry for Snodo TUI Dashboard.

FILE: snodo/dashboard/panels/registry.py
"""

from typing import Callable, Dict, Any, List

_PANELS: Dict[str, Callable[..., Any]] = {}


def register_panel(name: str):
    """Decorator to register a panel factory class or function."""
    def decorator(factory: Callable[..., Any]):
        _PANELS[name] = factory
        return factory
    return decorator


def get_panel(name: str, *args, **kwargs) -> Any:
    """Retrieve and instantiate a registered panel."""
    if name not in _PANELS:
        raise ValueError(f"Panel '{name}' is not registered.")
    return _PANELS[name](*args, **kwargs)


def list_panels() -> List[str]:
    """List all registered panel names."""
    return list(_PANELS.keys())
