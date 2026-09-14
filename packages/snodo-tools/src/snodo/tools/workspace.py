"""Workspace MCP server for sandboxed file operations.

FILE: snodo/mcp/workspace.py

Implements INV2 capability boundaries - enforces project root and
prevents directory traversal attacks.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple
import functools
import os
import re
import threading


# Version-control bookkeeping, never the work under judgement.  The comparison
# is case-insensitive on purpose: on the case-insensitive filesystem this
# project runs on, ``.GIT/logs/HEAD`` opens the repository's real reflog while
# its path parts spell ``.GIT``.
_VCS_INTERNAL_NAMES = frozenset({".git"})

# The tooling's own directory stays readable even though every project
# gitignores it (ADR 026): the protocol is context a judge is entitled to.
_READABLE_IGNORED_DIRS = frozenset({".snodo"})

# Snodo's own state directory is not source a text search or symbol search
# should scan, so a walk does not descend it.  This is snodo's own knowledge —
# ``.snodo`` is a name snodo defines — not a guess about which of a project's
# directories are generated; that question is answered by the project's own
# ignore declaration (see ``_refuse_derived_output``).
_SEARCH_PRUNED_DIRS = frozenset({".git", ".snodo"})

# Bounds for summarize_directory: a judge must be able to read the whole
# response in one turn, so each record stays small and the response says
# plainly when it stopped early rather than silently returning a prefix.
_MAX_SUMMARY_FILES = 200
_MAX_SUMMARY_CHARS = 8000
_MAX_LEADING_FIELDS = 6
_MAX_TITLE_CHARS = 160

# Markdown conventions the documents actually use: a heading line, then plain
# "Key: value" lines. There is deliberately no YAML front-matter handling —
# these documents have none, and a front-matter parser would return nothing on
# a real corpus while passing against a fixture nobody checked against reality.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _./-]{0,40}):\s*(\S.*?)\s*$")


def _parse_document(text: str) -> Tuple[str, List[str], bool]:
    """Extract a document's title and leading key-value lines.

    Returns ``(title, fields, capped)`` where ``title`` is the first heading,
    ``fields`` are the contiguous "Key: value" lines before the first
    subheading (blank lines between them are transparent), and ``capped`` says
    the field cap was reached. A document with no heading and no leading
    key-value lines yields ``("", [], False)``.
    """
    lines = text.splitlines()
    title = ""
    title_idx = -1
    for i, raw in enumerate(lines):
        heading = _HEADING_RE.match(raw)
        if heading:
            title = heading.group(2).strip()[:_MAX_TITLE_CHARS]
            title_idx = i
            break

    fields: List[str] = []
    capped = False
    start = title_idx + 1 if title_idx >= 0 else 0
    for raw in lines[start:]:
        if _HEADING_RE.match(raw):
            break  # first subheading ends the leading block
        stripped = raw.strip()
        if not stripped:
            continue
        if _KEY_VALUE_RE.match(stripped):
            if len(fields) < _MAX_LEADING_FIELDS:
                fields.append(stripped)
            else:
                capped = True
            continue
        break  # first non-key-value content ends the leading block

    return title, fields, capped


class PathValidationError(Exception):
    """Raised when path validation fails."""


def _declaration_scoped(method):
    """Run a read tool inside one git-declaration snapshot.

    ``WorkspaceMCP._declaration_scope`` reads the project's ignore/tracked
    declaration once for the duration of the call, so a walk does not shell out
    to git once per path.  Applied to the public read tools; ``validate_path``
    reads fresh when called on its own.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._declaration_scope():
            return method(self, *args, **kwargs)
    return wrapper


