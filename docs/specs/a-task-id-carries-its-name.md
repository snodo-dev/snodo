# A Task ID Carries Its Name

## Intent

Generated task specs use the task ID as their filename and progress-tree label.
The ID must therefore carry the task's readable name instead of treating the
name suffix as optional.

## Contract

- New task specs require `<wave>.<sequence>_<name>`, such as `1.1_models`.
- A bare sequence ID such as `1.1` is rejected with an actionable example.
- Existing plan files and task references are not renamed or migrated.
- The MCP `generate_spec` schema and server instructions state the format where
  an orchestrator uses it.
