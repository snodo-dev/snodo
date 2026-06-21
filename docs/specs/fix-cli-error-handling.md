# Fix: graceful CLI error handling for unknown commands and bad args

## Intent
Running an unknown command (snodo cloud) or bad arguments produces a
20-line Python traceback instead of a clean error message. The CLI
should catch UsageError and print a clean one-liner, then exit with
code 2 (the standard for usage errors).

## What to change

### cli/main.py — main() function
Currently catches only SystemExit. Add:

import click

try:
    result = app(args=argv, standalone_mode=False)
    return result if isinstance(result, int) else 0
except SystemExit:
    raise
except click.exceptions.UsageError as e:
    print(f"Error: {e.format_message()}", file=sys.stderr)
    print("Run 'snodo --help' to see available commands.",
          file=sys.stderr)
    return 2
except click.exceptions.Exit as e:
    return e.code

That's it. UsageError covers: unknown commands, missing required args,
invalid option values. All become clean one-liners.

## Acceptance criteria
- snodo cloud → "Error: No such command 'cloud'." + help hint
- snodo run (missing required arg) → clean error, no traceback
- snodo serve --transport invalid → clean error, no traceback
- Legitimate errors (engine failures, file not found) still raise
  normally — only Click/Typer UsageError is caught here
- Exit code 2 for usage errors (standard Unix convention)

## Testing
- Unit: unknown command → exit code 2, clean stderr, no traceback
- Unit: missing required arg → exit code 2, clean stderr
- Full suite passes

## Constraints
- Read cli/main.py (main() function, existing exception handling)
- Touch only cli/main.py
- Do not suppress legitimate runtime errors — only UsageError
