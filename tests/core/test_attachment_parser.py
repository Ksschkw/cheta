"""Attachment parser contracts.

These prove the formats the bot claims to read are the formats it actually
reads, that legacy binary formats are refused by name instead of decoded into
noise, and that audio is classified for transcription rather than parsed as
text. Fixtures are built in-process with the real libraries, so the suite never
touches the network.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from core.attachment_parser import (
    ADVERTISED_EXTENSIONS,
    LEGACY_KIND,
    MAX_ATTACHMENT_BYTES,
    SUPPORTED_EXTENSIONS,
    AttachmentParser,
    classify_document,
    legacy_format_message,
    sniff_text,
)
from core.errors import AttachmentError, LegacyFormatError
from main import create_app
from tests.routers.test_chat_and_memory_routers import build_test_container


def build_pptx(slides: list[tuple[str, str, str]]) -> bytes:
    """Two real slides (title, body, notes) written by python-pptx."""
    presentation = Presentation()
    for title, body, notes in slides:
        slide = presentation.slides.add_slide(presentation.slide_layouts[5])
        slide.shapes.title.text = title
        box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(5), Inches(1))
        box.text_frame.text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    buffer = BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def build_xlsx(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    workbook = Workbook()
    first = True
    for name, rows in sheets:
        sheet = workbook.active if first else workbook.create_sheet()
        first = False
        sheet.title = name
        for row in rows:
            sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------- documents


def test_a_pptx_with_two_slides_yields_both_slide_texts_in_order() -> None:
    content = build_pptx(
        [
            ("Opening", "the first body line", "a speaker note"),
            ("Closing", "the second body line", ""),
        ]
    )

    text = AttachmentParser().extract("pptx", "deck.pptx", content)

    assert "[Slide 1]" in text
    assert "[Slide 2]" in text
    assert "Opening" in text
    assert "the first body line" in text
    assert "a speaker note" in text
    assert "Closing" in text
    assert "the second body line" in text
    # Order is the deck order, not dictionary order.
    assert (
        text.index("[Slide 1]")
        < text.index("the first body line")
        < text.index("[Slide 2]")
        < text.index("the second body line")
    )


def test_an_xlsx_with_two_sheets_yields_both_sheet_names_and_cell_values() -> None:
    content = build_xlsx(
        [
            ("Alpha", [["name", "score"], ["ada", 10]]),
            ("Beta", [["city"], ["london"]]),
        ]
    )

    text = AttachmentParser().extract("xlsx", "book.xlsx", content)

    assert "[Sheet: Alpha]" in text
    assert "[Sheet: Beta]" in text
    assert "name" in text
    assert "ada" in text
    assert "10" in text
    assert "london" in text
    assert text.index("[Sheet: Alpha]") < text.index("[Sheet: Beta]")
    # The same reader handles the macro-enabled container, by name or by type.
    assert AttachmentParser().classify("book.xlsm", "") == "xlsx"
    assert (
        AttachmentParser().classify("book", "application/vnd.ms-excel.sheet.macroEnabled.12")
        == "xlsx"
    )


@pytest.mark.parametrize(
    ("kind", "file_name", "content", "expected"),
    [
        ("xml", "data.xml", b"<root><item>alpha</item></root>", "alpha"),
        ("json", "data.json", b'{"name": "beta"}', "beta"),
        ("yaml", "data.yaml", b"name: gamma\n", "gamma"),
        ("html", "page.html", b"<html><body><p>delta</p></body></html>", "delta"),
        ("csv", "rows.csv", b"city,code\nlondon,44\n", "london,44"),
        ("text", "notes.md", b"# Heading\n\nsome prose\n", "some prose"),
        ("text", "script.py", b"def answer():\n    return 42\n", "def answer():"),
    ],
)
def test_each_text_format_round_trips(
    kind: str, file_name: str, content: bytes, expected: str
) -> None:
    parser = AttachmentParser()

    assert parser.classify(file_name, "") == kind
    assert expected in parser.extract(kind, file_name, content)


def test_malformed_json_or_xml_falls_back_to_raw_text_instead_of_refusing() -> None:
    parser = AttachmentParser()

    broken_json = b'{"name": "beta", '
    json_text = parser.extract("json", "data.json", broken_json)
    assert "beta" in json_text
    assert "raw text" in json_text.lower()

    broken_xml = b"<root><item>alpha</root>"
    xml_text = parser.extract("xml", "data.xml", broken_xml)
    assert "alpha" in xml_text
    assert "raw text" in xml_text.lower()


def test_an_unknown_extension_with_clean_utf8_text_is_treated_as_text() -> None:
    assert sniff_text(b"plain words, no NUL byte") is True
    assert classify_document("mystery.zzz", "application/octet-stream", b"hello world") == "text"
    assert classify_document("mystery", "application/octet-stream", b"no extension") == "text"
    # Without bytes to sniff, an unknown name is still refused rather than guessed.
    assert classify_document("mystery.zzz", "application/octet-stream") is None

    parser = AttachmentParser()
    kind = parser.classify("mystery.zzz", "application/octet-stream", b"hello world")
    assert parser.extract(kind, "mystery.zzz", b"hello world") == "hello world"


def test_a_file_containing_nul_bytes_is_not_treated_as_text() -> None:
    binary = b"looks like text\x00but is not"

    assert sniff_text(binary) is False
    assert sniff_text(b"still binary\x00") is False
    assert classify_document("mystery.zzz", "application/octet-stream", binary) is None


@pytest.mark.parametrize(
    ("file_name", "replacement"),
    [("report.doc", ".docx"), ("deck.ppt", ".pptx"), ("sheet.xls", ".xlsx")],
)
def test_legacy_binary_formats_are_refused_by_name_with_save_as_advice(
    file_name: str, replacement: str
) -> None:
    parser = AttachmentParser()

    assert parser.classify(file_name, "") == LEGACY_KIND
    message = legacy_format_message(file_name)
    assert file_name in message
    assert replacement in message
    assert "old binary" in message
    assert "send it again" in message
    # It must never be decoded into garbage: extraction refuses too.
    with pytest.raises(LegacyFormatError) as raised:
        parser.extract(LEGACY_KIND, file_name, b"\xd0\xcf\x11\xe0binary")
    assert raised.value.message == message


def test_an_mp3_is_classified_as_audio_and_never_read_as_text() -> None:
    parser = AttachmentParser()

    assert parser.classify("track.mp3", "") == "audio"
    assert parser.classify("memo.ogg", "audio/ogg") == "audio"
    assert parser.classify("no-extension", "audio/mpeg") == "audio"
    assert classify_document("clip.wav", "") == "audio"

    with pytest.raises(AttachmentError) as raised:
        parser.extract("audio", "track.mp3", b"\x00\x01not text")
    assert "transcri" in raised.value.message.lower()


def test_the_advertised_capability_and_the_handled_capability_are_one_object() -> None:
    parser = AttachmentParser()

    # The exact object, not an equal copy that could drift.
    assert parser.capabilities is ADVERTISED_EXTENSIONS
    assert ADVERTISED_EXTENSIONS is SUPPORTED_EXTENSIONS
    assert parser.handled_kinds() == frozenset(SUPPORTED_EXTENSIONS)
    # Everything advertised must classify back to the kind that advertises it.
    for kind, extensions in parser.capabilities.items():
        assert parser.handles(kind) is True
        for extension in extensions:
            assert parser.classify(f"file{extension}", "") == kind


# --------------------------------------------------------------- size cap flow


class _NeverCalledAttachmentGateway:
    """Fails the test by recording any getFile or download call."""

    def __init__(self) -> None:
        self.path_calls: list[str] = []
        self.download_calls: list[str] = []

    async def get_file_path(self, file_id: str) -> str:
        self.path_calls.append(file_id)
        return "files/report.pdf"

    async def download_file(self, file_path: str) -> bytes:
        self.download_calls.append(file_path)
        return b"x"


def _oversized_document_update(chat_id: int, file_name: str, file_size: int) -> dict:
    return {
        "update_id": 8,
        "message": {
            "message_id": 12,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            "chat": {"id": chat_id, "type": "private"},
            "document": {
                "file_id": "file-1",
                "file_name": file_name,
                "mime_type": "application/pdf",
                "file_size": file_size,
            },
        },
    }


def test_the_size_cap_still_refuses_before_any_download_attempt() -> None:
    gateway = _NeverCalledAttachmentGateway()
    container, _, channel = build_test_container(
        [], webhook_secret="s3cret", attachment_gateway=gateway
    )
    client = TestClient(create_app(container=container))

    response = client.post(
        "/webhooks/telegram/s3cret",
        json=_oversized_document_update(555, "huge.pdf", 21 * 1024 * 1024),
    )

    assert response.json()["handled"] is True
    assert "20 MB" in channel.sent[-1][1]
    assert "did not download" in channel.sent[-1][1]
    assert MAX_ATTACHMENT_BYTES == 20 * 1024 * 1024
    # Refused on the declared size, so neither getFile nor the download ran.
    assert gateway.path_calls == []
    assert gateway.download_calls == []
