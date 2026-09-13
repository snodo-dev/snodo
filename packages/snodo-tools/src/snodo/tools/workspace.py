"""Workspace MCP server for sandboxed file operations.

FILE: snodo/mcp/workspace.py

Implements INV2 capability boundaries - enforces project root and
prevents directory traversal attacks.
"""

from pathlib import Path
from typing import List, Optional, Tuple
import os
import re


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
    
    def validate_path(self, path: str, for_mutation: bool = False) -> Path:
        """Validate that path is within project root.
        
        Args:
            path: Path to validate (relative or absolute)
            for_mutation: If True, also validate that path is not protected under .snodo/
            
        Returns:
            Resolved absolute Path object
            
        Raises:
            PathValidationError: If path escapes project root, accesses .git/, or
                attempts to mutate .snodo/
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
        
        if ".git" in rel.parts:
            raise PathValidationError(
                f"Path is protected under .git/ and cannot be accessed: {path} -> {resolved}"
            )

        if for_mutation and rel.parts and rel.parts[0] == ".snodo":
            raise PathValidationError(
                f"Path is protected under .snodo/ and cannot be mutated: {path} -> {resolved}"
            )

        return resolved
    
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
    
    def list_files(self, directory: str = ".") -> List[str]:
        """List files and directories in a directory.
        
        Args:
            directory: Directory path (relative to project root or absolute)
            
        Returns:
            List of file/directory names (not full paths). `.git` entries are omitted.
            
        Raises:
            PathValidationError: If path escapes project root or is under .git/
            FileNotFoundError: If directory doesn't exist
        """
        validated_path = self.validate_path(directory)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        if not validated_path.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")
        
        # List directory contents
        return [
            item.name
            for item in validated_path.iterdir()
            if item.name != ".git"
        ]
    
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
            PathValidationError: If path escapes project root or is under .git/
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
                if item.is_file() and not item.name.startswith(".")
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

        for root, dirs, files in os.walk(validated):
            dirs[:] = [d for d in dirs if d not in {".git", ".snodo", "__pycache__", "node_modules", ".venv"}]
            for fname in sorted(files):
                fpath = Path(root) / fname
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
            if len(matches) >= max_matches:
                break

        if not matches:
            return f"No matches found for '{query}' in {directory}"
        return "\n".join(matches)

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

        for root, dirs, files in os.walk(validated):
            dirs[:] = [d for d in dirs if d not in {".git", ".snodo", "__pycache__", "node_modules", ".venv"}]
            for fname in sorted(files):
                fpath = Path(root) / fname
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
