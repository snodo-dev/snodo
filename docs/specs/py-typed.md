# py.typed marker (PEP 561)

Goal: ship inline type hints to downstream users of `import snodo`.

1. Add empty file snodo/py.typed (in the import package dir).
2. Ensure it's included in the built wheel — depends on build-backend in pyproject.toml:
   - hatchling: usually automatic; confirm nothing excludes it.
   - setuptools: add [tool.setuptools.package-data] with snodo = ["py.typed"] (or include-package-data).
3. Verify: python -m build, then `unzip -l dist/*.whl | grep py.typed` — confirm it's in the wheel.
4. Do NOT bump the version or republish — ships on the next release.

Read pyproject.toml first; ground the change in what's there.
