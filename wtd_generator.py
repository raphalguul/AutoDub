import os
import sys
import subprocess
import re
import json
import tempfile
import shutil
import urllib.request
import zipfile
import threading
import time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Callable
from wtd_utils import _join_words

_cuda_download_lock = threading.Lock()

@dataclass
class SubtitleLine:
    index: int
    start_time: str
    end_time: str
    text: str
    is_dub: bool = False
    dub_gender: Optional[str] = None
    words: Optional[List[Tuple[str, float, float]]] = None
    no_speech_prob: Optional[float] = None
    from_split: bool = False

class WTDDubGenerator:
    def __init__(self):
        self.video_file = None
        self.audio_file = None
        self.subtitles: List[SubtitleLine] = []
        self.dub_requests: Dict[int, Tuple[str, str]] = {}
        self.merged_dub_range: Optional[Tuple[int, int]] = None
        self.speaker_index = 0
        self.video_duration: Optional[float] = None
        self.dub_extend_to_end: bool = False
        self._last_error: Optional[str] = None
        self._current_model_name: Optional[str] = None
        self.log_fn = print

    def set_video(self, file_path: str) -> bool:
        if not os.path.exists(file_path):
            return False
        self.video_file = file_path
        return True

    def _ffmpeg_path(self) -> str:
        return self._ensure_ffmpeg()

    def _ffmpeg_staging_dir(self) -> str:
        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        d = os.path.join(base, '_ffmpeg_bin')
        os.makedirs(d, exist_ok=True)
        return d

    def _resolve_ffmpeg_url(self) -> str:
        return "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

    def _download_ffmpeg(self, target_dir: str, progress_callback: Optional[Callable[[int], None]] = None) -> Optional[str]:
        os.makedirs(target_dir, exist_ok=True)
        url = self._resolve_ffmpeg_url()
        tmp = tempfile.mkdtemp(prefix="ffmpeg_dl_")
        zip_path = os.path.join(tmp, "ffmpeg.zip")

        def reporthook(blocks: int, block_size: int, total: int):
            if total > 0 and progress_callback:
                pct = min(100, blocks * block_size * 100 // total)
                progress_callback(pct)

        try:
            msg = f"Downloading GPL-licensed ffmpeg from {url}..."
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            urllib.request.urlretrieve(url, zip_path, reporthook=reporthook)
            print("Extracting...")
            with zipfile.ZipFile(zip_path, 'r') as z:
                for name in z.namelist():
                    if name.endswith('ffmpeg.exe'):
                        z.extract(name, tmp)
                        src = os.path.join(tmp, name)
                        dst = os.path.join(target_dir, 'ffmpeg.exe')
                        shutil.move(src, dst)
                        break
            os.unlink(zip_path)

            exe_dst = os.path.join(target_dir, 'ffmpeg.exe')
            if os.path.exists(exe_dst):
                msg = f"ffmpeg downloaded to {exe_dst}"
                print(msg)
                if hasattr(self, 'log_fn') and self.log_fn:
                    self.log_fn(msg)
                if progress_callback:
                    progress_callback(100)
                return exe_dst

            print("Warning: ffmpeg.exe not found in downloaded zip.")
            return None
        except Exception as e:
            msg = f"ffmpeg auto-download failed: {e}"
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            print("Install ffmpeg manually from https://ffmpeg.org/download.html")
            return None
        finally:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

    def _ensure_ffmpeg(self) -> str:
        if shutil.which('ffmpeg'):
            return 'ffmpeg'

        exe = getattr(sys, 'frozen', False)
        if exe:
            local = os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe')
            if os.path.exists(local):
                return local

        staging = self._ffmpeg_staging_dir()
        local_staged = os.path.join(staging, 'ffmpeg.exe')
        if os.path.exists(local_staged):
            return local_staged

        msg = "ffmpeg not found. Attempting auto-download..."
        print(msg)
        if hasattr(self, 'log_fn') and self.log_fn:
            self.log_fn(msg)
        downloaded = self._download_ffmpeg(staging)
        if downloaded:
            return downloaded

        return 'ffmpeg'

    def extract_audio(self) -> bool:
        if not self.video_file:
            self._last_error = "No video file set"
            return False
        try:
            self.audio_file = os.path.join(tempfile.gettempdir(), "autodub_temp_audio.wav")
            af = 'highpass=f=80,dynaudnorm'
            cmd = [
                self._ffmpeg_path(),
                '-i', self.video_file,
                '-af', af,
                '-ar', '16000',
                '-ac', '1',
                '-q:a', '9',
                '-y',
                self.audio_file
            ]
            print(f"[extract_audio] Running: {' '.join(cmd)}")
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            r = subprocess.run(cmd, check=True, capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=si)
            out_size = os.path.getsize(self.audio_file) if os.path.exists(self.audio_file) else 0
            print(f"[extract_audio] OK — {self.audio_file} is {out_size} bytes")
            self._last_error = None
            return True
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
            print(f"[extract_audio] ffmpeg failed (code {e.returncode}): {err[:500]}")
            self._last_error = f"ffmpeg exited with code {e.returncode}: {err[:300]}"
            return False
        except subprocess.TimeoutExpired:
            print(f"[extract_audio] ffmpeg timed out after 120s")
            self._last_error = "ffmpeg timed out after 120 seconds"
            return False
        except Exception as e:
            print(f"[extract_audio] unexpected error: {e}")
            self._last_error = str(e)
            return False

    def transcribe_with_whisper(self, model_name: str = "base", use_cuda: bool = True, suppress_silence: bool = True, cuda_fallback_cpu: bool = False) -> bool:
        if not self.audio_file:
            self._last_error = "No audio file"
            return False
        
        return self._transcribe_with_whisper_cpp(model_name, use_cuda, suppress_silence, cuda_fallback_cpu)

    def _transcribe_with_whisper_cpp(self, model_name: str, use_cuda: bool = False, suppress_silence: bool = True, cuda_fallback_cpu: bool = False) -> bool:
        """Transcribe using whisper.cpp CLI via subprocess."""
        if not self.audio_file:
            self._last_error = "No audio file"
            return False
        
        try:
            # Find whisper.cpp binary (CUDA or CPU)
            whisper_cpp_exe = self._find_whisper_cpp(cuda=use_cuda)
            if not whisper_cpp_exe:
                if use_cuda:
                    self.log_fn("CUDA binary not available, falling back to CPU")
                    whisper_cpp_exe = self._find_whisper_cpp(cuda=False)
                    use_cuda = False
                if not whisper_cpp_exe:
                    self._last_error = "whisper.cpp not found"
                    return False
            
            model_file = self._resolve_model_file(model_name)
            
            # Find model file (check local models dir, then default locations)
            model_path = self._find_model_file(model_name, model_file)
            if not model_path:
                self._last_error = f"Model file not found: {model_file}"
                return False
            
            # Create temp output file
            with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp_out:
                json_out = tmp_out.name
            
            try:
                # Build whisper.cpp command
                cmd = [
                    whisper_cpp_exe,
                    '-m', model_path,
                    '-f', self.audio_file,
                    '-ojf',  # Full JSON output (includes token timestamps)
                    '-of', json_out.replace('.json', ''),
                ]

                # Add suppress non-speech if requested
                if suppress_silence:
                    cmd.append('--suppress-nst')
                
                # Disable GPU if CUDA not requested (for CUDA-enabled binaries)
                if not use_cuda:
                    cmd.append('-ng')
                
                self.log_fn(f"[whisper.cpp] Running: {' '.join(cmd)}")

                # Set env so binary's DLLs are found
                env = os.environ.copy()
                whisper_dir = os.path.dirname(whisper_cpp_exe)
                env['PATH'] = whisper_dir + os.pathsep + env.get('PATH', '')
                
                # Run whisper.cpp
                r = subprocess.run(cmd, capture_output=True, timeout=300, text=True, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                
                if r.returncode != 0:
                    if use_cuda and cuda_fallback_cpu:
                        self.log_fn(f"[whisper.cpp] CUDA failed, falling back to CPU binary: {r.stderr[:200]}")
                        cpu_exe = self._find_whisper_cpp(cuda=False)
                        if cpu_exe:
                            whisper_dir = os.path.dirname(cpu_exe)
                            env['PATH'] = whisper_dir + os.pathsep + env.get('PATH', '')
                            cmd[0] = cpu_exe
                            if '-ng' not in cmd:
                                cmd.append('-ng')
                            self.log_fn(f"[whisper.cpp] Retrying with CPU binary: {' '.join(cmd)}")
                            r = subprocess.run(cmd, capture_output=True, timeout=300, text=True, env=env,
                                               creationflags=subprocess.CREATE_NO_WINDOW)
                            if r.returncode == 0:
                                pass
                            else:
                                self._last_error = f"whisper.cpp failed (fallback): {r.stderr[:500]}"
                                return False
                        else:
                            self._last_error = f"whisper.cpp failed: {r.stderr[:500]}"
                            return False
                    else:
                        self._last_error = f"whisper.cpp failed: {r.stderr[:500]}"
                        return False
                
                # Verify output file exists
                if not os.path.exists(json_out):
                    self._last_error = f"whisper.cpp failed (exit 0, no output). stderr: {r.stderr[:300]}"
                    return False

                # Parse JSON output
                with open(json_out, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self.subtitles = self._parse_whisper_cpp_output(data, True)
                self._last_error = None
                return True
                
            finally:
                # Cleanup temp files
                for ext in ['', '.json']:
                    try:
                        os.unlink(json_out.replace('.json', ext))
                    except:
                        pass
                        
        except Exception as e:
            self._last_error = f"whisper.cpp: {e}"
            return False
        
        return False



    def _find_whisper_cpp(self, cuda: bool = False) -> Optional[str]:
        """Find whisper.cpp executable. Download if not found."""
        if cuda:
            return self._find_whisper_cpp_cuda()

        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS

        candidates = [
            os.path.join(base, 'whisper.cpp.exe'),
            os.path.join(base, 'whisper-cli.exe'),
            os.path.join(os.path.dirname(sys.executable), 'whisper.cpp.exe'),
            os.path.join(os.path.dirname(sys.executable), 'whisper-cli.exe'),
            'whisper.cpp.exe',
            'whisper-cli.exe',
            os.path.join(self._cpu_staging_dir(), 'whisper.cpp.exe'),
        ]

        for exe in candidates:
            if exe and os.path.exists(exe):
                return exe

        msg = "whisper.cpp not found. Attempting auto-download..."
        print(msg)
        if hasattr(self, 'log_fn') and self.log_fn:
            self.log_fn(msg)
        return self._download_whisper_cpp(self._cpu_staging_dir())

    def _cpu_staging_dir(self) -> str:
        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        d = os.path.join(base, '_whisper_bin')
        os.makedirs(d, exist_ok=True)
        return d

    def _cuda_staging_dir(self) -> str:
        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        d = os.path.join(base, '_cuda_bin')
        os.makedirs(d, exist_ok=True)
        return d

    def _find_whisper_cpp_cuda(self) -> Optional[str]:
        """Find CUDA whisper.cpp binary. Download CUDA build if not found."""
        staging = self._cuda_staging_dir()
        exe_candidates = [
            os.path.join(staging, 'whisper.cpp.cuda.exe'),
            os.path.join(staging, 'whisper-cli.exe'),
        ]
        for exe in exe_candidates:
            if exe and os.path.exists(exe):
                if self._verify_cuda_dlls(exe):
                    return exe
                else:
                    self.log_fn(f"CUDA binary found but missing required DLLs: {exe}")
                    return None

        with _cuda_download_lock:
            for exe in exe_candidates:
                if exe and os.path.exists(exe):
                    if self._verify_cuda_dlls(exe):
                        return exe
                    else:
                        self.log_fn(f"CUDA binary found but missing required DLLs: {exe}")
                        return None
            msg = "CUDA whisper.cpp not found. Attempting auto-download..."
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            return self._download_whisper_cpp_cuda(staging)

    def _verify_cuda_dlls(self, exe_path: str) -> bool:
        """Check if critical CUDA DLLs exist alongside the binary."""
        dll_dir = os.path.dirname(exe_path)
        # Check for cublas (11 or 12) and at least one other CUDA runtime DLL
        cublas_ok = any(
            f.lower().startswith('cublas') and f.lower().endswith('.dll')
            for f in os.listdir(dll_dir)
            if os.path.isfile(os.path.join(dll_dir, f))
        )
        cuda_runtime_ok = any(
            f.lower().startswith(('cufft', 'cusparse', 'cusolver', 'curand', 'nvrtc', 'cudart')) and f.lower().endswith('.dll')
            for f in os.listdir(dll_dir)
            if os.path.isfile(os.path.join(dll_dir, f))
        )
        if not cublas_ok:
            self.log_fn(f"CUDA DLL check failed: no cublas*.dll in {dll_dir}")
        if not cuda_runtime_ok:
            self.log_fn(f"CUDA DLL check failed: no cufft/cusparse/cusolver/curand/nvrtc/cudart*.dll in {dll_dir}")
        return cublas_ok and cuda_runtime_ok

    def _resolve_download_url(self, prefix: str) -> Optional[str]:
        """Fetch latest release asset URL starting with *prefix* from GitHub API."""
        try:
            req = urllib.request.Request("https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest")
            req.add_header('Accept', 'application/json')
            with urllib.request.urlopen(req, timeout=15) as r:
                assets = json.loads(r.read().decode()).get('assets', [])
                # For CUDA, prefer 12.x over 11.x
                if prefix == 'whisper-cublas-':
                    for asset in assets:
                        name = asset.get('name', '')
                        if name.startswith(prefix) and '12.' in name:
                            return asset.get('browser_download_url')
                for asset in assets:
                    if asset.get('name', '').startswith(prefix):
                        return asset.get('browser_download_url')
        except:
            pass
        return None

    def _download_whisper_cpp_cuda(self, target_dir: str, progress_callback: Optional[Callable[[int], None]] = None) -> Optional[str]:
        """Download CUDA whisper.cpp build from GitHub releases."""
        os.makedirs(target_dir, exist_ok=True)
        url = self._resolve_download_url('whisper-cublas-')
        if not url:
            url = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-cublas-12.4.0-bin-x64.zip"
        tmp = tempfile.mkdtemp(prefix="whisper_cuda_dl_")
        zip_path = os.path.join(tmp, "whisper-cuda.zip")

        def reporthook(blocks: int, block_size: int, total: int):
            if total > 0 and progress_callback:
                pct = min(100, blocks * block_size * 100 // total)
                progress_callback(pct)

        try:
            msg = f"Downloading CUDA whisper.cpp from {url}..."
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            urllib.request.urlretrieve(url, zip_path, reporthook=reporthook)
            print("Extracting...")
            with zipfile.ZipFile(zip_path, 'r') as z:
                all_names = z.namelist()
                print(f"Zip contains {len(all_names)} entries:")
                for name in all_names:
                    print(f"  {name}")
                z.extractall(tmp)
            os.unlink(zip_path)

            moved = 0
            for root, dirs, files in os.walk(tmp):
                for f in files:
                    f_lower = f.lower()
                    if f_lower.endswith('.dll') or f_lower.endswith('.exe'):
                        src = os.path.join(root, f)
                        dst = os.path.join(target_dir, f)
                        print(f"  Moving {f} -> {dst}")
                        shutil.move(src, dst)
                        moved += 1
            print(f"Moved {moved} files to {target_dir}")

            exe_src = os.path.join(target_dir, 'whisper-cli.exe')
            exe_dst = os.path.join(target_dir, 'whisper.cpp.cuda.exe')
            if os.path.exists(exe_src):
                os.rename(exe_src, exe_dst)

            if os.path.exists(exe_dst):
                files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
                msg = f"CUDA whisper.cpp downloaded to {exe_dst} ({len(files)} files)"
                print(msg)
                if hasattr(self, 'log_fn') and self.log_fn:
                    self.log_fn(msg)
                if progress_callback:
                    progress_callback(100)
                return exe_dst

            print("Warning: No CUDA whisper executable found in downloaded zip.")
            return None
        except Exception as e:
            msg = f"CUDA auto-download failed: {e}"
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            print("Download manually from https://github.com/ggml-org/whisper.cpp/releases")
            return None
        finally:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

    def _download_whisper_cpp(self, target_dir: str, progress_callback: Optional[Callable[[int], None]] = None) -> Optional[str]:
        """Download CPU whisper.cpp build from GitHub releases."""
        os.makedirs(target_dir, exist_ok=True)
        url = self._resolve_download_url('whisper-bin-x64')
        if not url:
            url = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip"
        tmp = tempfile.mkdtemp(prefix="whisper_dl_")
        zip_path = os.path.join(tmp, "whisper-bin.zip")

        def reporthook(blocks: int, block_size: int, total: int):
            if total > 0 and progress_callback:
                pct = min(100, blocks * block_size * 100 // total)
                progress_callback(pct)

        try:
            msg = f"Downloading whisper.cpp from {url}..."
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            urllib.request.urlretrieve(url, zip_path, reporthook=reporthook)
            print("Extracting...")
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(tmp)
            os.unlink(zip_path)

            needed = {'whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll', 'sdl2.dll'}
            for root, dirs, files in os.walk(tmp):
                for f in files:
                    if f.lower() in needed:
                        src = os.path.join(root, f)
                        dst = os.path.join(target_dir, 'whisper.cpp.exe' if f.lower() == 'whisper-cli.exe' else f)
                        shutil.move(src, dst)

            exe_dst = os.path.join(target_dir, 'whisper.cpp.exe')
            if os.path.exists(exe_dst):
                files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
                msg = f"whisper.cpp downloaded to {exe_dst} ({len(files)} files)"
                print(msg)
                if hasattr(self, 'log_fn') and self.log_fn:
                    self.log_fn(msg)
                if progress_callback:
                    progress_callback(100)
                return exe_dst

            print("Warning: No whisper executable found in downloaded zip.")
            return None
        except Exception as e:
            msg = f"Auto-download failed: {e}"
            print(msg)
            if hasattr(self, 'log_fn') and self.log_fn:
                self.log_fn(msg)
            print("Download manually from https://github.com/ggml-org/whisper.cpp/releases")
            return None
        finally:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)

    def _remove_binaries(self, staging_dir: str) -> tuple[int, list[str]]:
        """Remove all files in a staging directory. Returns (bytes_freed, files_removed)."""
        if not os.path.isdir(staging_dir):
            return 0, []
        files_removed = []
        total_bytes = 0
        for root, _, fnames in os.walk(staging_dir):
            for f in fnames:
                p = os.path.join(root, f)
                try:
                    sz = os.path.getsize(p)
                    os.remove(p)
                    files_removed.append(f)
                    total_bytes += sz
                except OSError:
                    pass
        try:
            os.rmdir(staging_dir)
        except OSError:
            pass
        return total_bytes, files_removed

    def remove_ffmpeg_binaries(self) -> tuple[int, list[str]]:
        return self._remove_binaries(self._ffmpeg_staging_dir())

    def remove_cpu_binaries(self) -> tuple[int, list[str]]:
        return self._remove_binaries(self._cpu_staging_dir())

    def remove_cuda_binaries(self) -> tuple[int, list[str]]:
        return self._remove_binaries(self._cuda_staging_dir())

    def _resolve_model_file(self, model_name: str) -> str:
        model_map = {
            'tiny': 'ggml-tiny-q5_1.bin',
            'tiny.en': 'ggml-tiny-q5_1.bin',
            'base': 'ggml-base-q5_1.bin',
            'base.en': 'ggml-base-q5_1.bin',
            'small': 'ggml-small-q5_1.bin',
            'small.en': 'ggml-small-q5_1.bin',
            'medium': 'ggml-medium-q5_0.bin',
            'medium.en': 'ggml-medium-q5_0.bin',
            'large': 'ggml-large-v3-q5_0.bin',
            'large-v1': 'ggml-large-v1-q5_0.bin',
            'large-v2': 'ggml-large-v2-q5_0.bin',
            'large-v3': 'ggml-large-v3-q5_0.bin',
        }
        return model_map.get(model_name, f'ggml-{model_name}.bin')

    def check_model(self, model_name: str) -> bool:
        model_file = self._resolve_model_file(model_name)
        path = self._find_model_file(model_name, model_file)
        if path:
            self.log_fn(f"Model {model_file} ready")
            return True
        return False

    def _find_model_file(self, model_name: str, model_file: str) -> Optional[str]:
        """Find whisper.cpp model file. Download from HuggingFace if not found."""
        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)

        models_dir = os.path.join(base, 'models')
        candidates = [
            os.path.join(models_dir, model_file),
            os.path.join(base, model_file),
            os.path.join(os.path.expanduser('~'), 'whisper.cpp', 'models', model_file),
            os.path.join('models', model_file),
        ]

        for path in candidates:
            if path and os.path.exists(path):
                return path

        # Auto-download from HuggingFace (official whisper.cpp model repo)
        os.makedirs(models_dir, exist_ok=True)
        dst = os.path.join(models_dir, model_file)
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{model_file}"

        model_sizes = {
            'ggml-tiny-q5_1.bin': '32 MB',
            'ggml-base-q5_1.bin': '60 MB',
            'ggml-small-q5_1.bin': '190 MB',
            'ggml-medium-q5_0.bin': '539 MB',
            'ggml-large-v1-q5_0.bin': '1081 MB',
            'ggml-large-v2-q5_0.bin': '1081 MB',
            'ggml-large-v3-q5_0.bin': '1081 MB',
            'ggml-tiny.en-q5_1.bin': '32 MB',
            'ggml-base.en-q5_1.bin': '60 MB',
            'ggml-small.en-q5_1.bin': '190 MB',
            'ggml-medium.en-q5_0.bin': '539 MB',
        }
        size_str = model_sizes.get(model_file, '?')

        print(f"Model {model_file} ({size_str}) not found locally.")
        self.log_fn(f"Downloading {model_file} ({size_str}) from HuggingFace (ggerganov/whisper.cpp)...")

        delays = [5, 10, 20]  # exponential backoff
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Connection': 'keep-alive',
                })
                with urllib.request.urlopen(req, timeout=300) as resp:
                    total = int(resp.headers.get('Content-Length', 0))
                    downloaded = 0
                    last_reported = 0
                    chunk_size = 8192
                    with open(dst, 'wb') as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                pct = downloaded * 100 // total
                                if pct >= last_reported + 10:
                                    self.log_fn(f"  Download: {pct}%")
                                    last_reported = pct
                print(f"Model saved to {dst}")
                self.log_fn(f"Model {model_file} downloaded")
                return dst
            except Exception as e:
                print(f"Download attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    delay = delays[attempt]
                    print(f"Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    self.log_fn(f"Model download failed after 3 attempts: {e}")
                    print(f"Download manually from: {url}")
                    return None
        return None

    def _parse_whisper_cpp_output(self, data: dict, use_words: bool) -> List[SubtitleLine]:
        """Parse whisper.cpp JSON output into SubtitleLine objects."""
        subtitles = []
        index = 1

        for segment in data.get('transcription', []):
            text = re.sub(r'^-\s*', '', segment.get('text', '').strip())
            words = []

            offsets = segment.get('offsets', {})
            start_seconds = offsets.get('from', 0) / 1000.0
            end_seconds = offsets.get('to', 0) / 1000.0

            if use_words and 'tokens' in segment:
                for t in segment['tokens']:
                    wt = t.get('text', '').strip()
                    if not wt or wt.startswith('[') or wt in ('-', '–'):
                        continue
                    offsets = t.get('offsets', {})
                    ws = offsets.get('from', 0) / 1000.0
                    we = offsets.get('to', 0) / 1000.0
                    words.append((wt, ws, we))

            if use_words and words:
                start_seconds = words[0][1]
                end_seconds = words[-1][2]

            self.log_fn(f"[timing] line {index}: seg=({start_seconds:.2f}-{end_seconds:.2f})  \"{text[:50]}\"")

            start = self._format_timestamp(start_seconds)
            end = self._format_timestamp(end_seconds)
            subtitle = SubtitleLine(
                index=index,
                start_time=start,
                end_time=end,
                text=text,
                words=words or None,
                no_speech_prob=None
            )
            subtitles.append(subtitle)
            index += 1

        return subtitles

    def _format_timestamp(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        int_secs = int(secs)
        milliseconds = int(round((secs - int_secs) * 1000))
        if milliseconds == 1000:
            milliseconds = 0
            int_secs += 1
            if int_secs == 60:
                int_secs = 0
                minutes += 1
                if minutes == 60:
                    minutes = 0
                    hours += 1
        return f"{hours:02d}:{minutes:02d}:{int_secs:02d},{milliseconds:03d}"

    def add_dub_request(self, line_number: int, speaker: str, gender: str):
        self.dub_requests[line_number] = (speaker, gender)

    def remove_dub_request(self, line_number: int):
        if line_number in self.dub_requests:
            del self.dub_requests[line_number]

    def _parse_timestamp(self, ts: str) -> float:
        m = re.match(r'(\d+):(\d+):(\d+),(\d+)', ts)
        h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        return h * 3600 + mi * 60 + s + ms / 1000

    def _shift_timestamp(self, ts: str, offset: float, clamp_before: str = None, clamp_after: str = None) -> str:
        total = self._parse_timestamp(ts) + offset
        if total < 0:
            total = 0
        if clamp_before is not None:
            total = max(total, self._parse_timestamp(clamp_before))
        if clamp_after is not None:
            total = min(total, self._parse_timestamp(clamp_after))
        return self._format_timestamp(total)

    def generate_srt(self, speaker_map: Dict[int, str] = None) -> str:
        srt_content = []
        output_index = 1
        dub_pad = 0.1
        dub_start = dub_end = None
        dub_speaker = dub_gender = None
        if self.merged_dub_range:
            ds, de = self.merged_dub_range
            if ds in self.dub_requests:
                dub_start, dub_end = ds, de
                dub_speaker, dub_gender = self.dub_requests[dub_start]
        for subtitle in self.subtitles:
            if dub_start is not None and dub_start < subtitle.index <= dub_end:
                continue
            if dub_start is not None and subtitle.index == dub_start:
                end_time = subtitle.end_time
                for s in self.subtitles:
                    if s.index == dub_end:
                        end_time = s.end_time
                        break
                prev_end = None
                for s in self.subtitles:
                    if s.index == dub_start - 1:
                        prev_end = s.end_time
                        break
                next_start = None
                for s in self.subtitles:
                    if s.index == dub_end + 1:
                        next_start = s.start_time
                        break
                if dub_speaker:
                    sp_start = prev_end if prev_end else subtitle.start_time
                    sp_end = self._shift_timestamp(sp_start, 0.001)
                    srt_content.append(
                        f"{output_index}\n{sp_start} --> {sp_end}\n{dub_speaker}:\n"
                    )
                    output_index += 1
                text = f"[{dub_gender}_dub]"
                dub_end_str = self._shift_timestamp(end_time, dub_pad, clamp_after=next_start)
                if self.video_duration:
                    end_sec = self._parse_timestamp(end_time) + dub_pad
                    if next_start:
                        end_sec = min(end_sec, self._parse_timestamp(next_start))
                    if self.video_duration - end_sec <= 0.5:
                        dub_end_str = self._format_timestamp(self.video_duration)
                    if self.dub_extend_to_end:
                        dub_end_str = self._format_timestamp(self.video_duration)
                srt_entry = (
                    f"{output_index}\n"
                    f"{self._shift_timestamp(subtitle.start_time, -dub_pad, clamp_before=prev_end)} --> {dub_end_str}\n"
                    f"{text}\n"
                )
            elif subtitle.index in self.dub_requests:
                speaker, gender = self.dub_requests[subtitle.index]
                prev_end = None
                for s in self.subtitles:
                    if s.index == subtitle.index - 1:
                        prev_end = s.end_time
                        break
                next_start = None
                for s in self.subtitles:
                    if s.index == subtitle.index + 1:
                        next_start = s.start_time
                        break
                if speaker:
                    sp_start = prev_end if prev_end else subtitle.start_time
                    sp_end = self._shift_timestamp(sp_start, 0.001)
                    srt_content.append(
                        f"{output_index}\n{sp_start} --> {sp_end}\n{speaker}:\n"
                    )
                    output_index += 1
                text = f"[{gender}_dub]"
                dub_end_str = self._shift_timestamp(subtitle.end_time, dub_pad, clamp_after=next_start)
                if self.video_duration:
                    end_sec = self._parse_timestamp(subtitle.end_time) + dub_pad
                    if next_start:
                        end_sec = min(end_sec, self._parse_timestamp(next_start))
                    if self.video_duration - end_sec <= 0.5:
                        dub_end_str = self._format_timestamp(self.video_duration)
                    if self.dub_extend_to_end:
                        dub_end_str = self._format_timestamp(self.video_duration)
                srt_entry = (
                    f"{output_index}\n"
                    f"{self._shift_timestamp(subtitle.start_time, -dub_pad, clamp_before=prev_end)} --> {dub_end_str}\n"
                    f"{text}\n"
                )
            else:
                speaker = (speaker_map or {}).get(subtitle.index, "")
                if speaker:
                    text = f"{speaker}: {subtitle.text}"
                else:
                    text = subtitle.text
                srt_entry = (
                    f"{output_index}\n"
                    f"{subtitle.start_time} --> {subtitle.end_time}\n"
                    f"{text}\n"
                )
            srt_content.append(srt_entry)
            output_index += 1
        return "\n".join(srt_content)

    def split_line(self, line_index: int, split_after_indices: List[int]) -> Optional[int]:
        subs = self.subtitles
        if line_index < 0 or line_index >= len(subs):
            return None
        sub = subs[line_index]
        if not sub.words or len(sub.words) < 2:
            return None
        split_set = set(split_after_indices)
        if not split_set:
            return None
        new_lines = []
        current_words = []
        for wi, (wt, ws, we) in enumerate(sub.words):
            current_words.append((wt, ws, we))
            if wi in split_set:
                if current_words:
                    seg_text = _join_words(current_words)
                    new_lines.append(SubtitleLine(
                        index=0,
                        start_time=self._format_timestamp(current_words[0][1]),
                        end_time=self._format_timestamp(current_words[-1][2]),
                        text=seg_text,
                        words=current_words,
                        from_split=True
                    ))
                current_words = []
        if current_words:
            seg_text = _join_words(current_words)
            new_lines.append(SubtitleLine(
                index=0,
                start_time=self._format_timestamp(current_words[0][1]),
                end_time=self._format_timestamp(current_words[-1][2]),
                text=seg_text,
                words=current_words,
                from_split=True
            ))
        if len(new_lines) <= 1:
            return None
        self.subtitles = subs[:line_index] + new_lines + subs[line_index + 1:]
        for i, s in enumerate(self.subtitles):
            s.index = i + 1
        return len(new_lines)

    def cleanup(self):
        if self.audio_file and os.path.exists(self.audio_file):
            os.remove(self.audio_file)
