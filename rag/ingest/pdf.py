"""Extract text from a PDF portfolio CV using the rag extra."""

from pathlib import Path


def extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise ValueError(f"PDF contains no readable text: {path}")
    return text
