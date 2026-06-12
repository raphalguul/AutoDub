import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import urllib.request
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
VERSION_FILE = PROJECT_ROOT / "VERSION"
CUDA_STAGING_DIR = PROJECT_ROOT / "dist" / "CUDA Package"

WHISPER_CPP_REPO = "https://github.com/ggml-org/whisper.cpp"
WHISPER_CPP_API = "https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest"


def find_whisper_cpp_asset(prefix):
    """Fetch latest release JSON and return download URL of first asset
    whose name starts with *prefix*, or None on failure."""
    try:
        req = urllib.request.Request(WHISPER_CPP_API)
        req.add_header('Accept', 'application/json')
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            if name.startswith(prefix):
                return asset.get('browser_download_url')
        return None
    except Exception as e:
        print(f"Warning: Could not fetch release assets: {e}")
        return None


def download_whisper_cpp_cuda():
    """Download CUDA-enabled whisper.cpp binary and CUDA DLLs."""
    cuda_bin_dir = CUDA_STAGING_DIR / "whisper_cuda"
    cuda_bin_dir.mkdir(parents=True, exist_ok=True)

    whisper_exe = cuda_bin_dir / "whisper.cpp.exe"
    if whisper_exe.exists():
        print(f"CUDA whisper.cpp already exists at {whisper_exe}")
        return cuda_bin_dir

    zip_path = cuda_bin_dir / "whisper-cuda.zip"

    print("\nLooking up CUDA whisper.cpp release asset...")
    zip_url = find_whisper_cpp_asset("whisper-cublas-")
    if not zip_url:
        print("CUDA whisper.cpp asset not found in latest release.")
        return None
    print(f"Downloading from {zip_url}...")
    try:
        urllib.request.urlretrieve(zip_url, zip_path)
    except Exception as e:
        print(f"Download failed: {e}")
        return None

    print("Extracting...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(cuda_bin_dir)

    zip_path.unlink()

    if whisper_exe.exists():
        print(f"CUDA whisper.cpp ready at {whisper_exe}")
    else:
        exes = list(cuda_bin_dir.rglob("whisper*.exe"))
        if exes:
            shutil.copy2(exes[0], whisper_exe)
            print(f"Copied {exes[0]} to {whisper_exe}")
        else:
            print("Warning: No whisper.cpp exe found in CUDA zip")
            return None

    return cuda_bin_dir


def find_cuda_dlls_from_zip():
    """Find CUDA DLLs from existing cuda_dlls.zip or extracted directory."""
    zip_file = CUDA_STAGING_DIR / "cuda_dlls.zip"
    if zip_file.exists():
        print(f"\nExtracting CUDA DLLs from {zip_file}...")
        extract_dir = CUDA_STAGING_DIR / "cuda_dlls_extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        with zipfile.ZipFile(zip_file, 'r') as z:
            z.extractall(extract_dir)
        return extract_dir

    extracted_dir = CUDA_STAGING_DIR / "cuda_dlls"
    if extracted_dir.exists():
        print(f"Using CUDA DLLs from {extracted_dir}")
        return extracted_dir

    return None


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


def download_cpu_whisper_cpp(target_dir: Path):
    """Download CPU whisper.cpp build from GitHub releases."""
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


def build(use_upx=True, cuda=False):
    prompt_version()

    spec_file = PROJECT_ROOT / "AutoDub_CUDA.spec" if cuda else PROJECT_ROOT / "AutoDub.spec"
    output_dir = DIST_DIR / ("AutoDub CUDA" if cuda else "AutoDub")
    whisper_staging = DIST_DIR / "_whisper_staging"

    env = os.environ.copy()
    if not use_upx:
        env['AUTODUB_NOUPX'] = '1'

    if cuda:
        if not download_whisper_cpp_cuda():
            print("ERROR: CUDA whisper.cpp download failed")
            raise SystemExit(1)
        cuda_whisper_dir = CUDA_STAGING_DIR / "whisper_cuda"
        if cuda_whisper_dir.exists():
            env['AUTODUB_WHISPER_CPP_DIR'] = str(cuda_whisper_dir)
        cuda_dll_dir = find_cuda_dlls_from_zip()
        if cuda_dll_dir:
            env['AUTODUB_CUDA_DLL_DIR'] = str(cuda_dll_dir)
        else:
            print("Warning: No CUDA DLLs found.")
    else:
        if whisper_staging.exists():
            shutil.rmtree(whisper_staging)
        download_cpu_whisper_cpp(whisper_staging)
        env['AUTODUB_WHISPER_CPP_DIR'] = str(whisper_staging)

    if output_dir.exists():
        print(f"Cleaning previous build: {output_dir}")
        shutil.rmtree(output_dir)

    print(f"\nBuilding AutoDub {'CUDA' if cuda else 'CPU'} with PyInstaller...")
    cmd = [
        "pyinstaller",
        "--clean", "-y",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
    ]
    cmd.append(str(spec_file))

    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        print("\nBuild failed!", file=sys.stderr)
        raise RuntimeError("PyInstaller build failed")
    print("Build succeeded!")


def copy_cuda_output():
    """Copy CUDA build output to CUDA-named directory."""
    src = DIST_DIR / "AutoDub"
    dst = DIST_DIR / "AutoDub CUDA"
    if src.exists() and src != dst:
        if dst.exists():
            shutil.rmtree(dst)
        print(f"\nCopying to {dst}...")
        shutil.copytree(src, dst)
        print("Done.")


def clean_build():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"Cleaned build cache: {BUILD_DIR}")


def print_summary(elapsed, cuda=False):
    out = DIST_DIR / ("AutoDub CUDA" if cuda else "AutoDub")
    exe = out / "AutoDub.exe"
    internal = out / "_internal"
    if not (exe.exists() and internal.exists()):
        return

    exe_size = exe.stat().st_size
    internal_size = sum(f.stat().st_size for f in internal.rglob('*') if f.is_file())
    total = exe_size + internal_size

    mins, secs = divmod(int(elapsed), 60)
    print(f"\n{'='*50}")
    print(f"{'CUDA' if cuda else 'CPU'} build complete!  ({mins}m {secs}s)")
    print(f"{'='*50}")
    print(f"Output: {out}")
    print(f"  AutoDub.exe: {exe_size / 1e6:.1f} MB")
    print(f"  _internal:   {internal_size / 1e6:.1f} MB")
    print(f"  Total:       {total / 1e6:.1f} MB")
    if cuda:
        print(f"  CUDA:        Included")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build AutoDub")
    parser.add_argument('--cuda', action='store_true', help='Build CUDA version')
    args = parser.parse_args()

    _start = time.time()
    use_upx = prompt_upx()
    build(use_upx, cuda=args.cuda)
    if args.cuda:
        copy_cuda_output()
    clean_build()
    print_summary(time.time() - _start, cuda=args.cuda)


if __name__ == "__main__":
    main()
