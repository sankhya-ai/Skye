Write content to a file.

**Tips:**
- When `mode` is not specified, it defaults to `overwrite`. Always write with caution.
- In workspace-scoped runtimes, writes are scoped to the runtime workspace. Use relative paths for repo files.
- For files inside the working directory, missing parent directories are created automatically after approval.
- When the content to write is too long (e.g. > 100 lines), use this tool multiple times instead of a single call. Use `overwrite` mode at the first time, then use `append` mode after the first write.
