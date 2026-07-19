#!/usr/bin/env python3
"""Verify a built Mac app and emit a deterministic artifact manifest."""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
import json
import plistlib
import subprocess


HOST_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = HOST_ROOT / "src-tauri" / "target" / "release" / "bundle"
DEFAULT_APP = BUNDLE_ROOT / "macos" / "HFLedger.app"
PRIVATE_MARKERS = (
    b"/" + b"Users" + b"/",
    b"/" + b"home" + b"/",
    b"~/" + b"HF" + b"LC",
    b"/" + b"HF" + b"LC" + b"/",
)


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run(*args: object) -> None:
    subprocess.run([str(value) for value in args], check=True)


def bundle_files(app: Path) -> list[dict[str, object]]:
    root = app.resolve()
    records = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise SystemExit(f"unsafe external symlink in app bundle: {path}")
            continue
        if not path.is_file():
            continue
        payload = path.read_bytes()
        marker = next((value for value in PRIVATE_MARKERS if value in payload), None)
        if marker:
            raise SystemExit(f"private machine marker {marker!r} found in {path}")
        records.append({
            "path": path.relative_to(app).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        })
    return records


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--require-notarized", action="store_true")
    args = parser.parse_args()
    app = args.app.resolve()
    if not app.is_dir():
        raise SystemExit(f"app bundle not found: {app}")

    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    engine = app / "Contents" / "Resources" / "runtime" / "engine" / "hfledger-engine" / "hfledger-engine"
    if not engine.is_file():
        raise SystemExit(f"frozen engine not found: {engine}")
    version_output = subprocess.run(
        [str(engine), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
    if args.require_notarized:
        run("/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=2", app)
        run("/usr/bin/xcrun", "stapler", "validate", app)

    artifacts = []
    for candidate in sorted((BUNDLE_ROOT / "dmg").glob("*.dmg")):
        artifacts.append({
            "path": candidate.relative_to(BUNDLE_ROOT).as_posix(),
            "bytes": candidate.stat().st_size,
            "sha256": digest(candidate),
        })
    architecture = subprocess.run(
        ["/usr/bin/file", "-b", str(engine)], check=True, capture_output=True, text=True
    ).stdout.strip()
    if "arm64" not in architecture:
        raise SystemExit(f"expected an Apple Silicon engine, found: {architecture}")
    manifest = {
        "schemaVersion": 1,
        "product": info.get("CFBundleName", "HFLedger"),
        "appVersion": info.get("CFBundleShortVersionString"),
        "bundleIdentifier": info.get("CFBundleIdentifier"),
        "engineVersion": version_output,
        "architecture": architecture,
        "notarizationRequired": args.require_notarized,
        "artifacts": artifacts,
        "bundleFiles": bundle_files(app),
    }
    manifest_path = BUNDLE_ROOT / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"verified HFLedger {manifest['appVersion']} ({version_output})")
    print(f"release manifest: {manifest_path}")


if __name__ == "__main__":
    main()
