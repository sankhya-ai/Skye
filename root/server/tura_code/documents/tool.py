"""The document tools the agent sees: ``read_document``, ``edit_spreadsheet``, ``edit_pdf``.

Paths are the workspace's like ``ReadFile``'s and ``WriteFile``'s, and so are
the approvals: a write asks as an "edit file" (so "Accept edits" covers it),
and anything outside the workspace is fenced by the boundary (see `trust.boundary`).
Plan mode and line sessions are read-only: they get ``read_document`` alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import trust
from ..sessions.host_tools import tool_error
from . import pdfs, readers, sheets
from .readers import DocumentError

if TYPE_CHECKING:
    from ..sessions import SessionRuntime

READ_TOOL = "read_document"
SHEET_TOOL = "edit_spreadsheet"
PDF_TOOL = "edit_pdf"
EDIT_TOOLS = (SHEET_TOOL, PDF_TOOL)

_OPERATIONS = {"type": "array", "items": {"type": "object"}, "minItems": 1}

DEFINITIONS = {
    READ_TOOL: {
        "name": READ_TOOL,
        "description": (
            "Read a PDF, Word (.docx), Excel (.xlsx/.xlsm), PowerPoint (.pptx) or CSV/TSV file as text. ReadFile cannot "
            "read these. PDFs come back page by page with their form fields; workbooks as cells (`B2=…`, formulas with "
            "their last computed value). Use `pages` (e.g. `1-3,7`) for long PDFs and `sheet`/`cells` (e.g. `A1:F40`) for big sheets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file, relative to the workspace or absolute."},
                "pages": {"type": "string", "description": "PDF pages to read, e.g. `1-3,7` or `5-end`. Default: all."},
                "sheet": {"type": "string", "description": "Workbook sheet to read. Default: every sheet."},
                "cells": {"type": "string", "description": "A range on the sheet, e.g. `A1:F40`."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    SHEET_TOOL: {
        "name": SHEET_TOOL,
        "description": (
            "Edit (or create) an .xlsx/.xlsm workbook or a .csv/.tsv file. `operations` run in order, then the file is "
            "saved once. Operations: "
            '{"op":"set","sheet"?,"cells":{"A1":"Total","B5":"=SUM(B2:B4)"}}, '
            '{"op":"append_rows","sheet"?,"rows":[["a",1],["b",2]]}, '
            '{"op":"insert_rows"|"delete_rows"|"insert_cols"|"delete_cols","at":3,"count":1}, '
            '{"op":"add_sheet","name"}, {"op":"rename_sheet","sheet","name"}, {"op":"delete_sheet","sheet"}, '
            '{"op":"format","range":"A1:D1","bold"?,"italic"?,"color"?,"fill"?,"number_format"?,"width"?,"wrap"?,"align"?}, '
            '{"op":"merge"|"unmerge","range"}, {"op":"freeze","cell":"A2"}. '
            "Formulas are stored as written and computed when the file is opened. Read the file first with read_document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "operations": _OPERATIONS,
                "create": {"type": "boolean", "description": "Make the file if it does not exist."},
                "allow_lossy": {"type": "boolean", "description": "Save even though the workbook's charts/images/pivot tables would be lost."},
            },
            "required": ["path", "operations"],
            "additionalProperties": False,
        },
    },
    PDF_TOOL: {
        "name": PDF_TOOL,
        "description": (
            "Edit (or create) a PDF. `operations` run in order; the result is saved to `output` if given, else over the file. "
            'Operations: {"op":"merge","files":["b.pdf"],"at"?:2}, {"op":"delete_pages"|"keep_pages","pages":"2,4-6"}, '
            '{"op":"rotate","pages":"1","degrees":90}, {"op":"reorder","order":"3,1,2"}, {"op":"add_page","width"?,"height"?,"at"?}, '
            '{"op":"add_text","page":1,"text":"…","x":72,"y":72,"size"?:12,"color"?:"000000"} (x/y in points from the top-left), '
            '{"op":"fill_form","fields":{"name":"Ada"},"flatten"?:false} (read_document lists the fields), '
            '{"op":"metadata","title"?,"author"?,"subject"?,"keywords"?}, {"op":"encrypt","password"}. '
            "To split, call it once per part with keep_pages and an `output`. To make a new PDF, start with add_page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "operations": _OPERATIONS,
                "output": {"type": "string", "description": "Save the result here instead of over `path`."},
            },
            "required": ["path", "operations"],
            "additionalProperties": False,
        },
    },
}


def _ok(output: str, brief: str) -> dict[str, Any]:
    return {"is_error": False, "output": output, "message": "", "display": [{"type": "brief", "text": brief}]}


class DocumentToolset:
    plugin = "documents"

    def definitions(self, runtime: "SessionRuntime") -> list[dict[str, Any]]:
        if runtime.record.permission_mode == "plan" or runtime.record.answering:
            return [DEFINITIONS[READ_TOOL]]
        return list(DEFINITIONS.values())

    def handles(self, name: str) -> bool:
        return name in DEFINITIONS

    async def call(self, runtime: "SessionRuntime", call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        workspace = Path(runtime.record.workspace)
        try:
            path = self._resolve(workspace, str(arguments.get("path") or ""))
            if name == READ_TOOL:
                if not await self._may_read(runtime, call_id, workspace, path):
                    return tool_error(f"Reading `{path}` was not allowed.", "Not allowed")
                result = await asyncio.to_thread(
                    readers.read, path, pages=str(arguments.get("pages") or ""), sheet=str(arguments.get("sheet") or ""), cells=str(arguments.get("cells") or "")
                )
                return _ok(result["text"], f"Read {path.name}: {result['summary']}")
            if runtime.record.permission_mode == "plan" or runtime.record.answering:
                return tool_error("This session is read-only.", "Read-only")
            operations = arguments.get("operations")
            if not isinstance(operations, list):
                return tool_error("`operations` must be a list of operation objects.", "Invalid arguments")
            output = self._resolve(workspace, str(arguments["output"])) if arguments.get("output") else None
            target = output or path
            summary = ", ".join(str(op.get("op")) for op in operations if isinstance(op, dict))
            if not await self._may_write(runtime, call_id, workspace, target, f"{name}: {summary}"):
                return tool_error(f"Editing `{target}` was not allowed.", "Rejected")
            if name == SHEET_TOOL:
                result = await asyncio.to_thread(
                    sheets.edit, path, operations, create=bool(arguments.get("create")), allow_lossy=bool(arguments.get("allow_lossy"))
                )
                detail = result.get("sheets") or {"rows": result.get("rows")}
                return _ok("Saved. " + "; ".join(result["done"]) + f".\n{detail}", f"Edited {path.name}")
            merged = [str(f) for op in operations if isinstance(op, dict) and op.get("op") == "merge" for f in op.get("files") or []]
            for other in merged:
                other_path = self._resolve(workspace, other)
                if not await self._may_read(runtime, call_id, workspace, other_path):
                    return tool_error(f"Reading `{other_path}` was not allowed.", "Not allowed")
            result = await asyncio.to_thread(pdfs.edit, path, operations, output=output, resolve=lambda p: self._resolve(workspace, p))
            pages = f"{result['pages']} pages" if result["pages"] is not None else "encrypted"
            return _ok("Saved. " + "; ".join(result["done"]) + f". {Path(result['path']).name}: {pages}.", f"Edited {Path(result['path']).name}")
        except DocumentError as error:
            return tool_error(str(error), "Failed")

    # ----- paths and approvals -------------------------------------------------------

    @staticmethod
    def _resolve(workspace: Path, raw: str) -> Path:
        if not raw.strip():
            raise DocumentError("`path` is required.")
        path = Path(raw).expanduser()
        return (path if path.is_absolute() else workspace / path).resolve()

    @staticmethod
    def _inside(workspace: Path, path: Path) -> bool:
        return path.is_relative_to(workspace.resolve())

    async def _may_read(self, runtime: "SessionRuntime", call_id: str, workspace: Path, path: Path) -> bool:
        if self._inside(workspace, path):
            return True
        return await runtime.approve_host_action(call_id, trust.ACTION_READ_OUTSIDE, f"Read `{path}`")

    async def _may_write(self, runtime: "SessionRuntime", call_id: str, workspace: Path, path: Path, what: str) -> bool:
        action = trust.ACTION_EDIT_FILE if self._inside(workspace, path) else trust.ACTION_EDIT_OUTSIDE
        return await runtime.approve_host_action(call_id, action, f"Edit `{path}` ({what})", [{"type": "brief", "text": what}])
