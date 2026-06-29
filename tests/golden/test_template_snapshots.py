"""Golden-file snapshot tests for shipped protocol templates.

FILE: tests/golden/test_template_snapshots.py (Task 7.13)

Any change to the shipped YAML templates must produce a deliberate
golden-file update.  Accidental diffs fail the build.
"""

from .conftest import verify_golden


def test_solo_golden(snapshots_dir, update_goldens):
    verify_golden("solo", snapshots_dir, update_goldens)


def test_team_golden(snapshots_dir, update_goldens):
    verify_golden("team", snapshots_dir, update_goldens)


def test_2plus_n_golden(snapshots_dir, update_goldens):
    verify_golden("2+n", snapshots_dir, update_goldens)


def test_intent_golden(snapshots_dir, update_goldens):
    verify_golden("intent", snapshots_dir, update_goldens)
