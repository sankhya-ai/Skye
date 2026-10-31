Read text content from a file.

**Tips:**
- Make sure you follow the description of each tool parameter.
- A `<system>` tag will be given before the read file content.
- The system will notify you when there is anything wrong when reading the file.
- This tool is a tool that you typically want to use in parallel. Always read multiple files in one response when possible.
- This tool reads text files and returns a bounded listing when given a directory. To read images or videos, use other appropriate tools. To read other file types, use appropriate commands via the Shell tool.
- In workspace-scoped runtimes, file paths are scoped to the runtime workspace. Use relative paths for repo files.
- If the file doesn't exist or path is invalid, an error will be returned.
- If you want to search for a certain content/pattern, prefer Grep tool over ReadFile.
- Content will be returned with a line number before each line like `cat -n` format.
- Use `line_offset` and `n_lines` parameters when you only need to read a part of the file.
- The maximum number of lines that can be read at once is ${MAX_LINES}.
- Any lines longer than ${MAX_LINE_LENGTH} characters will be truncated, ending with "...".
