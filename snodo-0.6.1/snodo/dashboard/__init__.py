"""Snodo TUI Dashboard - Real-time observability.

FILE: snodo/dashboard/__init__.py (Task 5.3)

Like k9s for AI-SDLC. Shows active jobs, agents, plans, PRs, and events.
"""

from snodo.dashboard.app import SnodoDashboard

__all__ = ["SnodoDashboard"]
