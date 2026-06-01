#!/usr/bin/env python3
"""Headless runner for all Wave 8 studies.

Usage:
    python studies/run_all.py              # run all studies
    python studies/run_all.py _smoke        # run a single study

Each study is a marimo notebook.  The runner exports it to a flat
script via `marimo export script`, then executes the script in a
subprocess with the studies/ directory on sys.path so _common is
importable.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

STUDIES_ROOT = Path(__file__).resolve().parent

# Studies that have real implementations (not stubs).
# As 8.2-8.5 are implemented, add their names here.
IMPLEMENTED = {"_smoke", "policy_monte_carlo", "overhead_benchmarks",
               "detection_probability", "byzantine_robustness"}

STUBS = set()


def run_study(name: str) -> int:
    """Export a marimo notebook to a flat script and execute it headless."""
    notebook = STUDIES_ROOT / name / "notebook.py"
    if not notebook.exists():
        print(f"Error: study '{name}' not found at {notebook}", file=sys.stderr)
        return 1

    # Export the notebook to a flat script via marimo
    export = subprocess.run(
        [sys.executable, "-m", "marimo", "export", "script", str(notebook), "--no-sandbox"],
        cwd=str(STUDIES_ROOT),
        capture_output=True,
        text=True,
    )
    if export.returncode != 0:
        print(f"marimo export failed:\n{export.stderr}", file=sys.stderr)
        return 1

    # Write the exported script to a temp file and execute it
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix=f"marimo_{name}_", delete=False
    ) as f:
        f.write(export.stdout)
        script_path = f.name

    try:
        # Add studies/ to PYTHONPATH so _common is importable.
        # Set MARIMO_STUDY_OUTPUT so io.py writes to the right outputs/.
        import os as _os
        env = {
            **_os.environ,
            "PYTHONPATH": str(STUDIES_ROOT),
            "MARIMO_STUDY_OUTPUT": str(STUDIES_ROOT / name / "outputs"),
        }
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=str(STUDIES_ROOT / name),
            env=env,
            capture_output=False,
        )
        return result.returncode
    finally:
        Path(script_path).unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) > 1:
        names = [sys.argv[1]]
    else:
        names = sorted(IMPLEMENTED | STUBS)

    failed = []
    for name in names:
        is_stub = name in STUBS
        label = f"{name} (stub)" if is_stub else name
        print(f"\n{'=' * 50}")
        print(f"  Study: {label}")
        print(f"{'=' * 50}")
        rc = run_study(name)
        if rc == 0:
            print("  OK")
        elif is_stub:
            # Stubs may exit non-zero — expected
            print(f"  exit code {rc} (ignored — stub)")
        else:
            print(f"  FAILED (exit code {rc})", file=sys.stderr)
            failed.append(name)

    if failed:
        print(f"\n{len(failed)} study(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nAll studies complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
