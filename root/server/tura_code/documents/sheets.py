"""Editing spreadsheets: ``.xlsx``/``.xlsm`` through openpyxl, ``.csv``/``.tsv`` as rows.

Operations apply in order to one workbook and are saved once, atomically; the
saved file is opened again before the edit counts. openpyxl does not carry
charts, images or pivot tables through a save, so a workbook that has them is
refused unless the caller accepts losing them (``allow_lossy``). Formulas are
stored as written; Excel or LibreOffice compute them when the file is opened.
"""

from __future__ import annotations

import csv
import os
from copy import copy
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .readers import DocumentError

SHEET_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv")
_LOSSY_PARTS = ("xl/charts/", "xl/drawings/", "xl/pivotTables/", "xl/media/")


def _atomic_save(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=path.suffix, dir=str(path.parent))
    os.close(fd)
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def lossy_parts(path: Path) -> list[str]:
    """What an openpyxl round trip would drop from this workbook."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted({part.split("/")[1] for part in _LOSSY_PARTS for name in names if name.startswith(part)})


def edit(path: Path, operations: list[dict[str, Any]], *, create: bool = False, allow_lossy: bool = False) -> dict:
    suffix = path.suffix.lower()
    if suffix not in SHEET_SUFFIXES:
        raise DocumentError(f"edit_spreadsheet edits {', '.join(SHEET_SUFFIXES)} files.")
    if not operations:
        raise DocumentError("No operations given.")
    if not path.exists() and not create:
        raise DocumentError(f"{path.name} does not exist; pass `create: true` to make it.")
    if suffix in (".csv", ".tsv"):
        return _edit_csv(path, operations)
    return _edit_workbook(path, operations, allow_lossy=allow_lossy)


# ----- workbooks -------------------------------------------------------------


def _edit_workbook(path: Path, operations: list[dict[str, Any]], *, allow_lossy: bool) -> dict:
    from openpyxl import Workbook, load_workbook

    if path.exists():
        dropped = lossy_parts(path)
        if dropped and not allow_lossy:
            raise DocumentError(
                f"{path.name} contains {', '.join(dropped)}, which this editor cannot keep: saving would remove them. "
                "Edit a copy (or pass `allow_lossy: true` if losing them is acceptable)."
            )
        try:
            book = load_workbook(str(path), keep_vba=path.suffix.lower() == ".xlsm")
        except Exception as error:  # noqa: BLE001
            raise DocumentError(f"Could not open the workbook: {error}") from error
    else:
        book = Workbook()
    done = [_apply(book, op) for op in operations]
    _atomic_save(path, book.save)
    check = load_workbook(str(path))
    return {"done": done, "sheets": {ws.title: ws.dimensions for ws in check.worksheets}}


def _sheet(book, op: dict[str, Any]):
    name = op.get("sheet")
    if not name:
        return book.active
    if name not in book.sheetnames:
        raise DocumentError(f"No sheet {name!r}; the sheets are {', '.join(book.sheetnames)}.")
    return book[name]


def _cells(ws, ref: str):
    try:
        grid = ws[ref]
    except (ValueError, KeyError) as error:
        raise DocumentError(f"Bad range {ref!r}: {error}") from error
    if not isinstance(grid, tuple):
        return [grid]
    flat = []
    for item in grid:
        flat.extend(item if isinstance(item, tuple) else (item,))
    return flat


def _apply(book, op: dict[str, Any]) -> str:
    from openpyxl.styles import PatternFill
    from openpyxl.utils import get_column_letter

    kind = str(op.get("op") or "")
    if kind == "add_sheet":
        name = str(op.get("name") or "").strip()
        if not name or name in book.sheetnames:
            raise DocumentError(f"add_sheet needs a new sheet name (have {', '.join(book.sheetnames)}).")
        book.create_sheet(name, op.get("index"))
        return f"added sheet {name}"
    if kind == "rename_sheet":
        ws = _sheet(book, op)
        old, ws.title = ws.title, str(op.get("name") or ws.title)
        return f"renamed {old} to {ws.title}"
    if kind == "delete_sheet":
        ws = _sheet(book, op)
        if len(book.sheetnames) == 1:
            raise DocumentError("A workbook keeps at least one sheet.")
        book.remove(ws)
        return f"deleted sheet {ws.title}"
    ws = _sheet(book, op)
    if kind == "set":
        values = op.get("cells")
        if not isinstance(values, dict) or not values:
            raise DocumentError('set needs `cells`, e.g. {"A1": "Total", "B1": "=SUM(B2:B9)"}.')
        for ref, value in values.items():
            for cell in _cells(ws, ref):
                cell.value = value
        return f"set {len(values)} cell{'s' if len(values) != 1 else ''} on {ws.title}"
    if kind == "append_rows":
        rows = op.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
            raise DocumentError("append_rows needs `rows`: a list of lists.")
        for row in rows:
            ws.append(row)
        return f"appended {len(rows)} rows to {ws.title}"
    if kind in ("insert_rows", "delete_rows", "insert_cols", "delete_cols"):
        at, count = int(op.get("at") or 0), int(op.get("count") or 1)
        if at < 1 or count < 1:
            raise DocumentError(f"{kind} needs `at` (1-based) and a positive `count`.")
        getattr(ws, kind)(at, count)
        return f"{kind.replace('_', ' ')} {count} at {at} on {ws.title}"
    if kind in ("merge", "unmerge"):
        ref = str(op.get("range") or "")
        (ws.merge_cells if kind == "merge" else ws.unmerge_cells)(ref)
        return f"{kind}d {ref}"
    if kind == "freeze":
        ws.freeze_panes = op.get("cell") or None
        return f"froze panes at {op.get('cell') or 'nothing'}"
    if kind == "format":
        ref = str(op.get("range") or "")
        cells = _cells(ws, ref)
        for cell in cells:
            if any(key in op for key in ("bold", "italic", "color")):
                font = copy(cell.font)
                if "bold" in op:
                    font.bold = bool(op["bold"])
                if "italic" in op:
                    font.italic = bool(op["italic"])
                if op.get("color"):
                    font.color = str(op["color"]).lstrip("#")
                cell.font = font
            if op.get("number_format"):
                cell.number_format = str(op["number_format"])
            if op.get("fill"):
                cell.fill = PatternFill("solid", fgColor=str(op["fill"]).lstrip("#"))
            if "wrap" in op or "align" in op:
                alignment = copy(cell.alignment)
                if "align" in op:
                    alignment.horizontal = op["align"]
                if "wrap" in op:
                    alignment.wrap_text = bool(op["wrap"])
                cell.alignment = alignment
        if op.get("width"):
            for column in sorted({c.column for c in cells}):
                ws.column_dimensions[get_column_letter(column)].width = float(op["width"])
        return f"formatted {ref} on {ws.title}"
    raise DocumentError(
        f"Unknown operation {kind!r}: use set, append_rows, insert_rows, delete_rows, insert_cols, delete_cols, "
        "add_sheet, rename_sheet, delete_sheet, merge, unmerge, freeze or format."
    )


# ----- CSV ---------------------------------------------------------------------


def _edit_csv(path: Path, operations: list[dict[str, Any]]) -> dict:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    rows: list[list[str]] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = [list(r) for r in csv.reader(handle, delimiter=delimiter)]
    done = []
    for op in operations:
        kind = str(op.get("op") or "")
        if kind == "append_rows":
            rows.extend([str(v) if v is not None else "" for v in row] for row in op.get("rows") or [])
            done.append(f"appended {len(op.get('rows') or [])} rows")
        elif kind == "set":
            from openpyxl.utils.cell import coordinate_from_string, column_index_from_string

            for ref, value in (op.get("cells") or {}).items():
                letters, number = coordinate_from_string(ref)
                r, c = number - 1, column_index_from_string(letters) - 1
                while len(rows) <= r:
                    rows.append([])
                while len(rows[r]) <= c:
                    rows[r].append("")
                rows[r][c] = "" if value is None else str(value)
            done.append(f"set {len(op.get('cells') or {})} cells")
        elif kind in ("insert_rows", "delete_rows"):
            at, count = int(op.get("at") or 0) - 1, int(op.get("count") or 1)
            if at < 0:
                raise DocumentError(f"{kind} needs `at` (1-based).")
            if kind == "insert_rows":
                rows[at:at] = [[] for _ in range(count)]
            else:
                del rows[at : at + count]
            done.append(f"{kind.replace('_', ' ')} {count} at {at + 1}")
        else:
            raise DocumentError(f"A CSV supports set, append_rows, insert_rows and delete_rows, not {kind!r}.")

    def write(tmp: str) -> None:
        with open(tmp, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, delimiter=delimiter).writerows(rows)

    _atomic_save(path, write)
    return {"done": done, "rows": len(rows)}
