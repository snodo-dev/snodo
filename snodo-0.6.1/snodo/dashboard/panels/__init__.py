"""Dashboard panels package.

FILE: snodo/dashboard/panels/__init__.py
"""

import importlib
import pkgutil

from snodo.dashboard.panels.registry import register_panel, get_panel, list_panels

# Expose registry functions at package level
__all__ = ["register_panel", "get_panel", "list_panels"]

# Auto-import discovery loop to register all panels inside the panels/ subpackage
# Since we are in snodo.dashboard.panels __init__.py, __path__ is available
for _, module_name, _ in pkgutil.walk_packages(__path__, __name__ + "."):
    importlib.import_module(module_name)
