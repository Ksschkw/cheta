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


import zipfile


def build_archives() -> None:
    """Create .xpi and .zip archives for Firefox so about:debugging works even when .json cannot be selected."""
    firefox_dir = DIST / "firefox"
    if not firefox_dir.exists():
        return
    xpi_path = DIST / "cheta-firefox.xpi"
    zip_path = DIST / "cheta-firefox.zip"
    with zipfile.ZipFile(xpi_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(firefox_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(firefox_dir)))
    shutil.copy2(xpi_path, zip_path)
    print(f"[OK] {relative(xpi_path)} (Firefox add-on package)")
    print(f"[OK] {relative(zip_path)} (Firefox zip package)")

    chrome_dir = DIST / "chrome"
    if chrome_dir.exists():
        chrome_zip = DIST / "cheta-chrome.zip"
        with zipfile.ZipFile(chrome_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(chrome_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(chrome_dir)))
        print(f"[OK] {relative(chrome_zip)} (Chrome zip package)")

    web_downloads = REPO_ROOT / "src" / "web" / "downloads"
    if web_downloads.exists():
        if (DIST / "cheta-firefox.xpi").exists():
            shutil.copy2(DIST / "cheta-firefox.xpi", web_downloads / "cheta-firefox.xpi")
        if (DIST / "cheta-firefox.zip").exists():
            shutil.copy2(DIST / "cheta-firefox.zip", web_downloads / "cheta-firefox.zip")
        if (DIST / "cheta-chrome.zip").exists():
            shutil.copy2(DIST / "cheta-chrome.zip", web_downloads / "cheta-chrome.zip")
        print(f"[OK] {relative(web_downloads)} (synced pre-packaged downloads)")


def main() -> int:
    check_inputs()
    for name, manifest in TARGETS:
        written = build_target(name, manifest)
        print(f"[OK] {relative(DIST / name)}/ ({len(written)} files)")
        for path in written:
            print(f"     {relative(path)}")
    build_archives()
    print("[OK] all targets and archives rebuilt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
