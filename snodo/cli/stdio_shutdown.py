"""Process shutdown helpers for stdio CLI transports."""

import os
import sys


def exit_stdio_if_disconnected(transport: str) -> int:
    """Exit without executor teardown once a stdio transport has ended."""
    if transport == "stdio":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0
