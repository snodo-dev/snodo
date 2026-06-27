"""Workspace MCP server for sandboxed file operations.

FILE: snodo/mcp/workspace.py

Implements INV2 capability boundaries - enforces project root and
prevents directory traversal attacks.
"""

from pathlib import Path
from typing import List, Optional
import os


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
    
    def validate_path(self, path: str) -> Path:
        """Validate that path is within project root.
        
        Args:
            path: Path to validate (relative or absolute)
            
        Returns:
            Resolved absolute Path object
            
        Raises:
            PathValidationError: If path escapes project root
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
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PathValidationError(
                f"Path escapes project root: {path} -> {resolved}"
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
            PathValidationError: If path escapes project root
            PermissionError: If file cannot be written
        """
        validated_path = self.validate_path(path)
        
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
            List of file/directory names (not full paths)
            
        Raises:
            PathValidationError: If path escapes project root
            FileNotFoundError: If directory doesn't exist
        """
        validated_path = self.validate_path(directory)
        
        if not validated_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        if not validated_path.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")
        
        # List directory contents
        return [item.name for item in validated_path.iterdir()]
    
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
            PathValidationError: If path escapes project root
            FileNotFoundError: If file doesn't exist
        """
        validated_path = self.validate_path(path)
        
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
            PathValidationError: If path escapes project root
        """
        validated_path = self.validate_path(path)
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
