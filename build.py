"""Build on the target OS: python -m pip install -e '.[dev]' && python build.py"""
import argparse
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="Include optional Playwright Python module; install Chromium separately")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir", "--name", "DocHarbor", "--collect-submodules", "docharbor", "--hidden-import", "multiprocessing"]
    if not args.browser:
        command.extend(["--exclude-module", "playwright"])
    command.append(str(root / "main.py"))
    subprocess.run(command, cwd=root, check=True)
    print(f"Fertig: {root / 'dist' / 'DocHarbor'}")
