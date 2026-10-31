Execute a ${SHELL} command. Use this tool to explore the filesystem, inspect or edit files, run Windows scripts, collect system information, etc., whenever the agent is running on Windows.

Note that you are running on Windows, so make sure to use Windows commands, paths, and conventions.

**Output:**
The stdout and stderr streams are combined and returned as a single string. Extremely long output may be truncated. When a command fails, the exit code is provided in a system tag.

**Guidelines for safety and security:**
- Your commands run in one ${SHELL} session that stays open between calls: variables, `$env:` changes, functions and imported modules persist. Every call starts in the working directory again, so `cd` does not carry over. A timeout or `exit` resets the session.
- Do not launch interactive programs or anything that is expected to block indefinitely; ensure each command finishes promptly. Provide a `timeout` argument for potentially long runs.
- Avoid using `..` to leave the working directory, and never touch files outside that directory unless explicitly instructed.
- Never attempt commands that require elevated (Administrator) privileges unless explicitly authorized.

**Guidelines for efficiency:**
- Chain related commands with `;` and use `if ($?)` or `if (-not $?)` to conditionally execute commands based on the success or failure of previous ones.
- Redirect or pipe output with `>`, `>>`, `|`, and use `ForEach-Object`, `Where-Object` and `Select-String` to build richer one-liners instead of multiple tool calls.
- Reuse built-in utilities (e.g., `findstr`, `where.exe`) to filter, transform, or locate data in a single invocation.
- Programs that wait for keyboard input block until the timeout; pass their input as arguments or pipe it in.

**Commands available:**
- Shell environment: `cd`, `dir`, `$env:NAME`, `echo`, `where.exe`
- File operations: `type`, `copy`, `move`, `del`, `erase`, `mkdir`, `rmdir`, `attrib`, `mklink`
- Text/search: `find`, `findstr`, `more`, `sort`, `Get-Content`
- System info: `ver`, `systeminfo`, `tasklist`, `wmic`, `hostname`
- Archives/scripts: `tar`, `Compress-Archive`, `powershell`, `python`, `node`
- Other: Any other binaries available on the system PATH; run `where <command>` first if unsure.
