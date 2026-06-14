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


def build(use_upx=True, standalone=False):
    prompt_version()
    version = VERSION_FILE.read_text().strip()

    spec_file = PROJECT_ROOT / "AudioPatcher.spec"
    if standalone:
        final_dir = DIST_DIR / f"AudioPatcher {version} standalone"
    else:
        final_dir = DIST_DIR / f"AutoDub Suite {version}"

    build_tmp = DIST_DIR / "_build_temp_audiopatcher"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    print(f"\nBuilding AudioPatcher with PyInstaller...")
    cmd = [
        "pyinstaller",
        "--clean", "-y",
        "--distpath", str(build_tmp),
        "--workpath", str(BUILD_DIR),
    ]
    cmd.append(str(spec_file))

    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print("\nBuild failed!", file=sys.stderr)
        raise RuntimeError("PyInstaller build failed")
    print("Build succeeded!")

    # Move to final directory
    src = build_tmp / "AudioPatcher"
    if standalone:
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.move(str(src), str(final_dir))
    else:
        if not final_dir.exists():
            final_dir.mkdir(parents=True)
        for item in src.iterdir():
            dst = final_dir / item.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(item), str(final_dir))

    return final_dir, version


def clean_build():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"Cleaned build cache: {BUILD_DIR}")
    tmp = DIST_DIR / "_build_temp_audiopatcher"
    if tmp.exists():
        shutil.rmtree(tmp)


def print_summary(elapsed, output_dir: Path, version: str):
    exe = output_dir / "AudioPatcher.exe"
    internal = output_dir / "_internal_audiopatcher"
    if not exe.exists():
        return

    exe_size = exe.stat().st_size
    internal_size = 0
    if internal.exists():
        internal_size = sum(f.stat().st_size for f in internal.rglob('*') if f.is_file())
    total = exe_size + internal_size

    mins, secs = divmod(int(elapsed), 60)
    print(f"\n{'='*50}")
    print(f"Build complete!  ({mins}m {secs}s)")
    print(f"{'='*50}")
    print(f"Output: {output_dir}")
    print(f"  AudioPatcher.exe: {exe_size / 1e6:.1f} MB")
    print(f"  _internal_audiopatcher: {internal_size / 1e6:.1f} MB")
    print(f"  Total:            {total / 1e6:.1f} MB")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build AudioPatcher")
    parser.add_argument('--standalone', action='store_true',
                        help='Build standalone folder instead of into AutoDub Suite')
    args = parser.parse_args()

    _start = time.time()
    use_upx = prompt_upx()
    output_dir, version = build(use_upx, standalone=args.standalone)
    clean_build()
    print_summary(time.time() - _start, output_dir, version)


if __name__ == "__main__":
    main()
