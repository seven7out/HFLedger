#!/usr/bin/env python3
"""Stage the frozen engine and fictional example for the macOS app bundle."""

from pathlib import Path
import shutil


HOST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = HOST_ROOT.parents[1]
DESTINATION = HOST_ROOT / "src-tauri" / "runtime"
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def reject_unsafe_symlinks(source: Path, allow_internal: bool) -> None:
    source_root = source.resolve()
    for path in (source, *source.rglob("*")):
        if not path.is_symlink():
            continue
        resolved = path.resolve(strict=True)
        if not allow_internal or not resolved.is_relative_to(source_root):
            raise SystemExit(f"refusing unsafe symlink in staged runtime: {path}")


def main() -> None:
    if DESTINATION.exists():
        shutil.rmtree(DESTINATION)
    DESTINATION.mkdir(parents=True, mode=0o700)
    sources = (
        ("engine/hfledger-engine", HOST_ROOT / ".engine-build" / "dist" / "hfledger-engine", True),
        ("example", REPO_ROOT / "example", False),
    )
    for name, source, allow_internal_symlinks in sources:
        if not source.is_dir():
            raise SystemExit(f"missing runtime directory: {source}")
        reject_unsafe_symlinks(source, allow_internal_symlinks)
        shutil.copytree(source, DESTINATION / name, ignore=IGNORED, symlinks=True)
    print(f"staged self-contained HFLedger runtime at {DESTINATION}")


if __name__ == "__main__":
    main()
