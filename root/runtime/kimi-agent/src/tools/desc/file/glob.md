Find files and directories using glob patterns. This tool supports standard glob syntax like `*`, `?`, and `**` for recursive searches.
The optional `directory` parameter accepts either an absolute path inside the working directory or a relative path resolved from the working directory.

**When to use:**
- Find files matching specific patterns (e.g., all Python files: `*.py`)
- Search for files recursively in subdirectories (e.g., `src/**/*.js`)
- Locate configuration files (e.g., `*.config.*`, `*.json`)
- Find test files (e.g., `test_*.py`, `*_test.go`)

**Example patterns:**
- `*.py` - All Python files in current directory
- `src/**/*.js` - All JavaScript files in src directory recursively
- `test_*.py` - Python test files starting with "test_"
- `*.config.{js,ts}` - Config files with .js or .ts extension

**Bad example patterns:**
- `**` alone - matches everything. `**/*.py` is fine: it searches the whole workspace but skips dependency, VCS, build and cache directories (`node_modules`, `.git`, `target`, `dist`, `.venv`, `__pycache__`…). Prefer `src/**/*.py` when you know where to look.
- `node_modules/**/*.js` - Although this does not start with '**', it would still highly possible to yield large result because `node_modules` is well-known to contain too many directories and files. Avoid recursively searching in such directories, other examples include `venv`, `.venv`, `__pycache__`, `target`. If you really need to search in a dependency, use more specific patterns like `node_modules/react/src/*` instead.
