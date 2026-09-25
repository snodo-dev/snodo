# Writing files from an MCP mode

<!-- snodo-guide topic="writing" aliases="write,write_file,file-writing" summary="MCP file writing with an explicit mode grant" section="## Write files through MCP" -->

## Write files through MCP

The `write` protocol mode tool exposes the `write_file` MCP tool. Add `write`
only to modes that should be able to change host files; modes without that grant
do not see `write_file`. `delete_file` is not exposed.

The default allowed prefix is `.snodo/`, for authoring plans, specs, protocols,
and queues. A protocol can replace the default by declaring
`write_allowed_prefixes` at its top level:

```yaml
write_allowed_prefixes:
  - .snodo/
  - docs/specs/
```

Prefixes are project-relative. The resolved destination must remain inside the
project root and one of those prefixes; traversal and symlinks are resolved
before the check. A directory cannot be the destination, and refusal messages
name the allowed prefixes. The tool writes directly without staging or
committing and returns the project-relative path and UTF-8 byte count. Each
successful write appends its path, byte count, SHA-256 content hash, mode, and
session to the project audit log.
