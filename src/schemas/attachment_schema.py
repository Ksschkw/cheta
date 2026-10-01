"""Transport DTO for a file attached to an inbound message.

The router normalizes whatever the surface sent into this shape and hands it to
one use case. Media that is not a document still carries a ``media_kind`` so the
service can name exactly what it cannot read instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AttachmentSchema:
    """One inbound attachment, described without downloading it yet."""

    media_kind: str
    file_id: str = ""
    file_name: str = ""
    mime_type: str = ""
    file_size: int | None = None
    caption: str = ""
