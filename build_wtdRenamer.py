import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_FILE = PROJECT_ROOT / "wtdRenamer.spec"
VERSION_FILE = PROJECT_ROOT / "VERSION"


def prompt_version():
    current = "0.0.0"
    if VERSION_FILE.exists():
        current = VERSION_FILE.read_text().strip()

    print(f"Current version: {current}")
    try:
        inp = input(f"Enter new version (or press Enter to keep '{current}'): ").strip()
    except EOFError:
        return

    if not inp:
        print(f"Keeping version: {current}")
        return

    while not re.match(r'^\d+\.\d+\.\d+$', inp):
        print(f"Invalid format. Expected XX.XX.XX (e.g. 1.0.3)")
        try:
            inp = input("Enter new version: ").strip()
        except EOFError:
            return

    VERSION_FILE.write_text(inp + "\n")
    print(f"Version set to: {inp}")


def prompt_upx():
    try:
        inp = input("\nCompress with UPX? (Y/n): ").strip().lower()
    except EOFError:
        return True
    return inp != 'n'


def build(use_upx=True):
    prompt_version()

    print("\nBuilding wtdRenamer with PyInstaller...")
    cmd = [
        "pyinstaller",
        "--clean", "-y",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
    ]
    cmd.append(str(SPEC_FILE))

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print("\nBuild failed!", file=sys.stderr)
        raise RuntimeError("PyInstaller build failed")
    print("Build succeeded!")


def clean_build():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"Cleaned build cache: {BUILD_DIR}")


def print_summary(elapsed):
    out = DIST_DIR / "wtdRenamer"
    exe = out / "wtdRenamer.exe"
    internal = out / "_internal"
    if not (exe.exists() and internal.exists()):
        return
    exe_size = exe.stat().st_size
    internal_size = sum(f.stat().st_size for f in internal.rglob('*') if f.is_file())
    total = exe_size + internal_size

    mins, secs = divmod(int(elapsed), 60)
    print(f"\n{'='*50}")
    print(f"wtdRenamer build complete!  ({mins}m {secs}s)")
    print(f"{'='*50}")
    print(f"Output: {out}")
    print(f"  wtdRenamer.exe: {exe_size / 1e6:.1f} MB")
    print(f"  _internal:   {internal_size / 1e6:.1f} MB")
    print(f"  Total:       {total / 1e6:.1f} MB")


if __name__ == "__main__":
    _start = time.time()
    use_upx = prompt_upx()
    build(use_upx)
    clean_build()
    print_summary(time.time() - _start)