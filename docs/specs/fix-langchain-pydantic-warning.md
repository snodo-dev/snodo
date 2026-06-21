# Fix: suppress langchain_core pydantic v1 warning on Python 3.14+

## Intent
Every snodo CLI invocation prints a noisy UserWarning to stderr before
any output:
  "Core Pydantic V1 functionality isn't compatible with Python 3.14 or
   greater."
This is a known langchain_core issue — it imports from pydantic.v1 to
detect which pydantic version the user is on, which triggers the warning
on Python 3.14+ even though snodo uses pydantic v2 throughout and nothing
is actually broken. It is langchain's problem, not snodo's.

Suppress the warning at the CLI entry point so it never reaches the user.

## What to change

### snodo/cli/main.py — top of file, before any other imports
Add:
  import warnings
  warnings.filterwarnings(
      "ignore",
      category=UserWarning,
      module="langchain_core",
  )

Must be the FIRST thing in the file before any snodo/langchain imports
so it takes effect before langchain_core is imported.

## Acceptance criteria
- Running any snodo command on Python 3.14 produces no pydantic warning
  on stderr
- No legitimate warnings from snodo's own code are suppressed (filter
  is scoped to langchain_core module only)
- The filter is a no-op on Python 3.13 and below (warnings module is
  always available, filter just never matches)

## Testing
- Confirm `snodo --help` produces no UserWarning on stderr
- Existing test suite passes (no warnings infrastructure broken)

## Constraints
- One targeted filter — do not use PYTHONWARNINGS or blanket suppression
- This is a temporary workaround until langchain_core fixes their pydantic
  v1 detection. Add a comment in the code noting this:
  # TODO: remove once langchain_core fixes pydantic v1 detection on 3.14+
  # https://github.com/langchain-ai/langchain/issues/33926
- Touch only cli/main.py
