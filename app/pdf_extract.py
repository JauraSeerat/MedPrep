from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict


def extract_pdf_text(path: str) -> str:
    """Extract text from a PDF using multiple engines and pick richest output."""
    candidates = extract_pdf_text_candidates(path)
    best = max(candidates.values(), key=lambda t: len((t or "").strip()))
    return (best or "").strip()


def extract_pdf_text_candidates(path: str) -> Dict[str, str]:
    pdf_path = Path(path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    return {
        "fitz": _extract_with_fitz(pdf_path),
        "pypdf": _extract_with_pypdf(pdf_path),
        "ghostscript": _extract_with_ghostscript(pdf_path),
    }


def _extract_with_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _normalize_page_range(total: int, start_page: int, end_page: int) -> tuple[int, int]:
    if total <= 0:
        return (0, 0)
    start = max(0, start_page - 1)
    end = min(total, end_page)
    if end <= start:
        return (0, 0)
    return (start, end)


def _extract_with_pypdf_by_pages(path: Path, start_page: int, end_page: int) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(str(path))
        start, end = _normalize_page_range(len(reader.pages), start_page, end_page)
        if end <= start:
            return ""
        parts = []
        for i in range(start, end):
            parts.append(reader.pages[i].extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _extract_with_fitz(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""

    try:
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            parts.append(page.get_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _extract_with_ghostscript(path: Path) -> str:
    try:
        proc = subprocess.run(
            [
                "gs",
                "-q",
                "-dNOPAUSE",
                "-dBATCH",
                "-sDEVICE=txtwrite",
                "-sOutputFile=-",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""

    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def extract_pdf_text_by_pages(pdf_path: str, start_page: int, end_page: int) -> str:
    """Extract text from a specific page range (1-indexed, inclusive)."""
    try:
        path = Path(pdf_path)
        candidates = {
            "fitz": _extract_with_fitz_by_pages(path, start_page, end_page),
            "pypdf": _extract_with_pypdf_by_pages(path, start_page, end_page),
            "ghostscript": _extract_with_ghostscript_by_pages(path, start_page, end_page),
        }
        best = max(candidates.values(), key=lambda t: len((t or "").strip()))
        return (best or "").strip()
    except Exception:
        return ""


def get_pdf_page_count(pdf_path: str) -> int:
    """Return total number of pages in a PDF."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        return len(reader.pages)
    except Exception:
        return 0


def _extract_with_fitz_by_pages(path: Path, start_page: int, end_page: int) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""

    try:
        doc = fitz.open(str(path))
        try:
            start, end = _normalize_page_range(len(doc), start_page, end_page)
            if end <= start:
                return ""
            parts = []
            for i in range(start, end):
                parts.append(doc[i].get_text() or "")
            return "\n".join(parts)
        finally:
            doc.close()
    except Exception:
        return ""


def _extract_with_ghostscript_by_pages(path: Path, start_page: int, end_page: int) -> str:
    try:
        proc = subprocess.run(
            [
                "gs",
                "-q",
                "-dNOPAUSE",
                "-dBATCH",
                f"-dFirstPage={start_page}",
                f"-dLastPage={end_page}",
                "-sDEVICE=txtwrite",
                "-sOutputFile=-",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""

    if proc.returncode != 0:
        return ""
    return proc.stdout or ""
