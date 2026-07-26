#!/usr/bin/env python3
"""Build a pinned, self-contained HFLedger engine for the Mac app."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import sysconfig
import venv


HOST_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = HOST_ROOT.parents[1]
VENV = HOST_ROOT / ".build-venv"
BUILD_ROOT = HOST_ROOT / ".engine-build"
REQUIREMENTS = HOST_ROOT / "requirements-build.txt"
ENTRYPOINT = REPO_ROOT / "cli" / "ledger"


def run(*args: object, cwd: Path = HOST_ROOT) -> None:
    subprocess.run([str(value) for value in args], cwd=cwd, check=True)


def ensure_venv() -> Path:
    python = VENV / "bin" / "python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    stamp = VENV / ".requirements"
    expected = REQUIREMENTS.read_text(encoding="utf-8")
    if not stamp.exists() or stamp.read_text(encoding="utf-8") != expected:
        run(python, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQUIREMENTS)
        stamp.write_text(expected, encoding="utf-8")
    return python


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("the macOS engine must be built on macOS")
    for source in (ENTRYPOINT, REPO_ROOT / "app" / "static", REPO_ROOT / "packs" / "templates"):
        if not source.exists():
            raise SystemExit(f"missing engine source: {source}")
    python = ensure_venv()
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    (BUILD_ROOT / "dist").mkdir(parents=True)
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
    subprocess.run(
        [
            str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "hfledger-engine", "--distpath", str(BUILD_ROOT / "dist"),
            "--workpath", str(BUILD_ROOT / "work"), "--specpath", str(BUILD_ROOT),
            "--paths", str(REPO_ROOT),
            "--add-data", f"{REPO_ROOT / 'app' / 'static'}:app/static",
            "--add-data", f"{REPO_ROOT / 'packs' / 'templates'}:packs/templates",
            str(ENTRYPOINT),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )
    executable = BUILD_ROOT / "dist" / "hfledger-engine" / "hfledger-engine"
    license_root = executable.parent / "THIRD_PARTY_LICENSES"
    license_root.mkdir()
    license_sources = {
        "HFLedger-MIT.txt": REPO_ROOT / "LICENSE",
        "Python-3.9.txt": Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        "PyInstaller.txt": VENV / "lib" / "python3.9" / "site-packages" /
            "pyinstaller-6.21.0.dist-info" / "licenses" / "COPYING.txt",
        "PyInstaller-hooks-contrib.txt": VENV / "lib" / "python3.9" / "site-packages" /
            "pyinstaller_hooks_contrib-2026.6.dist-info" / "licenses" / "LICENSE",
    }
    for name, source in license_sources.items():
        if not source.is_file():
            raise SystemExit(f"missing runtime license: {source}")
        shutil.copy2(source, license_root / name)
    run(executable, "--version")
    print(f"built self-contained engine at {executable.parent}")


if __name__ == "__main__":
    main()
