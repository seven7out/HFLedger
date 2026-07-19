#!/usr/bin/env python3
"""Build Tauri with local source paths remapped out of release binaries."""

from pathlib import Path
import os
import subprocess
import sys


HOST_ROOT = Path(__file__).resolve().parents[1]
TAURI = HOST_ROOT / "node_modules" / ".bin" / "tauri"


def main() -> None:
    if not TAURI.exists():
        raise SystemExit("Tauri CLI is missing; run npm ci first")
    environment = os.environ.copy()
    rustup = "/opt/homebrew/opt/rustup/bin"
    environment["PATH"] = rustup + os.pathsep + environment.get("PATH", "")
    remap = f"--remap-path-prefix={Path.home()}=~"
    existing = environment.get("RUSTFLAGS", "").strip()
    environment["RUSTFLAGS"] = f"{existing} {remap}".strip()
    subprocess.run(
        [str(TAURI), "build", *sys.argv[1:]],
        cwd=HOST_ROOT,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