class WorkspaceMCP:
    """MCP server for sandboxed file operations within project root.
    
    Enforces capability boundaries (INV2) by:
    - Validating all paths against project root
    - Blocking directory traversal attacks
    - Normalizing paths to prevent bypass attempts
    """
    
    def __init__(self, project_root: str):
        """Initialize workspace MCP with project root.
        
        Args:
            project_root: Absolute path to project root directory
        """
        self.project_root = Path(project_root).resolve()

        # Ensure project root exists
        if not self.project_root.exists():
            raise ValueError(f"Project root does not exist: {self.project_root}")

        if not self.project_root.is_dir():
            raise ValueError(f"Project root is not a directory: {self.project_root}")

        # Derived-output detection reads the project's own declaration (its
        # git ignore rules).  The repository handle is found once; the path
        # sets are read fresh at the start of each read tool call and reused
        # only within that call, so a walk pays for one git read while build
        # output that appeared since the workspace was built — which never
        # touches the git index — is still refused on the next call.
        self._git_repo_cache = None
        self._git_repo_checked = False
        self._repo_prefix: Optional[Path] = None
        self._local = threading.local()
    
    def validate_path(self, path: str, for_mutation: bool = False) -> Path:
        """Validate that path is within project root.

        This is the one boundary every read tool funnels through, so the
        refusal lives here rather than in each caller.

        Args:
            path: Path to validate (relative or absolute)
            for_mutation: If True, also validate that path is not protected under .snodo/

        Returns:
            Resolved absolute Path object

        Raises:
            PathValidationError: If path escapes project root, is version-control
                bookkeeping, is derived build output, or (for mutation) is under .snodo/
        """
        # Convert to Path and resolve (handles .., symlinks, etc.)
        if os.path.isabs(path):
            # Absolute path
            resolved = Path(path).resolve()
        else:
            # Relative path - resolve against project root
            resolved = (self.project_root / path).resolve()

        # Check if resolved path is within project root
        try:
            rel = resolved.relative_to(self.project_root)
        except ValueError as e:
            raise PathValidationError(
                f"Path escapes project root: {path} -> {resolved}"
            ) from e

        self._refuse_vcs_internals(rel, path, resolved)
        if not for_mutation:
            self._refuse_derived_output(rel, path, resolved)

        if for_mutation and rel.parts and rel.parts[0] == ".snodo":
            raise PathValidationError(
                f"Path is protected under .snodo/ and cannot be mutated: {path} -> {resolved}"
            )

        return resolved

    @staticmethod
    def _refuse_vcs_internals(rel: Path, path: str, resolved: Path) -> None:
        """Refuse version-control bookkeeping, whatever case it is spelled in.

        ``.git`` is the tooling's own record of what was done, not the work a
        judge is asked to assess; reading it spends budget to learn nothing and
        reasons about history a judge was never meant to see.  The comparison is
        case-insensitive because on a case-insensitive filesystem ``.GIT`` opens
        the very same directory.
        """
        for part in rel.parts:
            if part.lower() in _VCS_INTERNAL_NAMES:
                raise PathValidationError(
                    f"Path is version-control bookkeeping, not the work under "
                    f"review, and is not shown: {path} -> {resolved}"
                )

    def _refuse_derived_output(self, rel: Path, path: str, resolved: Path) -> None:
        """Refuse derived build output the project has declared generated.

        Derived output says nothing its source did not already say, so a judge
        reading it spends budget to learn nothing.  How it is distinguished is
        deliberately *not* a fixed list of directory names — that is wrong for
        the next project — and deliberately not a guess, which would eventually
        hide real source.  It is what the project already declares about itself:
        a path git ignores and does not track is derived output.  A project that
        tracks source inside an ignored-looking directory keeps it readable; a
        project that does not use git simply declares nothing.
        """
        # .snodo/ is gitignored by every governed project but stays readable:
        # the protocol is context a judge is entitled to (ADR 026).
        if rel.parts and rel.parts[0] in _READABLE_IGNORED_DIRS:
            return
        if not self._is_git_ignored(rel):
            return
        raise PathValidationError(
            f"Path is derived build output the project's .gitignore declares "
            f"generated, not source the work is written in, and is not shown: "
            f"{path} -> {resolved}"
        )

    def _is_git_ignored(self, rel: Path) -> bool:
        """True if git ignores *rel* and does not track it.

        ``git ls-files --others --ignored --exclude-standard`` lists exactly the
        untracked files and directories a project's ignore rules cover: a path
        is refused only when it is in that set.  A force-added file is tracked,
        so it never appears there and stays readable — the project's own
        decision defeats the guess.  A workspace that is not a git repository
        declares nothing and nothing is refused.
        """
        data = self._git_paths()
        if data is None:
            return False
        repo_rel = self._repo_relative(rel)
        if repo_rel is None or repo_rel == ".":
            return False
        tracked, ignored = data
        if repo_rel in tracked:
            return False
        if repo_rel in ignored or repo_rel + "/" in ignored:
            return True
        # A path under an ignored directory is itself ignored: git reports the
        # directory (trailing slash) rather than each file beneath it.
        return any(
            entry.endswith("/") and repo_rel.startswith(entry) for entry in ignored
        )

    def _repo_relative(self, rel: Path) -> Optional[str]:
        """Express a project-root-relative path relative to the git root.

        The project root may itself sit inside a larger repository (a worktree
        or a monorepo package), in which case the prefix is not ".".
        """
        prefix = self._repo_prefix
        if prefix is None:
            return None
        joined = prefix / rel if str(prefix) != "." else rel
        normalized = joined.as_posix()
        return "." if normalized in ("", ".") else normalized

    @contextmanager
    def _declaration_scope(self):
        """Read the project's git declaration once for a whole read call.

        A tree walk checks the boundary for every directory and file it meets;
        without a scope that would be one git invocation per path.  The scope
        holds the snapshot for the duration of one public read tool only, so it
        cannot go stale between calls: derived output that appears without a
        commit is refused the next time a read tool runs.
        """
        self._local.git_paths = self._read_git_paths()
        try:
            yield
        finally:
            self._local.git_paths = None

    def _git_paths(self) -> Optional[Tuple[Set[str], Set[str]]]:
        """Return ``(tracked, ignored)`` repository-relative path sets.

        Uses this read call's snapshot when one is open; otherwise reads fresh,
        so a direct ``validate_path`` sees the project as it is now.
        """
        cached = getattr(self._local, "git_paths", None)
        if cached is not None:
            return cached
        return self._read_git_paths()

    def _read_git_paths(self) -> Optional[Tuple[Set[str], Set[str]]]:
        """Read ``(tracked, ignored)`` from git, or None when there is no view.

        ``tracked`` is every path git tracks; ``ignored`` is every untracked
        path git's ignore rules cover, directories carrying a trailing slash.
        Returns None when there is no usable git view, so the caller refuses
        nothing rather than guessing.
        """
        repo = self._git_repo()
        if repo is None:
            return None
        try:
            tracked: Set[str] = set()
            for entry in repo.git.ls_files("-z").split("\0"):
                if entry:
                    tracked.add(entry.replace("\\", "/"))
            ignored: Set[str] = set()
            listing = repo.git.ls_files(
                "--others", "--ignored", "--exclude-standard", "--directory", "-z"
            )
            for entry in listing.split("\0"):
                if entry:
                    ignored.add(entry.replace("\\", "/"))
        except Exception:
            return None
        return (tracked, ignored)

    def _git_repo(self):
        """The GitPython repository for the project root, or None.

        Imported lazily: this module sits below GitPython in the dependency
        order only for the tools package, and a workspace without git must keep
        working (a non-repository declares nothing).
        """
        if self._git_repo_checked:
            return self._git_repo_cache
        self._git_repo_checked = True
        try:
            from git import Repo
        except ImportError:
            return None
        try:
            repo = Repo(str(self.project_root), search_parent_directories=True)
        except Exception:
            self._git_repo_cache = None
            return None
        self._git_repo_cache = repo
        # Cache the prefix from the git root down to the project root, so a
        # project nested in a monorepo resolves against the right paths.
        try:
            root = Path(repo.working_tree_dir).resolve()
            self._repo_prefix = self.project_root.relative_to(root)
        except Exception:
            self._repo_prefix = None
        return repo
    
    @_declaration_scoped
    def read_file(self, path: str) -> str:
        """Read file content.
        
        Args:
            path: Path to file (relative to project root or absolute)
            
        Returns:
            File content as string
            
        Raises:
            PathValidationError: If path escapes project root
            FileNotFoundError: If file doesn't exist
            PermissionError: If file cannot be read
        """
        validated_path = self.validate_path(path)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if not validated_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        
        return validated_path.read_text()
    
    @_declaration_scoped
    def read_file_lines(self, path: str, start: int, end: int) -> str:
        """Read a line range from a file (1-indexed, inclusive).
        
        Args:
            path: Path to file (relative to project root or absolute)
            start: First line number to read (1-indexed)
            end: Last line number to read (1-indexed, inclusive)
            
        Returns:
            File content for the specified line range
            
        Raises:
            PathValidationError: If path escapes project root
            FileNotFoundError: If file doesn't exist
            ValueError: If start > end or start < 1
        """
        if start < 1:
            raise ValueError(f"start must be >= 1, got {start}")
        if end < start:
            raise ValueError(f"end must be >= start, got {end} < {start}")
        
        validated_path = self.validate_path(path)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if not validated_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        
        lines = validated_path.read_text().splitlines()
        selected = lines[start - 1:end]
        return "\n".join(selected)
    
    def write_file(self, path: str, content: str) -> bool:
        """Write content to file.
        
        Args:
            path: Path to file (relative to project root or absolute)
            content: Content to write
            
        Returns:
            True if successful
            
        Raises:
            PathValidationError: If path escapes project root or mutates .snodo/
            PermissionError: If file cannot be written
        """
        validated_path = self.validate_path(path, for_mutation=True)
        
        # Create parent directories if needed
        validated_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        validated_path.write_text(content)
        
        return True
    
    @_declaration_scoped
    def list_files(self, directory: str = ".") -> List[str]:
        """List files and directories in a directory.
        
        Args:
            directory: Directory path (relative to project root or absolute)
            
        Returns:
            List of file/directory names (not full paths). Entries that are
            version-control bookkeeping or derived build output are omitted.
            
        Raises:
            PathValidationError: If path escapes project root, is version-control
                bookkeeping, or is derived build output
            FileNotFoundError: If directory doesn't exist
        """
        validated_path = self.validate_path(directory)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        if not validated_path.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")
        
        # Every entry is offered only if it is itself the work: omitting `.git`
        # by name was the narrow version of this, and derived output belongs in
        # the same answer. A path a judge explicitly asks for is refused with a
        # reason; a listing simply does not offer what is not the work.
        return [
            item.name
            for item in validated_path.iterdir()
            if self._entry_allowed(item)
        ]

    def _entry_allowed(self, entry: Path) -> bool:
        """Whether *entry* is the work under review, so a listing may offer it.

        Routes the entry through the same boundary every explicit read uses,
        which is what catches a symlink pointing outside the workspace that a
        name-only check would miss, and derived output a name-only check would
        offer.
        """
        try:
            self.validate_path(str(entry))
        except PathValidationError:
            return False
        return True

    def _walk_readable_files(self, root: Path) -> Iterator[Path]:
        """Yield each file under *root* that is the work under review.

        The boundary is applied to every directory before descending and to
        every file before reading, so a symlink that resolves outside the
        workspace is neither traversed nor opened.  The explicit noise set is
        kept for workspaces that declare nothing (no git), where git's ignore
        rules cannot prune a dependency tree.
        """
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in _SEARCH_PRUNED_DIRS
                and self._entry_allowed(Path(dirpath) / name)
            )
            for name in sorted(filenames):
                fpath = Path(dirpath) / name
                if self._entry_allowed(fpath):
                    yield fpath
    
    @_declaration_scoped
    def summarize_directory(self, directory: str = ".") -> str:
        """Summarize a directory of markdown documents in one call.

        Returns one compact record per regular file directly in *directory*:
        the path, the first heading (its title), and the leading "Key: value"
        lines before the first subheading. Document bodies are not read — the
        existing read tools do that. This is computed from the files at call
        time, so there is nothing to maintain and nothing that can go stale.

        Ordering is alphabetical, never by relevance: ordering is the judge's
        inference to make. The response is bounded and states when it was
        truncated. A directory that does not exist is an answer, not an error.
        A path outside the workspace is refused like the other read tools.

        Args:
            directory: Directory path (relative to project root or absolute)

        Returns:
            A bounded, human-readable index of the directory's documents.

        Raises:
            PathValidationError: If path escapes project root, is version-control
                bookkeeping, or is derived build output
        """
        validated_path = self.validate_path(directory)

        if not validated_path.exists():
            return (
                f"No documents found in '{directory}' "
                "(directory does not exist)."
            )
        if not validated_path.is_dir():
            return f"Not a directory: '{directory}'."

        entries = sorted(
            (
                item
                for item in validated_path.iterdir()
                if item.is_file()
                and not item.name.startswith(".")
                and self._entry_allowed(item)
            ),
            key=lambda item: item.name,
        )
        total = len(entries)

        records: List[str] = []
        used = 0
        truncated = False
        for item in entries:
            if len(records) >= _MAX_SUMMARY_FILES:
                truncated = True
                break
            try:
                text = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # A file that is not readable UTF-8 text is not a document
                # this tool can summarize; it is not an error either.
                continue
            title, fields, capped = _parse_document(text)
            rel_path = item.relative_to(self.project_root).as_posix()
            block = [rel_path]
            if title:
                block.append(f"  # {title}")
            block.extend(f"  {field}" for field in fields)
            if capped:
                block.append("  ...")
            record = "\n".join(block)
            cost = len(record) + 2
            if records and used + cost > _MAX_SUMMARY_CHARS:
                truncated = True
                break
            records.append(record)
            used += cost

        if not records:
            return f"No documents found in '{directory}'."

        body = "\n\n".join(records)
        if truncated:
            body += (
                f"\n\n[truncated: showing {len(records)} of {total} documents; "
                "narrow the directory or read specific files]"
            )
        return body

    @_declaration_scoped
    def file_exists(self, path: str) -> bool:
        """Check if file exists.
        
        Args:
            path: Path to check
            
        Returns:
            True if file exists, False otherwise
            
        Raises:
            PathValidationError: If path escapes project root
        """
        try:
            validated_path = self.validate_path(path)
            return validated_path.exists()
        except PathValidationError:
            return False
    
    def delete_file(self, path: str) -> bool:
        """Delete a file.
        
        Args:
            path: Path to file to delete
            
        Returns:
            True if successful
            
        Raises:
            PathValidationError: If path escapes project root or mutates .snodo/
            FileNotFoundError: If file doesn't exist
        """
        validated_path = self.validate_path(path, for_mutation=True)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if validated_path.is_dir():
            raise ValueError(f"Cannot delete directory with delete_file: {path}")
        
        validated_path.unlink()
        return True
    
    def create_directory(self, path: str) -> bool:
        """Create a directory.
        
        Args:
            path: Path to directory to create
            
        Returns:
            True if successful
            
        Raises:
            PathValidationError: If path escapes project root or mutates .snodo/
        """
        validated_path = self.validate_path(path, for_mutation=True)
        validated_path.mkdir(parents=True, exist_ok=True)
        return True
    
    @_declaration_scoped
    def get_absolute_path(self, path: str) -> str:
        """Get absolute path for a relative path.
        
        Args:
            path: Relative or absolute path
            
        Returns:
            Absolute path as string
            
        Raises:
            PathValidationError: If path escapes project root
        """
        validated_path = self.validate_path(path)
        return str(validated_path)

    @_declaration_scoped
    def search_string(self, query: str, directory: str = ".") -> str:
        """Search text files in directory for matching string/pattern.

        Args:
            query: Literal string or pattern to search for
            directory: Subdirectory to search under

        Returns:
            Formatted search matches as string: "rel_path:line_num: line_content"
        """
        if not query:
            return "Query parameter cannot be empty."

        validated = self.validate_path(directory)
        matches = []
        max_matches = 50

        for fpath in self._walk_readable_files(validated):
            try:
                rel_path = fpath.relative_to(self.project_root)
            except ValueError:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, start=1):
                        if query in line:
                            clean_line = line.rstrip()
                            matches.append(f"{rel_path}:{line_idx}: {clean_line}")
                            if len(matches) >= max_matches:
                                break
            except Exception:
                continue
            if len(matches) >= max_matches:
                break

        if not matches:
            return f"No matches found for '{query}' in {directory}"
        return "\n".join(matches)

    @_declaration_scoped
    def search_symbol(self, name: str, directory: str = ".") -> str:
        """Search for symbol definition (class, def, function, struct, fn, type, const) in directory.

        Args:
            name: Symbol name to search for
            directory: Subdirectory to search under

        Returns:
            Formatted symbol definition matches as string
        """
        if not name:
            return "Symbol name parameter cannot be empty."

        pattern = re.compile(rf"\b(def|class|function|fn|struct|type|const|let|var)\s+{re.escape(name)}\b")
        validated = self.validate_path(directory)
        matches = []
        max_matches = 50

        for fpath in self._walk_readable_files(validated):
            try:
                rel_path = fpath.relative_to(self.project_root)
            except ValueError:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, start=1):
                        if pattern.search(line):
                            clean_line = line.rstrip()
                            matches.append(f"{rel_path}:{line_idx}: {clean_line}")
                            if len(matches) >= max_matches:
                                break
            except Exception:
                continue
            if len(matches) >= max_matches:
                break

        if not matches:
            return f"No symbol definitions found for '{name}' in {directory}"
        return "\n".join(matches)


# Module-level instance for convenience
_workspace_instance: Optional[WorkspaceMCP] = None


def get_workspace(project_root: Optional[str] = None) -> WorkspaceMCP:
    """Get workspace MCP instance.
    
    Args:
        project_root: Project root directory (uses existing instance if None)
        
    Returns:
        WorkspaceMCP instance
    """
    global _workspace_instance
    
    if project_root is not None:
        _workspace_instance = WorkspaceMCP(project_root)
    
    if _workspace_instance is None:
        raise ValueError("Workspace not initialized. Call with project_root first.")
    
    return _workspace_instance
