# Support running snodo inside a git subdirectory (monorepo)

Problem: `snodo serve` fails with "Not a git repository: <path>" when the workspace is a subdirectory of a git repo (e.g. snodo-cloud/app, with .git at snodo-cloud/). snodo checks the exact workspace dir for .git and doesn't walk up.

Fix: where the repo is opened via GitPython, use Repo(path, search_parent_directories=True) so it resolves the enclosing repo from any subdir. Find the call site (ADR 010 / git.py) and apply it there.

Nuance to handle: workspace/.snodo may live in a subdir while the git root is higher up — keep them decoupled. .snodo stays where it is; git operations + audit anchor to the resolved repo root, not the subdir.

Confirm: `snodo serve` works from a monorepo subdir; resolved repo root is the enclosing .git root, and a git subdir no longer errors.
