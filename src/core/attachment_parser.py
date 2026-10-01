"""Local, free extraction for inbound attachments.

Every parser runs in-process with no network call, so attachment handling never
reaches a third-party service and never costs a provider key. Unsupported input
is refused explicitly by name; this module never guesses at a format.
"""

from __future__ import annotations

from io import BytesIO

from core.errors import AttachmentError

# Telegram's own bot download ceiling. A file above this cannot be fetched at
# all, so refusing it before the getFile call saves bandwidth and an error.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# A cap on extracted characters so a huge document cannot flood the prompt,
# the extraction step or the model's context window.
MAX_EXTRACTED_CHARS = 12000

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_KIND_BY_SUFFIX = {
    ".pdf": "pdf",
    ".txt": "text",
    ".text": "text",
    ".md": "text",
    ".markdown": "text",
    ".log": "text",
    ".csv": "csv",
    ".tsv": "csv",
    ".docx": "docx",
}

_KIND_BY_MIME = {
    "application/pdf": "pdf",
    "text/plain": "text",
    "text/markdown": "text",
    "text/x-markdown": "text",
    "text/csv": "csv",
    "application/csv": "csv",
    DOCX_MIME: "docx",
}

MIME_BY_KIND = {
    "pdf": "application/pdf",
    "text": "text/plain",
    "csv": "text/csv",
    "docx": DOCX_MIME,
}


def _suffix(file_name: str) -> str:
    lowered = file_name.strip().lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot >= 0 else ""


def classify_document(file_name: str, mime_type: str) -> str | None:
    """Return pdf/text/csv/docx, or None when this project cannot read it.

    The filename wins because surfaces are inconsistent about MIME types, but a
    correct MIME type still rescues a file sent without an extension.
    """
    kind = _KIND_BY_SUFFIX.get(_suffix(file_name))
    if kind is not None:
        return kind
    return _KIND_BY_MIME.get(mime_type.strip().lower())


class AttachmentParser:
    """Extract plain text from one supported document, entirely offline."""

    def classify(self, file_name: str, mime_type: str) -> str | None:
        return classify_document(file_name, mime_type)

    def extract(self, kind: str, file_name: str, content: bytes) -> str:
        if kind == "pdf":
            return self._extract_pdf(content)
        if kind == "docx":
            return self._extract_docx(content)
        if kind in ("text", "csv"):
            return content.decode("utf-8", errors="replace")
        raise AttachmentError(f"no local reader for attachment kind {kind!r}")

    def _extract_pdf(self, content: bytes) -> str:
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as error:  # noqa: BLE001 - every parse failure is one answer
            raise AttachmentError(
                f"the PDF could not be parsed ({type(error).__name__})"
            ) from error
        return "\n\n".join(pages)

    def _extract_docx(self, content: bytes) -> str:
        try:
            from docx import Document
        except ImportError as error:
            raise AttachmentError("DOCX support is not installed on this server") from error
        try:
            document = Document(BytesIO(content))
        except Exception as error:  # noqa: BLE001
            raise AttachmentError(
                f"the DOCX could not be parsed ({type(error).__name__})"
            ) from error
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
