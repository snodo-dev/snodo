"""Path classification for changes that govern test behavior (ADR 040)."""


def _is_test_governing_file(path: str) -> bool:
    """Check if a file path governs test behavior."""
    normalized = str(path).replace("\\", "/").strip().lower()
    parts = normalized.split("/")
    filename = parts[-1]

    if any(part in {"tests", "test", "spec", "specs"} for part in parts[:-1]):
        return True

    if (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".test.js")
        or filename.endswith(".test.ts")
        or filename.endswith(".spec.js")
        or filename.endswith(".spec.ts")
    ):
        return True

    governing_filenames = {
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "pyproject.toml",
        ".coveragerc",
        "setup.cfg",
        "cargo.toml",
        "package.json",
        "jest.config.js",
        "jest.config.ts",
        "vitest.config.ts",
    }
    return filename in governing_filenames
