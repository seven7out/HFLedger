#!/usr/bin/env python3
"""Apply and verify an ad-hoc signature for this Mac's private dogfood build."""

from pathlib import Path
import subprocess


HOST_ROOT = Path(__file__).resolve().parents[1]
APP = HOST_ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "HFLedger.app"


def main() -> None:
    if not APP.is_dir():
        raise SystemExit(f"app bundle not found: {APP}")
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(APP)],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP)],
        check=True,
    )
    print(f"ad-hoc signed local app: {APP}")


if __name__ == "__main__":
    main()
