"""Editing PDFs with pypdf: pages, forms, text, metadata, passwords.

Operations apply in order and the result is saved once, atomically, to the
file itself or to ``output``; the saved PDF is opened again before the edit
counts. Text is real page content (Helvetica, drawn over the page), not an
annotation, so it prints and survives flattening.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable

from .readers import DocumentError, page_numbers
from .sheets import _atomic_save

#: US Letter in points, for new pages when no size is given.
LETTER = (612.0, 792.0)


def edit(path: Path, operations: list[dict[str, Any]], *, output: Path | None = None, resolve: Callable[[str], Path] | None = None) -> dict:
    """``resolve`` turns a path in an operation (``merge``'s files) into a checked one."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    if not operations:
        raise DocumentError("No operations given.")
    creating = not path.exists()
    if creating and not any(op.get("op") == "add_page" for op in operations):
        raise DocumentError(f"{path.name} does not exist; start a new PDF with an `add_page` operation.")
    try:
        writer = PdfWriter() if creating else PdfWriter(clone_from=PdfReader(str(path)))
    except PdfReadError as error:
        raise DocumentError(f"Could not open the PDF: {error}") from error
    if not creating and writer.is_encrypted:
        raise DocumentError("The PDF is password-protected.")
    done = []
    for op in operations:
        writer, note = _apply(writer, op, resolve or Path)
        done.append(note)
    target = output or path

    def write(tmp: str) -> None:
        with open(tmp, "wb") as handle:
            writer.write(handle)

    _atomic_save(target, write)
    check = PdfReader(str(target))
    pages = len(check.pages) if not check.is_encrypted else None
    return {"done": done, "pages": pages, "path": str(target)}


def _apply(writer, op: dict[str, Any], resolve: Callable[[str], Path]):
    from pypdf import PdfReader, PdfWriter

    kind = str(op.get("op") or "")
    count = len(writer.pages)
    if kind == "merge":
        files = op.get("files") or []
        if not isinstance(files, list) or not files:
            raise DocumentError("merge needs `files`: the PDFs to add, in order.")
        position = op.get("at")
        for name in files:
            other = PdfReader(str(resolve(str(name))))
            if position is None:
                writer.append(other)
            else:
                writer.merge(int(position) - 1, other)
                position = int(position) + len(other.pages)
        return writer, f"merged {len(files)} file{'s' if len(files) != 1 else ''}"
    if kind in ("delete_pages", "keep_pages"):
        chosen = set(page_numbers(str(op.get("pages") or ""), count))
        drop = sorted(chosen if kind == "delete_pages" else set(range(count)) - chosen, reverse=True)
        if len(drop) == count:
            raise DocumentError("That would leave the PDF with no pages.")
        for index in drop:
            writer.remove_page(index, clean=True)
        return writer, f"removed {len(drop)} page{'s' if len(drop) != 1 else ''}"
    if kind == "rotate":
        degrees = int(op.get("degrees") or 90)
        if degrees % 90:
            raise DocumentError("rotate takes a multiple of 90 degrees.")
        chosen = page_numbers(str(op.get("pages") or ""), count)
        for index in chosen:
            writer.pages[index].rotate(degrees)
        return writer, f"rotated {len(chosen)} page{'s' if len(chosen) != 1 else ''} by {degrees}°"
    if kind == "reorder":
        order = page_numbers(str(op.get("order") or ""), count)
        if sorted(order) != list(range(count)):
            raise DocumentError(f"reorder needs every page exactly once, e.g. `3,1,2` for 3 pages (this PDF has {count}).")
        reordered = PdfWriter()
        for index in order:
            reordered.add_page(writer.pages[index])
        if writer.metadata:
            reordered.add_metadata(dict(writer.metadata))
        return reordered, "reordered pages"
    if kind == "add_page":
        width, height = float(op.get("width") or LETTER[0]), float(op.get("height") or LETTER[1])
        if op.get("at"):
            writer.insert_blank_page(width, height, int(op["at"]) - 1)
        else:
            writer.add_blank_page(width, height)
        return writer, f"added a {int(width)}×{int(height)} page"
    if kind == "add_text":
        page_number = int(op.get("page") or 1)
        if not 1 <= page_number <= count:
            raise DocumentError(f"Page {page_number} is outside 1-{count}.")
        text = str(op.get("text") or "")
        if not text:
            raise DocumentError("add_text needs `text`.")
        _draw_text(writer.pages[page_number - 1], text, x=float(op.get("x", 72)), y=float(op.get("y", 72)), size=float(op.get("size") or 12), color=str(op.get("color") or "000000"))
        return writer, f"added text to page {page_number}"
    if kind == "fill_form":
        fields = op.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise DocumentError('fill_form needs `fields`, e.g. {"name": "Ada"} (read_document lists them).')
        known = set((writer.get_fields() or {}).keys()) if hasattr(writer, "get_fields") else set()
        unknown = sorted(set(fields) - known) if known else []
        if unknown:
            raise DocumentError(f"No form fields named {', '.join(unknown)}; the fields are {', '.join(sorted(known))}.")
        values = {str(k): str(v) for k, v in fields.items()}
        writer.set_need_appearances_writer(True)
        for page in writer.pages:
            if page.get("/Annots"):
                writer.update_page_form_field_values(page, values, auto_regenerate=False, flatten=bool(op.get("flatten")))
        return writer, f"filled {len(values)} field{'s' if len(values) != 1 else ''}" + (" and flattened" if op.get("flatten") else "")
    if kind == "metadata":
        names = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
        info = {pdf: str(op[key]) for key, pdf in names.items() if key in op}
        if not info:
            raise DocumentError("metadata takes title, author, subject or keywords.")
        writer.add_metadata(info)
        return writer, f"set {', '.join(k for k in names if k in op)}"
    if kind == "encrypt":
        password = str(op.get("password") or "")
        if not password:
            raise DocumentError("encrypt needs `password`.")
        writer.encrypt(user_password=password, algorithm="AES-256")
        return writer, "encrypted with AES-256"
    raise DocumentError(
        f"Unknown operation {kind!r}: use merge, delete_pages, keep_pages, rotate, reorder, add_page, add_text, fill_form, metadata or encrypt."
    )


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _draw_text(page, text: str, *, x: float, y: float, size: float, color: str) -> None:
    """Draw ``text`` with its top-left corner ``x``, ``y`` points from the page's top-left."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    box = page.mediabox
    width, height = float(box.width), float(box.height)
    left, bottom = float(box.left), float(box.bottom)
    hexcolor = (color.lstrip("#") + "000000")[:6]
    try:
        r, g, b = (int(hexcolor[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError as error:
        raise DocumentError(f"Bad color {color!r}: use hex like `cc0000`.") from error
    leading = size * 1.2
    lines = text.splitlines() or [""]
    ops = [b"BT", f"/RootF1 {size:g} Tf".encode(), f"{r:.3f} {g:.3f} {b:.3f} rg".encode(), f"{leading:g} TL".encode()]
    ops.append(f"{left + x:g} {bottom + height - y - size:g} Td".encode())
    for index, line in enumerate(lines):
        ops.append((b"T* " if index else b"") + b"(" + _escape(line) + b") Tj")
    ops.append(b"ET")

    overlay_writer = PdfWriter()
    overlay = overlay_writer.add_blank_page(width, height)
    overlay.mediabox = box
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    overlay[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/RootF1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"\n".join(ops))
    overlay.replace_contents(stream)
    # A rotated page draws its content rotated: compensate so the text reads upright.
    rotation = int(page.get("/Rotate") or 0) % 360
    if rotation:
        overlay.rotate(-rotation).transfer_rotation_to_content()
    page.merge_page(overlay)


def page_text(path: Path, page: int) -> str:
    """The extracted text of one page (tests and verification)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(path.read_bytes()))
    return reader.pages[page - 1].extract_text() or ""
