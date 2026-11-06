#!/usr/bin/env python3
"""Build a portable source archive in dist/, without local Git or runtime data."""

import hashlib
from pathlib import Path
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", "dist", "__pycache__", ".todo", ".timer", ".app-usage", "backups", "before-restore"}


def include(member):
    relative = Path(member.name).parts[1:]
    if any(part in EXCLUDED for part in relative):
        return None
    name = Path(member.name).name
    if name.startswith(".env") or ".sqlite3" in name or name.endswith((".pyc", ".bak", ".log", ".key", ".pem")):
        return None
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    return member


def main():
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "arch-hyprland-dotfiles.tar.gz"
    with tempfile.TemporaryDirectory(prefix="dotfiles-release-") as temporary:
        pending = Path(temporary) / archive.name
        with tarfile.open(pending, "w:gz") as stream:
            stream.add(ROOT, arcname=ROOT.name, filter=include)
        with tarfile.open(pending, "r:gz") as stream:
            members = stream.getnames()
            if not any(name.endswith("/docs/screenshots/01-desktop.png") for name in members):
                raise RuntimeError("Screenshots missing from archive")
        archive.write_bytes(pending.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    print(f"{archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
