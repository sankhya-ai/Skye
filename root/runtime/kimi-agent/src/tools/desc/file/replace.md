Replace specific strings within a specified file.

**Tips:**
- Only use this tool on text files.
- In workspace-scoped runtimes, edits are scoped to the runtime workspace. Use relative paths for repo files.
- Multi-line strings are supported.
- Can specify a single edit or a list of edits in one call.
- You should prefer this tool over WriteFile tool and Shell `sed` command.
