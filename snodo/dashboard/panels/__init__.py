"""Dashboard panel widgets.

FILE: snodo/dashboard/panels/__init__.py
"""

from snodo.dashboard.panels.jobs import JobsPanel
from snodo.dashboard.panels.agents import AgentsPanel
from snodo.dashboard.panels.plans import PlansPanel
from snodo.dashboard.panels.events import EventsPanel

__all__ = ["JobsPanel", "AgentsPanel", "PlansPanel", "EventsPanel"]
