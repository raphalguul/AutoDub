import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import urllib.request
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


def run_pyinstaller(spec_file: Path, build_tmp: Path, env: dict):
    cmd = [
        "pyinstaller",
        "--clean", "-y",
        "--distpath", str(build_tmp),
        "--workpath", str(BUILD_DIR),
        str(spec_file),
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print(f"\nBuild failed: {spec_file.name}", file=sys.stderr)
        raise RuntimeError(f"PyInstaller build failed for {spec_file.stem}")
    print("Build succeeded!")


def merge_output(src_name: str, final_dir: Path):
    """Move PyInstaller output into final_dir. Handles both one-folder
    (subdirectory) and one-file (bare exe) output layouts."""
    tmp = DIST_DIR / "_build_temp"
    src_dir = tmp / src_name
    src_exe = tmp / f"{src_name}.exe"
    if not final_dir.exists():
        final_dir.mkdir(parents=True)
    if src_dir.exists():
        for item in src_dir.iterdir():
            dst = final_dir / item.name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(item), str(final_dir))
    elif src_exe.exists():
        dst = final_dir / f"{src_name}.exe"
        if dst.exists():
            dst.unlink()
        shutil.move(str(src_exe), str(dst))


def clean_build_tmp():
    tmp = DIST_DIR / "_build_temp"
    if tmp.exists():
        shutil.rmtree(tmp)


def download_cpu_whisper_cpp(target_dir: Path):
    WHISPER_CPP_CPU_URL = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip"
    tmp = PROJECT_ROOT / "build" / "_whisper_dl"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp / "whisper-bin.zip"

    print(f"\nDownloading CPU whisper.cpp from {WHISPER_CPP_CPU_URL}...")
    try:
        urllib.request.urlretrieve(WHISPER_CPP_CPU_URL, zip_path)
    except Exception as e:
        print(f"Download failed: {e}")
        return False

    print("Extracting...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(tmp)
    zip_path.unlink()

    needed = {'whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll', 'sdl2.dll'}
    target_dir.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(tmp):
        for f in files:
            if f.lower() in needed:
                src = Path(root) / f
                dst_name = 'whisper.cpp.exe' if f.lower() == 'whisper-cli.exe' else f
                dst = target_dir / dst_name
                shutil.move(str(src), str(dst))
                print(f"  {dst_name}")

    if (target_dir / "whisper.cpp.exe").exists():
        print(f"CPU whisper.cpp ready in {target_dir}")
        return True
    return False


def build_autodub(version, use_upx, final_dir):
    build_tmp = DIST_DIR / "_build_temp"
    whisper_staging = DIST_DIR / "_whisper_staging"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if whisper_staging.exists():
        shutil.rmtree(whisper_staging)
    download_cpu_whisper_cpp(whisper_staging)
    env['AUTODUB_WHISPER_CPP_DIR'] = str(whisper_staging)

    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    print(f"\n--- AutoDub ---")
    run_pyinstaller(PROJECT_ROOT / "AutoDub.spec", build_tmp, env)
    merge_output("AutoDub", final_dir)

    exe = final_dir / "AutoDub.exe"
    internal = final_dir / "_internal"
    exe_size = exe.stat().st_size
    internal_size = sum(f.stat().st_size for f in internal.rglob('*') if f.is_file()) if internal.exists() else 0
    print(f"  AutoDub.exe: {exe_size / 1e6:.1f} MB | _internal: {internal_size / 1e6:.1f} MB")


def build_audiopatcher(version, use_upx, final_dir):
    build_tmp = DIST_DIR / "_build_temp"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    print(f"\n--- AudioPatcher ---")
    run_pyinstaller(PROJECT_ROOT / "AudioPatcher.spec", build_tmp, env)
    merge_output("AudioPatcher", final_dir)

    exe = final_dir / "AudioPatcher.exe"
    internal = final_dir / "_internal_audiopatcher"
    exe_size = exe.stat().st_size
    internal_size = sum(f.stat().st_size for f in internal.rglob('*') if f.is_file()) if internal.exists() else 0
    print(f"  AudioPatcher.exe: {exe_size / 1e6:.1f} MB | _internal_audiopatcher: {internal_size / 1e6:.1f} MB")


def build_genderfixer(version, use_upx, final_dir):
    build_tmp = DIST_DIR / "_build_temp"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    print(f"\n--- GenderFixer ---")
    run_pyinstaller(PROJECT_ROOT / "GenderFixer.spec", build_tmp, env)
    merge_output("GenderFixer", final_dir)

    exe = final_dir / "GenderFixer.exe"
    exe_size = exe.stat().st_size
    print(f"  GenderFixer.exe: {exe_size / 1e6:.1f} MB")


def build_wtdrenamer(version, use_upx, final_dir):
    build_tmp = DIST_DIR / "_build_temp"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if build_tmp.exists():
        shutil.rmtree(build_tmp)

    print(f"\n--- wtdRenamer ---")
    run_pyinstaller(PROJECT_ROOT / "wtdRenamer.spec", build_tmp, env)
    merge_output("wtdRenamer", final_dir)

    exe = final_dir / "wtdRenamer.exe"
    exe_size = exe.stat().st_size
    print(f"  wtdRenamer.exe: {exe_size / 1e6:.1f} MB")


def clean_build():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"Cleaned build cache: {BUILD_DIR}")
    clean_build_tmp()
    staging = DIST_DIR / "_whisper_staging"
    if staging.exists():
        shutil.rmtree(staging)


def print_summary(elapsed, final_dir: Path):
    files = []
    for name in ('AutoDub.exe', 'AudioPatcher.exe', 'GenderFixer.exe', 'wtdRenamer.exe'):
        exe = final_dir / name
        if exe.exists():
            files.append((name, exe.stat().st_size))
    if not files:
        return

    total_main = sum(s for _, s in files)
    internal_dirs = ['_internal', '_internal_audiopatcher']
    total_internal = 0
    for d in internal_dirs:
        p = final_dir / d
        if p.exists():
            total_internal += sum(f.stat().st_size for f in p.rglob('*') if f.is_file())

    mins, secs = divmod(int(elapsed), 60)
    print(f"\n{'='*50}")
    print(f"AutoDub Suite build complete!  ({mins}m {secs}s)")
    print(f"{'='*50}")
    print(f"Output: {final_dir}")
    for name, size in files:
        print(f"  {name}: {size / 1e6:.1f} MB")
    print(f"  Total exes: {total_main / 1e6:.1f} MB")
    print(f"  Internal deps: {total_internal / 1e6:.1f} MB")
    print(f"  Grand total: {(total_main + total_internal) / 1e6:.1f} MB")


def main():
    prompt_version()
    version = VERSION_FILE.read_text().strip()
    use_upx = prompt_upx()
    final_dir = DIST_DIR / f"AutoDub Suite {version}"

    _start = time.time()

    build_autodub(version, use_upx, final_dir)
    build_audiopatcher(version, use_upx, final_dir)
    build_genderfixer(version, use_upx, final_dir)
    build_wtdrenamer(version, use_upx, final_dir)

    clean_build()
    print_summary(time.time() - _start, final_dir)


if __name__ == "__main__":
    main()
