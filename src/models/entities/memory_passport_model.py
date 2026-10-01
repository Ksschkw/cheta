"""The portable memory passport. Imports nothing from this project.

The format lives here, not in a service, because two use cases need it (the
admin export and the Telegram "Export my memory" button) and a service is not
allowed to import another service. It takes duck-typed user and memory records
so it stays the innermost layer with no project imports.
"""

from __future__ import annotations

from datetime import UTC, datetime

PASSPORT_FORMAT = "ranti.memory-passport.v1"


def build_passport(user: object, records: object, namespace: str) -> dict[str, object]:
    """Bundle one identity and every note stored for it into portable JSON.

    The index is data, not application state: it travels with the memories,
    which is why a wiped install can rebuild from this file alone.
    """
    return {
        "format": PASSPORT_FORMAT,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "user": {
            "display_name": user.display_name,  # type: ignore[attr-defined]
            "surface": user.surface,  # type: ignore[attr-defined]
            "surface_user_id": user.surface_user_id,  # type: ignore[attr-defined]
        },
        "namespace": namespace,
        "memories": [
            {
                "blob_id": record.blob_id,
                "text": record.text,
                "status": record.status,
                "importance": record.importance,
                "origin_surface": record.origin_surface,
                "superseded_by": record.superseded_by,
                "occurred_at": record.occurred_at,
            }
            for record in records  # type: ignore[union-attr]
        ],
    }
