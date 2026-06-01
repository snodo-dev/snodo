"""Allow python -m snodo invocation (e.g. sys.executable -m snodo)."""

from snodo.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
