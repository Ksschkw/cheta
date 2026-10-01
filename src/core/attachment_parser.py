"""Local, free extraction for inbound attachments.

Every parser runs in-process with no network call, so attachment handling never
reaches a third-party service and never costs a provider key. Input we cannot
read is refused explicitly by name; this module never guesses at a format and
never pretends that a file it could not read said something.

The advertised capability and the handled capability are one object:
``SUPPORTED_EXTENSIONS`` is the single source of truth, classification reads it,
and the extraction dispatch accepts exactly the kinds it names. A prompt that
lists the formats is generated later from the same mapping, so the list the
model sees cannot drift from what this module can actually read.

Audio is classified but never parsed here. A voice note or an audio file is
routed to the transcription gateway by the caller; reading compressed audio as
text would only produce garbage.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from html.parser import HTMLParser
from io import BytesIO

from core.errors import AttachmentError, LegacyFormatError

# Telegram's own bot download ceiling. A file above this cannot be fetched at
# all, so refusing it before the getFile call saves bandwidth and an error.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# A cap on extracted characters so a huge document cannot flood the prompt,
# the extraction step or the model's context window.
MAX_EXTRACTED_CHARS = 12000

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_MIME = "application/vnd.ms-excel.sheet.macroenabled.12"

# The one source of truth for what this module reads, as data. It maps a kind to
# every extension that resolves to it. Classification, extraction dispatch and
# (later) the capability prompt all derive from this exact object, so there is no
# second list that can disagree with the first.
SUPPORTED_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "docx": (".docx",),
    "pptx": (".pptx",),
    "xlsx": (".xlsx", ".xlsm"),
    "xml": (".xml",),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "html": (".html", ".htm", ".xhtml"),
    "csv": (".csv", ".tsv"),
    "text": (
        ".txt",
        ".text",
        ".md",
        ".markdown",
        ".rst",
        ".log",
        ".ini",
        ".cfg",
        ".toml",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".go",
        ".rs",
        ".sh",
        ".rb",
        ".php",
        ".cs",
        ".kt",
        ".swift",
        ".sql",
        ".css",
    ),
    "audio": (".mp3", ".m4a", ".ogg", ".oga", ".opus", ".wav", ".webm", ".flac"),
}

# The exact object a capability prompt reads. The parser routes on this same
# object, so what is advertised and what is handled cannot diverge.
ADVERTISED_EXTENSIONS = SUPPORTED_EXTENSIONS

# The old binary Microsoft formats. There is no converter in-process and no
# dependency that can read them, so they are refused by name with the format to
# save as, instead of being decoded into garbage.
LEGACY_FORMATS: dict[str, tuple[str, str]] = {
    ".doc": ("Word", ".docx"),
    ".ppt": ("PowerPoint", ".pptx"),
    ".xls": ("Excel", ".xlsx"),
}
LEGACY_KIND = "legacy"

# Extension to kind, built from the same mapping the prompt is generated from.
_KIND_BY_SUFFIX: dict[str, str] = {
    extension: kind
    for kind, extensions in SUPPORTED_EXTENSIONS.items()
    for extension in extensions
}

MIME_BY_KIND: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": DOCX_MIME,
    "pptx": PPTX_MIME,
    "xlsx": XLSX_MIME,
    "xml": "application/xml",
    "json": "application/json",
    "yaml": "application/yaml",
    "html": "text/html",
    "csv": "text/csv",
    "text": "text/plain",
    "audio": "audio/mpeg",
}

# Surfaces disagree about MIME types, so common aliases are accepted too.
_MIME_ALIASES: dict[str, str] = {
    "text/xml": "xml",
    "text/json": "json",
    "application/x-yaml": "yaml",
    "text/yaml": "yaml",
    "application/xhtml+xml": "html",
    "text/x-markdown": "text",
    "text/markdown": "text",
    "application/csv": "csv",
    XLSM_MIME: "xlsx",
    "audio/mp3": "audio",
    "audio/mp4": "audio",
    "audio/x-m4a": "audio",
    "audio/m4a": "audio",
    "audio/ogg": "audio",
    "application/ogg": "audio",
    "audio/opus": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/wave": "audio",
    "audio/webm": "audio",
    "audio/flac": "audio",
    "audio/x-flac": "audio",
    "audio/x-mpeg": "audio",
    "audio/mpeg3": "audio",
}

_KIND_BY_MIME: dict[str, str] = {mime: kind for kind, mime in MIME_BY_KIND.items()}
_KIND_BY_MIME.update(_MIME_ALIASES)


def _suffix(file_name: str) -> str:
    lowered = file_name.strip().lower()
    dot = lowered.rfind(".")
    return lowered[dot:] if dot >= 0 else ""


def sniff_text(content: bytes) -> bool:
    """True when bytes are plainly text: valid UTF-8 and free of NUL bytes.

    An unknown extension is not proof that a file is binary. If the bytes decode
    cleanly as UTF-8 and contain no NUL, treating them as text is honest; a NUL
    byte is the one signal that says "this is not a text document".
    """
    if not content:
        return False
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def legacy_format_message(file_name: str) -> str:
    """The actionable refusal for an old binary Microsoft format."""
    suffix = _suffix(file_name)
    family, replacement = LEGACY_FORMATS[suffix]
    name = file_name.strip() or f"that {suffix} file"
    return (
        f"{name} is the old binary {family} format, which I cannot read. "
        f"Save it as {replacement} and send it again."
    )


def classify_document(
    file_name: str, mime_type: str, content: bytes | None = None
) -> str | None:
    """Return the kind this module routes, or None when it cannot read the file.

    The filename wins because surfaces are inconsistent about MIME types, but a
    correct MIME type still rescues a file sent without an extension. When the
    name and MIME are both unknown and the bytes are available, the bytes are
    sniffed so an unlabelled text file is still read. Legacy binary formats are
    answered with their own kind so the caller can refuse them by name.
    """
    suffix = _suffix(file_name)
    if suffix in LEGACY_FORMATS:
        return LEGACY_KIND
    kind = _KIND_BY_SUFFIX.get(suffix)
    if kind is not None:
        return kind
    kind = _KIND_BY_MIME.get(mime_type.strip().lower())
    if kind is not None:
        return kind
    if content is not None and sniff_text(content):
        return "text"
    return None


class AttachmentParser:
    """Extract plain text from one supported document, entirely offline."""

    # The same object the capability prompt reads and classification uses.
    capabilities = ADVERTISED_EXTENSIONS

    def classify(
        self, file_name: str, mime_type: str, content: bytes | None = None
    ) -> str | None:
        return classify_document(file_name, mime_type, content)

    def handled_kinds(self) -> frozenset[str]:
        """Every kind this parser can route, derived from the advertised set."""
        return frozenset(self.capabilities)

    def handles(self, kind: str) -> bool:
        return kind in self.capabilities

    def extract(self, kind: str, file_name: str, content: bytes) -> str:
        if kind == LEGACY_KIND:
            raise LegacyFormatError(legacy_format_message(file_name))
        if kind not in self.capabilities:
            raise AttachmentError(f"no local reader for attachment kind {kind!r}")
        if kind == "audio":
            name = file_name.strip() or "this file"
            raise AttachmentError(
                f"{name} is an audio file, so it has to be transcribed, not read "
                "as text"
            )
        if kind == "pdf":
            return self._extract_pdf(content)
        if kind == "docx":
            return self._extract_docx(content)
        if kind == "pptx":
            return self._extract_pptx(content)
        if kind == "xlsx":
            return self._extract_xlsx(content)
        if kind in ("xml", "json", "yaml"):
            return self._extract_structured(kind, content)
        if kind == "html":
            return self._extract_html(content)
        # Plain text and CSV/TSV are already text; decode and hand it on.
        return content.decode("utf-8", errors="replace")

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

    def _extract_pptx(self, content: bytes) -> str:
        try:
            from pptx import Presentation
        except ImportError as error:
            raise AttachmentError("PPTX support is not installed on this server") from error
        try:
            presentation = Presentation(BytesIO(content))
            slides = [
                self._slide_text(index, slide)
                for index, slide in enumerate(presentation.slides, start=1)
            ]
        except Exception as error:  # noqa: BLE001
            raise AttachmentError(
                f"the PPTX could not be parsed ({type(error).__name__})"
            ) from error
        return "\n\n".join(slides)

    def _slide_text(self, index: int, slide: object) -> str:
        lines = [f"[Slide {index}]"]
        for shape in slide.shapes:  # type: ignore[attr-defined]
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text  # type: ignore[attr-defined]
                if text.strip():
                    lines.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:  # type: ignore[attr-defined]
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
        if getattr(slide, "has_notes_slide", False):  # type: ignore[attr-defined]
            notes = slide.notes_slide.notes_text_frame.text  # type: ignore[attr-defined]
            if notes.strip():
                lines.append("[Notes]")
                lines.append(notes)
        return "\n".join(lines)

    def _extract_xlsx(self, content: bytes) -> str:
        try:
            from openpyxl import load_workbook
        except ImportError as error:
            raise AttachmentError("XLSX support is not installed on this server") from error
        try:
            workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
        except Exception as error:  # noqa: BLE001
            raise AttachmentError(
                f"the spreadsheet could not be parsed ({type(error).__name__})"
            ) from error
        try:
            sheets = [self._sheet_text(sheet) for sheet in workbook.worksheets]
        finally:
            workbook.close()
        return "\n\n".join(sheets)

    def _sheet_text(self, sheet: object) -> str:
        lines = [f"[Sheet: {sheet.title}]"]  # type: ignore[attr-defined]
        for row in sheet.iter_rows(values_only=True):  # type: ignore[attr-defined]
            cells = ["" if value is None else str(value) for value in row]
            line = " | ".join(cells).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _extract_structured(self, kind: str, content: bytes) -> str:
        raw = content.decode("utf-8", errors="replace")
        try:
            if kind == "json":
                return json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
            if kind == "xml":
                return self._render_xml(content)
            import yaml

            loaded = yaml.safe_load(raw)
            return yaml.safe_dump(
                loaded, allow_unicode=True, sort_keys=False, default_flow_style=False
            )
        except Exception:  # noqa: BLE001 - a failed parse is answered with raw text
            return (
                f"[could not parse this {kind.upper()} document as {kind}; "
                f"showing the raw text instead]\n\n{raw}"
            )

    def _render_xml(self, content: bytes) -> str:
        root = ElementTree.fromstring(content)
        ElementTree.indent(root, space="  ")
        return ElementTree.tostring(root, encoding="unicode")

    def _extract_html(self, content: bytes) -> str:
        markup = content.decode("utf-8", errors="replace")
        parser = _VisibleTextParser()
        parser.feed(markup)
        parser.close()
        return parser.visible_text()


class _VisibleTextParser(HTMLParser):
    """Collect visible text, dropping scripts, styles and markup noise."""

    _SKIP = {"script", "style"}
    _BLOCK = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def visible_text(self) -> str:
        lines = [line.strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)
