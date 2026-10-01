#!/usr/bin/env python3
"""Package the Cheta browser extension for Chromium and for Firefox.

Firefox only ever reads a file called manifest.json, so a folder with a
separate manifest.firefox.json cannot be loaded directly. This script turns the
single source folder into two loadable folders:

    python3 scripts/build_extension.py

Outputs, both rebuilt from scratch on every run:

    extension-dist/chrome/    shared files plus the Chromium manifest.json
    extension-dist/firefox/   shared files plus the Firefox manifest renamed
                              to manifest.json

Standard library only: pathlib and shutil. No build tooling, no bundler.

ASCII only by policy: no emojis, no smart punctuation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "extension"
DIST = REPO_ROOT / "extension-dist"

CHROMIUM_MANIFEST = SOURCE / "manifest.json"
FIREFOX_MANIFEST = SOURCE / "manifest.firefox.json"
SOURCE_MANIFESTS = (CHROMIUM_MANIFEST, FIREFOX_MANIFEST)

# Every target gets the same shared files; only the manifest differs.
TARGETS = (
    ("chrome", CHROMIUM_MANIFEST),
    ("firefox", FIREFOX_MANIFEST),
)


def relative(path: Path) -> str:
    """Path relative to the repository root, for readable output."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check_inputs() -> None:
    """Exit non-zero if any input the package needs is missing."""
    missing = [
        path
        for path in (SOURCE, CHROMIUM_MANIFEST, FIREFOX_MANIFEST)
        if not path.exists()
    ]
    if missing:
        for path in missing:
            print(f"[FAIL] missing input: {relative(path)}")
        raise SystemExit(1)


def shared_files() -> list[Path]:
    """Source files copied into both targets: everything but the manifests."""
    files: list[Path] = []
    for path in sorted(SOURCE.iterdir()):
        if path in SOURCE_MANIFESTS:
            continue
        if path.name.startswith("."):
            continue
        # Directories matter: the manifests declare icons/*.png, and copying only
        # top-level files produced a build whose icons were all missing.
        if path.is_dir() or path.is_file():
            files.append(path)
    return files


def build_target(name: str, manifest: Path) -> list[Path]:
    """Write one loadable directory and return the files it contains."""
    target = DIST / name
    if target.exists():
        # Only ever remove a directory that really sits inside extension-dist.
        resolved = target.resolve()
        if DIST.resolve() not in resolved.parents:
            print(f"[FAIL] refusing to remove unexpected path: {resolved}")
            raise SystemExit(1)
        shutil.rmtree(target)
    target.mkdir(parents=True)

    written: list[Path] = []
    for source in shared_files():
        destination = target / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
            written.extend(sorted(p for p in destination.rglob("*") if p.is_file()))
            continue
        shutil.copy2(source, destination)
        written.append(destination)

    manifest_destination = target / "manifest.json"
    shutil.copy2(manifest, manifest_destination)
    written.append(manifest_destination)
    return written


def main() -> int:
    check_inputs()
    for name, manifest in TARGETS:
        written = build_target(name, manifest)
        print(f"[OK] {relative(DIST / name)}/ ({len(written)} files)")
        for path in written:
            print(f"     {relative(path)}")
    print("[OK] both targets rebuilt. Load each manifest.json unpacked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
