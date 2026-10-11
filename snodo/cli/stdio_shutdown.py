"""Process shutdown helpers for stdio CLI transports."""

import os
import sys


def exit_stdio_if_disconnected(transport: str) -> None:
    """Exit without executor teardown once a real stdio CLI run has ended."""
    if transport == "stdio":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
