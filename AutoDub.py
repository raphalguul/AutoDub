"""
AutoDub
Generate and Manage Subtitle Files for What the Dub!?
"""

import PySimpleGUI as sg
import os
import sys
import shutil
import subprocess
import threading
import tempfile
import time
import re
from typing import List, Dict, Optional, Callable
from datetime import timedelta

# Read version from VERSION file
__version__ = "0.0.0"
_version_path = os.path.join(os.path.dirname(__file__), "VERSION")
if getattr(sys, 'frozen', False):
    _version_path = os.path.join(sys._MEIPASS, "VERSION")
if os.path.isfile(_version_path):
    with open(_version_path) as f:
        __version__ = f.read().strip()

def _icon_path(name):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'Icons', name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Icons', name)

from wtd_generator import SubtitleLine, WTDDubGenerator
from wtd_utils import (
    load_config, save_config,
    _migrate_config, _DEFAULT_CONFIG,
    _config_dir,
    _popup, _popup_yes_no, _popup_yes_no_iterate, _popup_location, set_main_win,
    _init_logging, _close_logging, _log,
    _find_mpv, _resolve_mpv_path,
    _cleanup_old_logs, _add_periods_to_srt,
    _show_split_window,
)
from preview_srt import (
    parse_srt, serialize_srt, normalize_dub_markers,
    validate_single_dub
)




# Configure PySimpleGUI theme
sg.theme('DarkBlue3')

# Capture original stdout before _init_logging replaces it with _TeeStream
_original_stdout = sys.stdout

def _logv(label, start):
    if _original_stdout is None:
        return
    elapsed = time.perf_counter() - start
    _original_stdout.write(f"[{time.strftime('%H:%M:%S')}] [{elapsed*1000:7.1f}ms] {label}\n")

def main():
    config = load_config()
    log_path = config["log_path"] if "log_path" in config else "logs"
    _init_logging(log_path)
    generator = WTDDubGenerator()
    main_cfg = config["main"]
    gf_cfg = config["gender_fixer"]
    renamer_cfg = config["file_renamer"]
    original_video_path = None
    
    left_panel = [
        [sg.Text("Step 1: Select Video File", font=("Arial", 12, "bold"))],
        [
            sg.InputText(key="-VIDEO-", disabled=True, size=(30, 1)),
            sg.Button("Browse", key="-BROWSE-VIDEO-")
        ],
        [sg.Button("Generate subtitles", key="-TRANSCRIBE-", disabled=True), sg.Text("", key="-STATUS-", size=(25, 1), text_color="yellow")],
        [sg.Text("Transcription model:", font=("Arial", 9))],
        [sg.Radio("tiny", "-MODEL-", key="-MODEL-tiny-", default=main_cfg.get("default_model") == "tiny", font=("Arial", 9), enable_events=True),
         sg.Radio("base", "-MODEL-", key="-MODEL-base-", default=main_cfg.get("default_model") == "base", font=("Arial", 9), enable_events=True),
         sg.Radio("small", "-MODEL-", key="-MODEL-small-", default=main_cfg.get("default_model") == "small", font=("Arial", 9), enable_events=True),
         sg.Radio("medium", "-MODEL-", key="-MODEL-medium-", default=main_cfg.get("default_model") == "medium", font=("Arial", 9), enable_events=True),
         sg.Radio("large", "-MODEL-", key="-MODEL-large-", default=main_cfg.get("default_model") == "large", font=("Arial", 9), enable_events=True)],
    ]
    
    right_panel = [
        [sg.Text("Step 2: Assign Speakers to Lines", font=("Arial", 12, "bold"))],
        [sg.Column([], key="-SPEAKER-CONTAINER-", size=(540, 253), scrollable=True, vertical_scroll_only=True, expand_y=False)],
        [sg.Button("Switch to female dub", key="-DUB-GENDER-STEP2-"), sg.Button("Dub lasts until end of video", key="-EXTEND-DUB-", disabled=True)],
    ]
    
    top_layout = [
        [sg.Column(left_panel, vertical_alignment="top", pad=(0, 0)), sg.Column(right_panel, vertical_alignment="top", pad=(3, 0))]
    ]
    
    step3_left = [
        [sg.Text("Step 3: Preview SRT Output", font=("Arial", 12, "bold"))],
        [sg.Multiline(size=(60, 21), key="-SRT-PREVIEW-", disabled=True)],
        [sg.Button("Edit preview", key="-EDIT-PREVIEW-", disabled=True), sg.Button("Undo manual changes", key="-UPDATE-", disabled=True), sg.Button("Show last manual", key="-SHOW-MANUAL-", disabled=True)],
    ]
    
    step3_right = [
        [sg.Text("Useful (?) Information", font=("Arial", 12, "bold"))],
        [sg.Multiline(size=(50, 21), key="-DEBUG-LOG-", disabled=True, autoscroll=True, expand_x=True, font=("Consolas", 9))],
        [sg.Button("Clear log", key="-CLEAR-LOG-")],
    ]
    
    bottom_layout = [
        [sg.Column(step3_left, vertical_alignment="top", pad=(0, 0)), sg.Column(step3_right, vertical_alignment="top", pad=(3, 0), expand_x=True)]
    ]
    
    save_layout = [
        [
            sg.InputText(key="-OUTPUT-", size=(40, 1), default_text="output.srt", disabled=True),
            sg.Button("Browse", key="-BROWSE-", disabled=True)
        ],
        [sg.Button("Save SRT", key="-SAVE-SRT-", disabled=True), sg.Button("Switch to Dub Editor", key="-DUB-EDITOR-"), sg.Button("GenderFixer", key="-ZIP-GENDER-"), sg.Button("AudioPatcher", key="-AUDIO-PATCHER-"), sg.Button("wtdRenamer", key="-FILE-RENAMER-"), sg.Button("Settings"), sg.Button("Exit", key="-EXIT-")],
    ]
    
    layout = [
        *top_layout,
        [sg.Text("")],
        *bottom_layout,
        [sg.Text("")],
        *save_layout,
    ]
    
    window = sg.Window("AutoDub", layout, size=(880, 900), margins=(0, 0), resizable=False, finalize=True, icon=_icon_path('AutoDub.ico'))
    set_main_win(window)
    generator.log_fn = lambda msg: window.write_event_value("-TIMING-LOG-", msg)
    
    # Startup: download required binaries in background
    _download_state = {"ffmpeg": False, "cpu": False, "cuda": False, "complete": False}
    _required_binaries = {"ffmpeg": True, "cpu": True, "cuda": main_cfg.get("use_cuda", False)}
    _download_threads = {}
    
    def _verify_cuda_dlls(exe_path: str) -> bool:
        """Check if critical CUDA DLLs exist alongside the binary."""
        dll_dir = os.path.dirname(exe_path)
        if not os.path.isdir(dll_dir):
            return False
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
        return cublas_ok and cuda_runtime_ok
    
    def _start_bg_download(label: str, method_name: str, target_dir: str, progress_prefix: str):
        """Start a background download thread with progress reporting."""
        if label in _download_threads:
            return  # already downloading
        
        def worker():
            method = getattr(generator, method_name)
            last_pct = 0
            def progress(pct):
                nonlocal last_pct
                if pct >= last_pct + 10:
                    window.write_event_value(f"-{progress_prefix}-DL-PROGRESS-", pct)
                    last_pct = pct
            try:
                method(target_dir, progress_callback=progress)
                window.write_event_value(f"-{progress_prefix}-DL-DONE-", True)
            except Exception as e:
                _log(window, f"[{label}] Download failed: {e}")
                window.write_event_value(f"-{progress_prefix}-DL-ERROR-", str(e))
            finally:
                _download_threads.pop(label, None)
        
        _download_threads[label] = threading.Thread(target=worker, daemon=True)
        _download_threads[label].start()
    
    def _check_all_binaries_ready():
        """Update transcribe button state based on download status."""
        all_ready = True
        if _required_binaries.get("ffmpeg", False) and not _download_state.get("ffmpeg", False):
            all_ready = False
        if _required_binaries.get("cpu", False) and not _download_state.get("cpu", False):
            all_ready = False
        if _required_binaries.get("cuda", False) and not _download_state.get("cuda", False):
            all_ready = False
        window["-TRANSCRIBE-"].update(disabled=not all_ready)
        if all_ready and not _download_state.get("complete", False):
            _download_state["complete"] = True
            _log(window, "All required binaries ready")
    
    def _check_binaries_and_start_downloads():
        """Check for required binaries and start downloads if missing."""
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        use_cuda = main_cfg.get("use_cuda", False)
        
        # FFmpeg (always required)
        if shutil.which('ffmpeg'):
            _download_state["ffmpeg"] = True
            _required_binaries["ffmpeg"] = False
        else:
            ffmpeg_path = os.path.join(exe_dir, '_ffmpeg_bin', 'ffmpeg.exe')
            if not os.path.exists(ffmpeg_path):
                _required_binaries["ffmpeg"] = True
                _start_bg_download("ffmpeg", "_download_ffmpeg", os.path.join(exe_dir, '_ffmpeg_bin'), "ffmpeg")
            else:
                _download_state["ffmpeg"] = True
                _required_binaries["ffmpeg"] = False
        
        # CPU Whisper (always required for fallback)
        cpu_path = os.path.join(exe_dir, '_whisper_bin', 'whisper.cpp.exe')
        if not os.path.exists(cpu_path):
            _required_binaries["cpu"] = True
            _start_bg_download("cpu", "_download_whisper_cpp", os.path.join(exe_dir, '_whisper_bin'), "cpu")
        else:
            _download_state["cpu"] = True
            _required_binaries["cpu"] = False
        
        # CUDA Whisper (only if enabled)
        if use_cuda:
            cuda_path = os.path.join(exe_dir, '_cuda_bin', 'whisper.cpp.cuda.exe')
            if not os.path.exists(cuda_path) or not _verify_cuda_dlls(cuda_path):
                _required_binaries["cuda"] = True
                _start_bg_download("cuda", "_download_whisper_cpp_cuda", os.path.join(exe_dir, '_cuda_bin'), "cuda")
            else:
                _download_state["cuda"] = True
                _required_binaries["cuda"] = False
        else:
            _download_state["cuda"] = True
            _required_binaries["cuda"] = False
        
        _check_all_binaries_ready()
    
    # Start checking and downloading binaries
    _check_binaries_and_start_downloads()
    
    _speaker_col_key = None
    _speaker_col_count = 0
    _transcription_successful = False
    _has_dub = False
    _has_speaker = False
    _unsaved_changes = False
    _dub_gender = "male"
    _preview_process = None
    _preview_ipc_dir = None
    _preview_srt_path = None
    _preview_video_path = None
    _preview_editing = False
    _audio_extracting = False
    _audio_extract_thread = None
    _audio_ready = False
    _gf_process = None
    _ap_process = None
    _fr_process = None
    _last_manual_preview = ""
    _manual_win = None
    _transcribing = False
    _audio_extract_start = None
    _model_downloading = False
    _model_download_seq = 0
    _export_mp4_path = None
    _audio_exporting = False

    def _start_model_download(model_name):
        nonlocal _model_download_seq, _model_downloading
        _model_download_seq += 1
        _model_downloading = True
        seq = _model_download_seq
        def worker(cur_seq):
            ok = generator.check_model(model_name)
            if cur_seq == _model_download_seq:
                window.write_event_value('-MODEL-CHECKED-', ok)
        threading.Thread(target=worker, args=(seq,), daemon=True).start()
        _log(window, f"Preparing model ({model_name})...")

    # Start initial model check for default model
    _start_model_download(main_cfg.get("default_model", "large"))
    
    def update_button_states():
        """Update button enabled/disabled states and colors based on current conditions"""
        nonlocal _transcription_successful, _has_dub, _has_speaker, _unsaved_changes, _audio_extracting, _audio_ready, _preview_editing, _last_manual_preview, _transcribing, _model_downloading, _export_mp4_path, _audio_exporting
        
        video_selected = bool(window["-VIDEO-"].get())
        try:
            srt_preview = window["-SRT-PREVIEW-"].get()
        except Exception:
            srt_preview = ""
        srt_has_content = bool(srt_preview.strip())
        _has_dub = "[male_dub]" in srt_preview or "[female_dub]" in srt_preview
        _has_speaker = False
        for line in srt_preview.split('\n'):
            if ':' in line and not line.strip().startswith('[') and not line.strip().isdigit():
                parts = line.split(':', 1)
                if parts[0].strip() and '-->' not in line:
                    _has_speaker = True
                    break
        
        window["-TRANSCRIBE-"].update(disabled=not (video_selected and not _audio_extracting and not _transcribing and not _model_downloading))
        for size in ('tiny', 'base', 'small', 'medium', 'large'):
            window[f"-MODEL-{size}-"].update(disabled=_model_downloading)
        window["-UPDATE-"].update(disabled=not _transcription_successful)
        
        last_is_dub = False
        if generator.subtitles:
            last_idx = len(generator.subtitles)
            last_is_dub = last_idx in generator.dub_requests or (generator.merged_dub_range and generator.merged_dub_range[1] == last_idx)
        window["-EXTEND-DUB-"].update(disabled=not (_transcription_successful and last_is_dub and not _preview_editing))
        window["-DUB-GENDER-STEP2-"].update(disabled=not (_transcription_successful and not _preview_editing))
        window["-OUTPUT-"].update(disabled=not srt_has_content)
        window["-BROWSE-"].update(disabled=not srt_has_content)
        
        window["-SAVE-SRT-"].update(disabled=not srt_has_content or _audio_exporting)
        if srt_has_content and _transcription_successful and _has_dub and _has_speaker:
            window["-SAVE-SRT-"].update(button_color=("white", "green"))
        else:
            window["-SAVE-SRT-"].update(button_color=('#FFFFFF', '#283b5b'))
        
        window["-EDIT-PREVIEW-"].update(disabled=not srt_has_content)
        window["-EDIT-PREVIEW-"].update("Lock preview" if _preview_editing else "Edit preview")
        for key in window.AllKeysDict:
            if key.startswith("-SPLIT-"):
                try:
                    window[key].update(disabled=_preview_editing)
                except:
                    pass
        window["-SHOW-MANUAL-"].update(disabled=not bool(_last_manual_preview))
        if _unsaved_changes:
            window["-EXIT-"].update(button_color=("white", "red"))
        else:
            window["-EXIT-"].update(button_color=('#FFFFFF', '#283b5b'))
        
        window.TKroot.update_idletasks()
    
    def rebuild_speaker_rows(restore_speakers: dict = None):
        """Rebuild the speaker input rows after transcription"""
        nonlocal _speaker_col_key, _speaker_col_count
        if _speaker_col_key and _speaker_col_key in window.AllKeysDict:
            window[_speaker_col_key].update(visible=False)
        
        _speaker_col_count += 1
        run = _speaker_col_count
        
        speaker_rows = [
            [sg.Text("Dub", size=(4, 1), font=("Arial", 9, "bold")),
             sg.Text("Speaker", size=(16, 1), font=("Arial", 9, "bold"), pad=((10,0),0)),
             sg.Text("Line Text", size=(40, 1), font=("Arial", 9, "bold"), pad=((15,0),0)),
             sg.Text("", size=(5, 1), pad=((5,0),0))],
        ]
        for i, sub in enumerate(generator.subtitles):
            has_words = bool(sub.words and len(sub.words) > 1)
            speaker_rows.append([
                sg.Checkbox("", key=f"-DUB-CHECK-{run}-{i}-", size=(4, 1), enable_events=True),

                sg.InputText(key=f"-SP-{run}-{i}-", size=(16, 1), font=("Arial", 9), enable_events=True),
                sg.Text(sub.text[:55], size=(40, 1), font=("Arial", 8)),
                sg.Button("Split", key=f"-SPLIT-{run}-{i}-", size=(5, 1), font=("Arial", 8), visible=has_words),
            ])
        
        new_key = f"-SPEAKER-COLS-{run}-"
        new_col = sg.pin(sg.Column(speaker_rows, key=new_key))
        window.extend_layout(window["-SPEAKER-CONTAINER-"], [[new_col]])
        _speaker_col_key = new_key
        
        window.refresh()
        window["-SPEAKER-CONTAINER-"].contents_changed()
        for i in range(len(generator.subtitles)):
            for sk in (f"-DUB-CHECK-{run}-{i}-", f"-SPLIT-{run}-{i}-"):
                if sk in window.AllKeysDict:
                    try:
                        window[sk].Widget.configure(takefocus=0)
                    except:
                        pass
        
        # Restore speaker names that were saved before the rebuild
        if restore_speakers:
            for new_idx, name in restore_speakers.items():
                key = f"-SP-{run}-{new_idx}-"
                if key in window.AllKeysDict:
                    window[key].update(name)
    
    def apply_dubs_from_checkboxes():
        """Read checkbox + speaker + gender state and update generator dub settings"""
        nonlocal _dub_gender
        generator.dub_requests.clear()
        generator.merged_dub_range = None
        run = _speaker_col_count
        gender = _dub_gender
        checked = []
        for i, sub in enumerate(generator.subtitles):
            check_key = f"-DUB-CHECK-{run}-{i}-"
            if check_key in window.AllKeysDict and window[check_key].get():
                checked.append(i)
        if checked:
            first = generator.subtitles[checked[0]]
            last = generator.subtitles[checked[-1]]
            speaker = ""
            speaker_key = f"-SP-{run}-{checked[0]}-"
            if speaker_key in window.AllKeysDict:
                speaker = window[speaker_key].get().strip()
            generator.add_dub_request(first.index, speaker, gender)
            generator.merged_dub_range = (first.index, last.index)
    
    def update_srt_preview(force=False):
        """Update the SRT preview — skip if user is editing manually (unless force=True)"""
        nonlocal _preview_editing
        if _preview_editing and not force:
            _log(window, "Preview not updated — lock the preview first")
            return
        speaker_map = {}
        run = _speaker_col_count
        for i, sub in enumerate(generator.subtitles):
            key = f"-SP-{run}-{i}-"
            if key in window.AllKeysDict:
                name = window[key].get().strip()
                if name:
                    speaker_map[sub.index] = name
        srt_content = generator.generate_srt(speaker_map=speaker_map)
        if main_cfg.get("punctuate_srt", True):
            dub_indices = set(generator.dub_requests.keys())
            if generator.merged_dub_range:
                s, e = generator.merged_dub_range
                dub_indices.update(range(s, e + 1))
            # Build set of 0-based SRT indices for split-generated lines
            split_srt_indices = set()
            srt_output_idx = 0
            merged = generator.merged_dub_range
            for sub in generator.subtitles:
                if merged and merged[0] < sub.index <= merged[1]:
                    continue
                if getattr(sub, 'from_split', False):
                    split_srt_indices.add(srt_output_idx)
                srt_output_idx += 1
            srt_content = _add_periods_to_srt(srt_content, dub_indices, split_indices=split_srt_indices)
        window["-SRT-PREVIEW-"].update(srt_content)
    
    QueuedTranscribe = None
    _log(window, "Application started")

    while True:
        event, values = window.read(timeout=500)
        
        # Process manual preview window events (non-blocking)
        if _manual_win:
            mev, _ = _manual_win.read(timeout=0)
            if mev in (sg.WINDOW_CLOSED, "Close"):
                _manual_win.close()
                _manual_win = None
        # Initial button state update (after first read)
        update_button_states()
        
        # Download progress / completion events
        if event == "-FFMPEG-DL-PROGRESS-":
            _log(window, f"[ffmpeg] Downloading: {values[event]}%")
        elif event == "-FFMPEG-DL-DONE-":
            _download_state["ffmpeg"] = True
            _required_binaries["ffmpeg"] = False
            _check_all_binaries_ready()
        elif event == "-FFMPEG-DL-ERROR-":
            _log(window, f"[ffmpeg] Download failed: {values[event]}")
            _required_binaries["ffmpeg"] = False
            _check_all_binaries_ready()
        elif event == "-CPU-DL-PROGRESS-":
            _log(window, f"[cpu whisper] Downloading: {values[event]}%")
        elif event == "-CPU-DL-DONE-":
            _download_state["cpu"] = True
            _required_binaries["cpu"] = False
            _check_all_binaries_ready()
        elif event == "-CPU-DL-ERROR-":
            _log(window, f"[cpu whisper] Download failed: {values[event]}")
            _required_binaries["cpu"] = False
            _check_all_binaries_ready()
        elif event == "-CUDA-DL-PROGRESS-":
            _log(window, f"[cuda whisper] Downloading: {values[event]}%")
        elif event == "-CUDA-DL-DONE-":
            _download_state["cuda"] = True
            _required_binaries["cuda"] = False
            _check_all_binaries_ready()
        elif event == "-CUDA-DL-ERROR-":
            _log(window, f"[cuda whisper] Download failed: {values[event]}")
            _required_binaries["cuda"] = False
            _check_all_binaries_ready()
        
        # Check background transcription completion
        if _transcribing and not generator._transcribe_thread.is_alive():
            _transcribing = False
            _whisper_end = time.perf_counter()
            
            total_time = _whisper_end - _click_time
            whisper_time = _whisper_end - generator._transcribe_start
            overhead = generator._transcribe_start - _click_time
            _logv(f"transcription: whisper {whisper_time:.1f}s, overhead {overhead:.1f}s, total {total_time:.1f}s", _click_time)
            _log(window, f"Transcription: whisper {whisper_time:.1f}s, overhead {overhead:.1f}s, total {total_time:.1f}s")
            
            if generator._transcribe_result[0]:
                if len(generator.subtitles) == 0:
                    generator.subtitles.append(SubtitleLine(
                        index=1, start_time="00:00:00,000",
                        end_time="00:00:03,000",
                        text="Place Holder Line",
                        words=[("Place", 0.0, 1.0), ("Holder", 1.0, 2.0), ("Line", 2.0, 3.0)]
                    ))
                window["-STATUS-"].update(
                    f"✓ Transcribed {len(generator.subtitles)} lines",
                    text_color="white"
                )
                _log(window, f"Transcription complete: {len(generator.subtitles)} lines")
                if generator.video_duration:
                    end_srt = generator._format_timestamp(generator.video_duration)
                    _log(window, f"  Video ends at {end_srt}")
                _transcription_successful = True
                rebuild_speaker_rows()
                update_srt_preview()
                update_button_states()

                # Auto-launch audio gain/DRC export if enabled
                if (main_cfg.get("audio_processing_enabled", False)
                        and _export_mp4_path
                        and not _audio_exporting):
                    _audio_exporting = True
                    window["-STATUS-"].update("Exporting processed MP4 audio...", text_color="yellow")
                    _log(window, f"Starting audio export: {_export_mp4_path}")
                    def _export_worker():
                        try:
                            ok = generator.process_audio_export(
                                output_path=_export_mp4_path,
                                gain_enabled=main_cfg.get("audio_gain_enabled", True),
                                gain_ceiling_db=main_cfg.get("audio_gain_ceiling", -20.0),
                                drc_enabled=main_cfg.get("audio_drc_enabled", True),
                                drc_threshold=main_cfg.get("audio_drc_threshold", -12.0),
                                drc_ratio=main_cfg.get("audio_drc_ratio", 2.0),
                                drc_attack=main_cfg.get("audio_drc_attack", 0.20),
                                drc_release=main_cfg.get("audio_drc_release", 1.0),
                            )
                        except Exception as e:
                            import traceback
                            _log(window, f"Audio export crashed: {e}\n{traceback.format_exc()}")
                            ok = False
                        window.write_event_value("-AUDIO-EXPORT-DONE-", ok)
                    threading.Thread(target=_export_worker, daemon=True).start()
            else:
                err = generator._last_error or "Unknown error"
                _log(window, f"Transcription failed: {err}")
                layout = [
                    [sg.Text("Transcription failed:")],
                    [sg.Multiline(err, size=(80, 15), key="-ERR-", disabled=True)],
                    [sg.Button("Okay")],
                ]
                sg.Window("Error", layout, modal=True, resizable=True, size=(700, 500)).read(close=True)
                window["-STATUS-"].update("✗ Transcription failed", text_color="red")
                _transcription_successful = False
                update_button_states()
        
        # Warn if transcription is taking unusually long
        if _transcribing:
            elapsed = time.perf_counter() - generator._transcribe_start
            if elapsed > generator._transcribe_est * 3 and generator._transcribe_est > 0 and not generator._transcribe_warned_slow:
                generator._transcribe_warned_slow = True
                _log(window, f"Transcription taking longer than expected ({elapsed:.0f}s elapsed, {generator._transcribe_est:.0f}s estimated)")
        
        if event == "-TIMING-LOG-":
            print(values[event])
            _log(window, values[event])
        
        if event == "-AUDIO-EXTRACTED-":
            _audio_extracting = False
            if values[event]:
                _audio_ready = True
                _logv("audio extraction OK", _audio_extract_start)
                _audio_extract_start = None
                _log(window, "Audio extraction OK")
            else:
                _audio_ready = False
                err = generator._last_error or "unknown"
                _logv(f"audio extraction failed: {err}", _audio_extract_start)
                _audio_extract_start = None
                _log(window, f"Audio extraction failed: {err}")
            update_button_states()
        
        if event == "-AUDIO-EXPORT-DONE-":
            _audio_exporting = False
            if values[event]:
                _log(window, f"Audio export OK — {_export_mp4_path}")
                window["-STATUS-"].update(
                    f"✓ Exported processed MP4: {os.path.basename(_export_mp4_path)}",
                    text_color="white"
                )
            else:
                err = generator._last_error or "unknown"
                _log(window, f"Audio export failed: {err}")
                window["-STATUS-"].update("✗ Audio export failed", text_color="red")
        
        if event in ('-MODEL-tiny-', '-MODEL-base-', '-MODEL-small-', '-MODEL-medium-', '-MODEL-large-'):
            model_name = event.split('-')[2]
            _start_model_download(model_name)

        if event == '-MODEL-CHECKED-':
            _model_downloading = False
            if values[event]:
                _log(window, "Model ready")
            else:
                _log(window, "Model check/ download failed")
            update_button_states()
        
        if event == sg.WINDOW_CLOSED or event == "-EXIT-":
            if _unsaved_changes:
                resp = _popup_yes_no("You have unsaved changes. Exit anyway?", title="Unsaved Changes")
                if resp != "Yes":
                    continue
            _log(window, "Application exiting")
            break
        
        if event == "-BROWSE-VIDEO-":
            path = sg.filedialog.askopenfilename(
                initialdir=main_cfg.get("input_dir", ""),
                filetypes=[("MP4 Files", "*.mp4")],
                parent=window.TKroot
            )
            if path:
                window["-VIDEO-"].update(path)
                generator.set_video(path)
                base_name = os.path.splitext(os.path.basename(path))[0]
                base_name = re.sub(r'[^a-zA-Z0-9-]', '', base_name)
                if main_cfg.get("rename_mp4", True):
                    if not base_name.startswith('_'):
                        base_name = '_' + base_name
                    if not re.search(r'-Clip\d{3}$', base_name):
                        base_name = base_name + '-Clip001'
                out_dir = main_cfg.get("output_dir") or os.path.dirname(path)
                window["-OUTPUT-"].update(os.path.join(out_dir, base_name + ".srt"))
                window["-SRT-PREVIEW-"].update("")
                window["-SRT-PREVIEW-"].update(disabled=True)
                _preview_editing = False
                window["-EDIT-PREVIEW-"].update("Edit preview")
                if _speaker_col_key and _speaker_col_key in window.AllKeysDict:
                    window[_speaker_col_key].update(visible=False)
                generator.subtitles = []
                _transcription_successful = False
                _has_dub = False
                _has_speaker = False
                _unsaved_changes = False
                _audio_ready = False
                _audio_extracting = True
                _audio_extract_start = time.perf_counter()
                generator.video_duration = None
                window.refresh()
                def _extract_worker():
                    ok = generator.extract_audio()
                    if not ok:
                        err = generator._last_error or "unknown"
                        _log(window, f"Audio extract failed: {err}")
                    window.write_event_value("-AUDIO-EXTRACTED-", ok)
                _audio_extract_thread = threading.Thread(target=_extract_worker, daemon=True)
                _audio_extract_thread.start()
                update_button_states()
        
        if event == "-TRANSCRIBE-":
            _click_time = time.perf_counter()
            if hasattr(generator, '_transcribe_thread') and generator._transcribe_thread.is_alive():
                window["-STATUS-"].update("⏳ Queued...", text_color="yellow")
                QueuedTranscribe = values["-VIDEO-"]
                continue
            
            video_path = values["-VIDEO-"]
            if not video_path:
                _popup("Info", "Please select a video file first")
                continue
            
            if generator.set_video(video_path):
                original_video_path = video_path
                generator.video_duration = generator._get_video_duration()
                generator.dub_extend_to_end = False
                window["-EXTEND-DUB-"].update("Dub lasts until end of video")
                window.refresh()
                base_name = os.path.splitext(os.path.basename(video_path))[0]
                base_name = re.sub(r'[^a-zA-Z0-9-]', '', base_name)
                if main_cfg.get("rename_mp4", True):
                    if not base_name.startswith('_'):
                        base_name = '_' + base_name
                    if not re.search(r'-Clip\d{3}$', base_name):
                        base_name = base_name + '-Clip001'
                srt_name = base_name + ".srt"
                out_dir = main_cfg.get("output_dir") or os.path.dirname(video_path)
                window["-OUTPUT-"].update(os.path.join(out_dir, srt_name))
                _export_mp4_path = video_path
                
                model_name = next(k.split("-")[-2] for k in ("-MODEL-tiny-", "-MODEL-base-", "-MODEL-small-", "-MODEL-medium-", "-MODEL-large-") if values.get(k))
                _logv(f"starting transcription: {os.path.basename(video_path)} (model: {model_name})", _click_time)
                _log(window, f"Starting transcription: {video_path} (model: {model_name})")
                
                if not _audio_ready:
                    _log(window, "Audio extraction starting")
                    window.refresh()
                    if not generator.extract_audio():
                        err = generator._last_error or "unknown"
                        _log(window, f"Audio extraction failed: {err}")
                        _transcription_successful = False
                        update_button_states()
                        continue
                    _audio_ready = True
                
                # Start background transcription (no cancel — whisper can't be cancelled)
                result = [False]
                start_time = time.perf_counter()
                
                def worker():
                    try:
                        use_cuda = main_cfg.get("use_cuda", True)
                        suppress_silence = main_cfg.get("suppress_silence", True)
                        cuda_fallback_cpu = main_cfg.get("cuda_fallback_cpu", False)
                        _log(window, f"Transcribing with CUDA={use_cuda}, fallback={cuda_fallback_cpu}")
                        result[0] = generator.transcribe_with_whisper(
                            model_name=model_name,
                            use_cuda=use_cuda,
                            suppress_silence=suppress_silence,
                            cuda_fallback_cpu=cuda_fallback_cpu,
                        )
                    except Exception as e:
                        import traceback
                        _log(window, f"Transcribe thread crashed: {e}\n{traceback.format_exc()}")
                
                generator._transcribe_thread = threading.Thread(target=worker, daemon=True)
                generator._transcribe_thread.start()
                _transcribing = True
                generator._transcribe_start = start_time
                generator._transcribe_result = result
                generator._transcribe_click_time = _click_time
                generator._transcribe_warned_slow = False
                
                # Estimate for timeout warning
                import wave as _wave
                try:
                    w = _wave.open(generator.audio_file, 'r')
                    audio_duration = w.getnframes() / w.getframerate()
                    w.close()
                except:
                    audio_duration = 120
                model_speed = {"tiny": 0.06, "base": 0.12, "small": 0.30, "medium": 0.70, "large": 1.4}
                transcribe_est = audio_duration * model_speed.get(model_name, 0.6)
                generator._transcribe_est = transcribe_est
                
                _log(window, "Transcription started in background")
                window["-STATUS-"].update(f"Transcribing... ({model_name})", text_color="yellow")
                update_button_states()
            else:
                _popup("Error", "Invalid video file")
        

        if QueuedTranscribe:
            vid = QueuedTranscribe
            QueuedTranscribe = None
            window["-VIDEO-"].update(vid)
            window.write_event_value("-TRANSCRIBE-", None)
        

        if event.startswith("-DUB-CHECK-"):
            run = _speaker_col_count
            now_checked = window[event].get()
            if not now_checked:
                # Was checked before toggle → clear all
                for i in range(len(generator.subtitles)):
                    window[f"-DUB-CHECK-{run}-{i}-"].update(False)
                apply_dubs_from_checkboxes()
                update_srt_preview()
            else:
                # Was unchecked → auto-fill range between min and max checked
                checked = []
                for i in range(len(generator.subtitles)):
                    key = f"-DUB-CHECK-{run}-{i}-"
                    if key in window.AllKeysDict and window[key].get():
                        checked.append(i)
                if len(checked) >= 2:
                    lo, hi = min(checked), max(checked)
                    for j in range(lo, hi + 1):
                        window[f"-DUB-CHECK-{run}-{j}-"].update(True)
            
            apply_dubs_from_checkboxes()
            update_srt_preview()
            _unsaved_changes = True
            update_button_states()
        

        if event.startswith("-SP-") and not event.startswith("-SPLIT-"):
            apply_dubs_from_checkboxes()
            update_srt_preview()
            _unsaved_changes = True
            update_button_states()
        

        if event == "-DUB-GENDER-STEP2-":
            _dub_gender = "female" if _dub_gender == "male" else "male"
            opposite = "female" if _dub_gender == "male" else "male"
            window["-DUB-GENDER-STEP2-"].update(f"Switch to {opposite} dub")
            apply_dubs_from_checkboxes()
            update_srt_preview(force=True)
            _unsaved_changes = True
            update_button_states()
        
        if event.startswith("-SPLIT-"):
            parts = event.split("-")
            split_idx = int(parts[3])
            sub = generator.subtitles[split_idx]
            if not sub.words or len(sub.words) < 2:
                continue
            if _show_split_window(window, generator, split_idx, rebuild_speaker_rows, update_srt_preview, _speaker_col_count, _popup):
                _unsaved_changes = True
                update_button_states()
        
        if event == "-EDIT-PREVIEW-":
            if not _preview_editing:
                _preview_editing = True
                window["-SRT-PREVIEW-"].update(disabled=False)
                window["-EDIT-PREVIEW-"].update("Lock Preview")
            else:
                _preview_editing = False
                _last_manual_preview = window["-SRT-PREVIEW-"].get()
                window["-SRT-PREVIEW-"].update(disabled=True)
                window["-EDIT-PREVIEW-"].update("Edit preview")
            update_button_states()
        
        if event == "-SHOW-MANUAL-":
            if _manual_win:
                _manual_win.close()
            _manual_win = sg.Window(
                "Last manual preview",
                [[sg.Multiline(_last_manual_preview, size=(80, 24), font=("Consolas", 10), key="-MANUAL-TEXT-")],
                 [sg.Button("Close")]],
                modal=False, finalize=True, icon=_icon_path('AutoDub.ico')
            )
        
        if event in ("Set Speakers", "-UPDATE-"):
            if not generator.subtitles:
                _popup("Info", "Please transcribe a video first")
                continue
            resp = _popup_yes_no(
                "This will overwrite all manual changes in the preview box. Continue?",
                title="Overwrite Warning"
            )
            if resp != "Yes":
                continue
            apply_dubs_from_checkboxes()
            update_srt_preview(force=True)
            update_button_states()
        
        if event == "-EXTEND-DUB-":
            if not generator.subtitles:
                _popup("Info", "Please transcribe a video first")
                continue
            last_idx = len(generator.subtitles)
            is_last_dub = (
                last_idx in generator.dub_requests or
                (generator.merged_dub_range and generator.merged_dub_range[1] == last_idx)
            )
            if not is_last_dub:
                _popup("Info", "The last line is not a dub line.")
                continue
            if not generator.video_duration:
                _popup("Error", "Video duration not available.")
                continue
            generator.dub_extend_to_end = not generator.dub_extend_to_end
            window["-EXTEND-DUB-"].update(
                "✓ Dub extends to end" if generator.dub_extend_to_end else "Dub lasts until end of video"
            )
            update_srt_preview(force=True)
            _unsaved_changes = True
            update_button_states()
        
        if event == "Settings":
            _log_path = config.get("log_path", "logs")
            if not os.path.isabs(_log_path):
                _log_path = os.path.join(_config_dir(), _log_path)

            settings_layout = [
                [sg.Text("Default video folder:"), sg.InputText(key="-SET-INPUT-", default_text=main_cfg.get("input_dir", "")), sg.FolderBrowse(initial_folder=main_cfg.get("input_dir", ""))],
                [sg.Text("Default SRT output folder:"), sg.InputText(key="-SET-OUTPUT-", default_text=main_cfg.get("output_dir", "")), sg.FolderBrowse(initial_folder=main_cfg.get("output_dir", ""))],
                [sg.Text("Dub Editor path:"), sg.InputText(key="-SET-DUB-EDITOR-", default_text=main_cfg.get("dub_editor_path", "")), sg.FileBrowse(file_types=(("Executables", "*.exe"),))],
                [sg.Text("Log folder:"), sg.InputText(key="-SET-LOG-PATH-", default_text=_log_path), sg.FolderBrowse(initial_folder=_log_path)],
                [sg.HorizontalSeparator()],
                [sg.Text("Default transcription model:")],
                [sg.Radio("tiny", "-SET-MODEL-", key="-SET-MODEL-tiny-", default=main_cfg.get("default_model") == "tiny"),
                 sg.Radio("base", "-SET-MODEL-", key="-SET-MODEL-base-", default=main_cfg.get("default_model") == "base"),
                 sg.Radio("small", "-SET-MODEL-", key="-SET-MODEL-small-", default=main_cfg.get("default_model") == "small"),
                 sg.Radio("medium", "-SET-MODEL-", key="-SET-MODEL-medium-", default=main_cfg.get("default_model") == "medium"),
                 sg.Radio("large", "-SET-MODEL-", key="-SET-MODEL-large-", default=main_cfg.get("default_model") == "large")],
                [sg.Button("Manage downloaded models", key="-MANAGE-MODELS-")],
                [sg.Button("Manage additional binaries", key="-MANAGE-BINARIES-")],
                [sg.HorizontalSeparator()],
                [sg.Checkbox("Use GPU acceleration (requires nVidia GPU)", key="-SET-USE-CUDA-",
                             default=main_cfg.get("use_cuda", False))],
                [sg.Checkbox("Fallback to CPU if GPU fails", key="-SET-CUDA-FALLBACK-",
                             default=main_cfg.get("cuda_fallback_cpu", False))],
                [sg.Checkbox("Suppress non-speech (experimental)", key="-SET-SUPPRESS-SILENCE-",
                             default=main_cfg.get("suppress_silence", True))],
                [sg.Checkbox("Rename MP4 files (for compatibility with Dub Editor)", 
                             key="-SET-RENAME-MP4-", 
                             default=main_cfg.get("rename_mp4", True))],
                [sg.Checkbox("Add periods to sentence lines automatically", key="-SET-PUNCTUATE-",
                             default=main_cfg.get("punctuate_srt", True))],
                [sg.Checkbox("Remove logs older than", key="-SET-LOG-CLEANUP-",
                             default=main_cfg.get("log_cleanup_enabled", True)),
                  sg.Spin([str(i) for i in range(1, 366)], initial_value=str(main_cfg.get("log_cleanup_days", 30)),
                          key="-SET-LOG-DAYS-", size=(5, 1)), sg.Text("days")],
                [sg.HorizontalSeparator()],
                [sg.Text("Audio Processing:", font=("Arial", 10, "bold"))],
                [sg.Checkbox("Enable audio gain/DRC export after transcription", key="-SET-AUDIO-ENABLE-",
                             default=main_cfg.get("audio_processing_enabled", False))],
                [sg.Button("Open Audio Settings...", key="-OPEN-AUDIO-SETTINGS-")],
                [sg.HorizontalSeparator()],
                [sg.Text("GenderFixer Settings:", font=("Arial", 10, "bold"))],
                [sg.Text("Default input folder:"), sg.InputText(key="-SET-GF-INPUT-", default_text=gf_cfg.get("input_dir", "")), sg.FolderBrowse(initial_folder=gf_cfg.get("input_dir", ""))],
                [sg.Checkbox("Overwrite original files without confirmation", key="-SET-GF-OVERWRITE-", default=gf_cfg.get("overwrite_original", False))],
                [sg.HorizontalSeparator()],
                [sg.Text("wtdRenamer Settings:", font=("Arial", 10, "bold"))],
                [sg.Text("Default folder:"), sg.InputText(key="-SET-RENAMER-FOLDER-", default_text=renamer_cfg.get("default_folder", "")), sg.FolderBrowse(initial_folder=renamer_cfg.get("default_folder", ""))],
                [sg.Checkbox("Remember last used folder", key="-SET-RENAMER-REMEMBER-", default=renamer_cfg.get("remember_last", False))],
                [sg.HorizontalSeparator()],
                [sg.Text(f"Version: {__version__}", font=("Arial", 9))],
                [sg.Button("Save"), sg.Button("Cancel")],
            ]
            set_win = sg.Window("Settings",
                [[sg.Column(settings_layout, scrollable=True, size=(640, 510), expand_x=False, expand_y=False, key="-SETTINGS-COL-")]],
                modal=True, resizable=False, size=(660, 550), location=_popup_location((660, 550)), finalize=True,
                icon=_icon_path('AutoDub.ico'))
            set_win.refresh(); set_win["-SETTINGS-COL-"].contents_changed()
            while True:
                sev, svals = set_win.read()
                if sev in (sg.WINDOW_CLOSED, "Cancel"):
                    break
                if sev == "Save":
                    inp = svals["-SET-INPUT-"].strip()
                    outp = svals["-SET-OUTPUT-"].strip()
                    model = next(k.split("-")[-2] for k in ("-SET-MODEL-tiny-", "-SET-MODEL-base-", "-SET-MODEL-small-", "-SET-MODEL-medium-", "-SET-MODEL-large-") if svals.get(k))
                    main_cfg["input_dir"] = inp
                    main_cfg["output_dir"] = outp
                    main_cfg["default_model"] = model
                    main_cfg["rename_mp4"] = svals.get("-SET-RENAME-MP4-", True)
                    main_cfg["log_cleanup_enabled"] = svals.get("-SET-LOG-CLEANUP-", True)
                    main_cfg["log_cleanup_days"] = int(svals.get("-SET-LOG-DAYS-", 30))
                    
                    old_cuda = main_cfg.get("use_cuda", False)
                    new_cuda = svals.get("-SET-USE-CUDA-", False)
                    main_cfg["use_cuda"] = new_cuda
                    main_cfg["cuda_fallback_cpu"] = svals.get("-SET-CUDA-FALLBACK-", False)
                    main_cfg["suppress_silence"] = svals.get("-SET-SUPPRESS-SILENCE-", True)
                    main_cfg["punctuate_srt"] = svals.get("-SET-PUNCTUATE-", True)
                    main_cfg["audio_processing_enabled"] = svals.get("-SET-AUDIO-ENABLE-", False)
                    renamer_cfg["default_folder"] = svals.get("-SET-RENAMER-FOLDER-", "").strip()
                    renamer_cfg["remember_last"] = svals.get("-SET-RENAMER-REMEMBER-", False)
                    main_cfg["dub_editor_path"] = svals.get("-SET-DUB-EDITOR-", "").strip()
                    config["log_path"] = svals.get("-SET-LOG-PATH-", "logs").strip()
                    gf_cfg["input_dir"] = svals["-SET-GF-INPUT-"].strip()
                    gf_cfg["overwrite_original"] = svals.get("-SET-GF-OVERWRITE-", False)
                    save_config(config)
                    
                    if new_cuda != old_cuda:
                        window.write_event_value("-CUDA-TOGGLED-", new_cuda)
                    
                    break
                if sev == "-OPEN-AUDIO-SETTINGS-":
                    audio_layout = [
                        [sg.Text("Autogain", font=("Arial", 10, "bold"))],
                        [sg.Checkbox("Enable autogain", key="-AD-GAIN-ENABLE-",
                                     default=main_cfg.get("audio_gain_enabled", True))],
                        [sg.Text("Target loudness (LUFS):"), sg.Slider(range=(-30, -5),
                                default_value=main_cfg.get("audio_gain_ceiling", -20.0),
                                key="-AD-GAIN-CEILING-", orientation="h", size=(30, 10), resolution=0.5)],
                        [sg.HorizontalSeparator()],
                        [sg.Text("DRC", font=("Arial", 10, "bold"))],
                        [sg.Checkbox("Enable DRC", key="-AD-DRC-ENABLE-",
                                     default=main_cfg.get("audio_drc_enabled", True))],
                        [sg.Text("Threshold (dB):"), sg.Slider(range=(-30, 0),
                                default_value=main_cfg.get("audio_drc_threshold", -12.0),
                                key="-AD-DRC-THRESHOLD-", orientation="h", size=(30, 10), resolution=0.5)],
                        [sg.Text("Ratio:"), sg.Slider(range=(1.0, 10.0),
                                default_value=main_cfg.get("audio_drc_ratio", 2.0),
                                key="-AD-DRC-RATIO-", orientation="h", size=(30, 10), resolution=0.5)],
                        [sg.Text("Attack (s):"), sg.Slider(range=(0.01, 1.0),
                                default_value=main_cfg.get("audio_drc_attack", 0.20),
                                key="-AD-DRC-ATTACK-", orientation="h", size=(30, 10), resolution=0.01)],
                        [sg.Text("Release (s):"), sg.Slider(range=(0.05, 5.0),
                                default_value=main_cfg.get("audio_drc_release", 1.0),
                                key="-AD-DRC-RELEASE-", orientation="h", size=(30, 10), resolution=0.05)],
                        [sg.HorizontalSeparator()],
                        [sg.Button("Restore settings for WTD", key="-AD-RESTORE-")],
                        [sg.Button("Save", key="-AD-SAVE-"), sg.Button("Cancel", key="-AD-CANCEL-")],
                    ]
                    aud_win = sg.Window("Audio Settings", audio_layout, modal=True, resizable=True,
                                        size=(550, 480), location=_popup_location((550, 480)),
                                        icon=_icon_path('AutoDub.ico'))
                    while True:
                        aev, avals = aud_win.read()
                        if aev in (sg.WINDOW_CLOSED, "-AD-CANCEL-"):
                            break
                        if aev == "-AD-SAVE-":
                            main_cfg["audio_gain_enabled"] = avals.get("-AD-GAIN-ENABLE-", True)
                            main_cfg["audio_gain_ceiling"] = float(avals.get("-AD-GAIN-CEILING-", -20.0))
                            main_cfg["audio_drc_enabled"] = avals.get("-AD-DRC-ENABLE-", True)
                            main_cfg["audio_drc_threshold"] = float(avals.get("-AD-DRC-THRESHOLD-", -12.0))
                            main_cfg["audio_drc_ratio"] = float(avals.get("-AD-DRC-RATIO-", 2.0))
                            main_cfg["audio_drc_attack"] = float(avals.get("-AD-DRC-ATTACK-", 0.20))
                            main_cfg["audio_drc_release"] = float(avals.get("-AD-DRC-RELEASE-", 1.0))
                            save_config(config)
                            _log(window, "Audio settings saved")
                            break
                        if aev == "-AD-RESTORE-":
                            main_cfg["audio_gain_enabled"] = True
                            main_cfg["audio_gain_ceiling"] = -20.0
                            main_cfg["audio_drc_enabled"] = True
                            main_cfg["audio_drc_threshold"] = -12.0
                            main_cfg["audio_drc_ratio"] = 2.0
                            main_cfg["audio_drc_attack"] = 0.20
                            main_cfg["audio_drc_release"] = 1.0
                            aud_win["-AD-GAIN-ENABLE-"].update(True)
                            aud_win["-AD-GAIN-CEILING-"].update(-20.0)
                            aud_win["-AD-DRC-ENABLE-"].update(True)
                            aud_win["-AD-DRC-THRESHOLD-"].update(-12.0)
                            aud_win["-AD-DRC-RATIO-"].update(2.0)
                            aud_win["-AD-DRC-ATTACK-"].update(0.20)
                            aud_win["-AD-DRC-RELEASE-"].update(1.0)
                            _log(window, "Audio settings restored to defaults")
                    aud_win.close()
                if sev == "-MANAGE-MODELS-":
                    _base = os.path.dirname(os.path.abspath(__file__))
                    if getattr(sys, 'frozen', False):
                        _base = os.path.dirname(sys.executable)
                    _models_dir = os.path.join(_base, 'models')
                    if not os.path.isdir(_models_dir):
                        sg.popup("No model files found.", title="Model Cache")
                        continue
                    _files = sorted([f for f in os.listdir(_models_dir) if f.endswith('.bin') and f.startswith('ggml')])
                    if not _files:
                        sg.popup("No model files found.", title="Model Cache")
                        continue
                    _m_layout = [[sg.Text("Select model files to delete:")]]
                    for f in _files:
                        _path = os.path.join(_models_dir, f)
                        _sz = os.path.getsize(_path)
                        _sz_str = f"{_sz / (1024**3):.1f} GB" if _sz > 1024**3 else f"{_sz / (1024**2):.0f} MB"
                        _m_layout.append([sg.Checkbox(f"  {f} ({_sz_str})", key=f)])
                    _m_layout.append([sg.Button("Delete Selected"), sg.Button("Cancel")])
                    _pop = sg.Window("Manage Models", _m_layout, modal=True,
                                     location=_popup_location((420, 300)))
                    while True:
                        _pev, _pvals = _pop.read()
                        if _pev in (sg.WINDOW_CLOSED, "Cancel"):
                            break
                        if _pev == "Delete Selected":
                            _deleted = []
                            for f in _files:
                                if _pvals.get(f):
                                    os.remove(os.path.join(_models_dir, f))
                                    _deleted.append(f)
                            if _deleted:
                                sg.popup(f"Deleted:\n" + "\n".join(_deleted), title="Done")
                            break
                    _pop.close()

                if sev == "-MANAGE-BINARIES-":
                    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
                    binaries = [
                        ("FFmpeg", os.path.join(exe_dir, '_ffmpeg_bin'), generator.remove_ffmpeg_binaries),
                        ("Whisper.cpp CPU", os.path.join(exe_dir, '_whisper_bin'), generator.remove_cpu_binaries),
                        ("Whisper.cpp GPU", os.path.join(exe_dir, '_cuda_bin'), generator.remove_cuda_binaries),
                    ]
                    layout = [[sg.Text("Select binaries to remove:")]]
                    for name, path, _ in binaries:
                        if os.path.isdir(path):
                            files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
                            total = sum(os.path.getsize(os.path.join(path, f)) for f in files)
                            sz_str = f"{total / (1024**2):.0f} MB" if total < 1024**3 else f"{total / (1024**3):.1f} GB"
                            layout.append([sg.Checkbox(f"  {name} ({sz_str}, {len(files)} files)", key=f"-REMOVE-{name.replace(' ', '-').replace('.', '')}-")])
                        else:
                            layout.append([sg.Text(f"  {name}: not found", text_color="gray")])
                    layout.append([sg.Button("Remove Selected"), sg.Button("Cancel")])
                    pop = sg.Window("Manage Binaries", layout, modal=True,
                                    location=_popup_location((450, 300)))
                    while True:
                        pev, pvals = pop.read()
                        if pev in (sg.WINDOW_CLOSED, "Cancel"):
                            break
                        if pev == "Remove Selected":
                            removed_any = False
                            for name, path, remove_fn in binaries:
                                key = f"-REMOVE-{name.replace(' ', '-').replace('.', '')}-"
                                if pvals.get(key):
                                    try:
                                        bytes_freed, files_removed = remove_fn()
                                        if files_removed:
                                            _log(window, f"Removed {name}: {len(files_removed)} files, {bytes_freed / (1024**2):.1f} MB")
                                            removed_any = True
                                    except Exception as e:
                                        _log(window, f"Error removing {name}: {e}")
                            if removed_any:
                                sg.popup("Selected binaries removed.", title="Done")
                            else:
                                sg.popup("No binaries selected or already removed.", title="Info")
                            break
                    pop.close()
            set_win.close()
        
        if event == "-CUDA-TOGGLED-":
            use_cuda = values[event]
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            if use_cuda:
                # Start CUDA download
                cuda_path = os.path.join(exe_dir, '_cuda_bin', 'whisper.cpp.cuda.exe')
                if not os.path.exists(cuda_path) or not _verify_cuda_dlls(cuda_path):
                    _log(window, "CUDA enabled: downloading CUDA binaries...")
                    _required_binaries["cuda"] = True
                    _start_bg_download("cuda", "_download_whisper_cpp_cuda", os.path.join(exe_dir, '_cuda_bin'), "cuda")
            else:
                # CUDA disabled: ensure CPU binary is present
                cpu_path = os.path.join(exe_dir, '_whisper_bin', 'whisper.cpp.exe')
                if not os.path.exists(cpu_path):
                    _log(window, "CUDA disabled: downloading CPU binaries...")
                    _required_binaries["cpu"] = True
                    _start_bg_download("cpu", "_download_whisper_cpp", os.path.join(exe_dir, '_whisper_bin'), "cpu")
        
        if event == "-BROWSE-":
            cur = values["-OUTPUT-"]
            initial_dir = main_cfg.get("output_dir", "")
            initial_file = ""
            if cur:
                d = os.path.dirname(cur)
                if d:
                    initial_dir = d
                initial_file = os.path.basename(cur)
            path = sg.filedialog.asksaveasfilename(
                initialdir=initial_dir,
                initialfile=initial_file or "output.srt",
                filetypes=[("SRT Files", "*.srt")],
                defaultextension=".srt",
                parent=window.TKroot
            )
            if path:
                window["-OUTPUT-"].update(path)
        
        if event == "-PREVIEW-":
            if not original_video_path:
                _popup("Error", "No source video loaded")
                continue
            
            srt_content = values["-SRT-PREVIEW-"]
            if not srt_content.strip():
                _popup("Error", "No SRT content to preview")
                continue
            
            fixed_content, changed = normalize_dub_markers(srt_content)
            norm_msg = None
            if changed:
                norm_msg = "Normalized the dub marker"
                srt_content = fixed_content
                window["-SRT-PREVIEW-"].update(srt_content)
            
            ok, msg = validate_single_dub(srt_content)
            if not ok:
                _popup("Preview Error", msg, size=(500, 120))
                continue
            
            _preview_ipc_dir = tempfile.mkdtemp(prefix="wtd_preview_")
            _preview_srt_path = os.path.join(_preview_ipc_dir, "preview.srt")
            with open(_preview_srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)
            
            video_path = values["-VIDEO-"]
            if not os.path.exists(video_path):
                _popup("Error", "Video file not found")
                continue
            _preview_video_path = video_path
            
            if not _find_mpv(main_cfg):
                _popup("MPV Not Found",
                       "mpv is required for the preview window.\n\n"
                       "Download from: https://mpv.io/installation/\n"
                       "Make sure mpv.exe is in your PATH.",
                       size=(550, 180))
                continue
            
            exe = getattr(sys, 'frozen', False)
            base_dir = os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))
            preview_script = os.path.join(base_dir, "wtd_preview.exe" if exe else "wtd_preview.py")
            
            if not os.path.exists(preview_script):
                _popup("Error", f"Preview script not found:\n{preview_script}", size=(500, 120))
                continue
            
            cmd = [sys.executable, preview_script] if not exe else [preview_script]
            cmd += ["--video", video_path, "--srt", _preview_srt_path, "--ipc-dir", _preview_ipc_dir]
            mpv_path = _resolve_mpv_path(main_cfg)
            if mpv_path:
                cmd += ["--mpv-path", mpv_path]
            _log(window, f"Using mpv: {mpv_path}")
            
            _log(window, f"Launching preview: {' '.join(cmd)}")
            _preview_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            def _log_preview_stderr(proc):
                for line in iter(proc.stderr.readline, b''):
                    _log(window, f"preview: {line.decode('utf-8', errors='replace').rstrip()}")
            threading.Thread(target=_log_preview_stderr, args=(_preview_process,), daemon=True).start()
            
            window["-STATUS-"].update("Preview window open...", text_color="cyan")
            window["-PREVIEW-"].update(disabled=True, text="Preview...")
            
            if norm_msg:
                _popup("Preview Info",
                       f"{norm_msg} before opening preview.\n"
                       "The preview has been updated with normalized markers.\n"
                       "Save in preview to accept changes.",
                       size=(550, 150))
        
        if event == "-SAVE-SRT-":
            if not original_video_path:
                _popup("Error", "No source video loaded")
                continue
            
            video_dir = os.path.dirname(original_video_path)
            video_name = os.path.basename(original_video_path)
            base_name, ext = os.path.splitext(video_name)
            base_name = re.sub(r'[^a-zA-Z0-9-]', '', base_name)
            
            rename_enabled = main_cfg.get("rename_mp4", True)
            
            if rename_enabled:
                clip_match = re.search(r'-Clip(\d{3})$', base_name)
                has_clip_suffix = clip_match is not None
                clip_num = int(clip_match.group(1)) if has_clip_suffix else 1
                
                if not base_name.startswith('_'):
                    base_name = '_' + base_name
                
                if not has_clip_suffix:
                    base_name = base_name + '-Clip001'
                    clip_num = 1
                
                original_name = os.path.splitext(os.path.basename(original_video_path))[0]
                already_renamed = original_name.startswith('_') and has_clip_suffix
                
                if not already_renamed:
                    while True:
                        if clip_num > 1:
                            candidate_base = re.sub(r'-Clip\d{3}$', f'-Clip{clip_num:03d}', base_name)
                        else:
                            candidate_base = base_name
                        
                        candidate_video = os.path.join(video_dir, candidate_base + ext)
                        if not os.path.exists(candidate_video):
                            base_name = candidate_base
                            new_video_path = candidate_video
                            break
                        
                        clip_num += 1
                        if clip_num > 999:
                            _popup("Error", "Disable renaming or delete some videos (Clip001 to Clip999 exist)")
                            raise Exception("Too many Clips")
                else:
                    new_video_path = original_video_path
                
                new_video_name = base_name + ext
            else:
                new_video_path = original_video_path
                new_video_name = video_name
            
            out_val = values.get("-OUTPUT-", "")
            srt_dir = os.path.dirname(out_val) or main_cfg.get("output_dir") or video_dir
            custom_name = os.path.basename(out_val)
            if custom_name and custom_name != base_name + '.srt':
                new_srt_path = out_val
            else:
                new_srt_path = os.path.join(srt_dir, base_name + '.srt')
            
            while os.path.exists(new_srt_path):
                resp = _popup_yes_no_iterate(
                    f"SRT file already exists:\n{new_srt_path}\n\nOverwrite?",
                    title="Confirm Overwrite"
                )
                if resp == "No":
                    break
                if resp == "Yes":
                    break
                if resp == "Iterate":
                    clip_match = re.search(r'-Clip(\d{3})$', base_name)
                    if clip_match:
                        clip_num = int(clip_match.group(1)) + 1
                        if clip_num > 999:
                            _popup("Error", "Cannot add higher clip number (Clip999 reached).\nDisable renaming or delete some existing clips.")
                            break
                        base_name = re.sub(r'-Clip\d{3}$', f'-Clip{clip_num:03d}', base_name)
                    else:
                        base_name = base_name + '-Clip001'
                    
                    new_srt_path = os.path.join(srt_dir, base_name + '.srt')
                    if rename_enabled:
                        new_video_path = os.path.join(video_dir, base_name + ext)
                        new_video_name = base_name + ext
                    
                    if not os.path.exists(new_srt_path) and (not rename_enabled or not os.path.exists(new_video_path)):
                        break
                    continue
            else:
                resp = "Yes"
            
            if resp == "No":
                continue
            
            try:
                if rename_enabled and not already_renamed:
                    os.rename(original_video_path, new_video_path)
                    original_video_path = new_video_path
                
                with open(new_srt_path, 'w', encoding='utf-8') as f:
                    f.write(values["-SRT-PREVIEW-"])
                
                window["-VIDEO-"].update(new_video_path)
                window["-OUTPUT-"].update(new_srt_path)
                _log(window, f"Saved SRT: {new_srt_path}")
                if rename_enabled and not already_renamed:
                    _log(window, f"Renamed video: {new_video_path}")
                elif not rename_enabled:
                    _log(window, f"MP4 rename disabled; kept original: {original_video_path}")
                video_dir_name = os.path.basename(os.path.dirname(new_video_path)).lower()
                srt_dir_name = os.path.basename(os.path.dirname(new_srt_path)).lower()
                video_gparent = os.path.dirname(os.path.dirname(new_video_path))
                srt_gparent = os.path.dirname(os.path.dirname(new_srt_path))
                structure_ok = (
                    video_dir_name in ("videoclips", "videoclip")
                    and srt_dir_name in ("subtitles", "subtitle")
                    and video_gparent and srt_gparent
                    and os.path.normpath(video_gparent).lower() == os.path.normpath(srt_gparent).lower()
                    and os.path.splitext(os.path.basename(new_video_path))[0].lower()
                    == os.path.splitext(os.path.basename(new_srt_path))[0].lower()
                )
                structure_text = "✓ Correct folder structure" if structure_ok else "✗ Incorrect folder structure"
                loc = _popup_location((500, 150))
                sg.Window("Success", [
                    [sg.Text(f"✓ Files saved:\n{new_video_name}\n{base_name}.srt")],
                    [sg.Text(structure_text, text_color="white" if structure_ok else "red")],
                    [sg.Button("Okay")],
                ], modal=True, size=(500, 150), location=loc, finalize=True).read(close=True)
                _unsaved_changes = False
                update_button_states()
            except Exception as e:
                _log(window, f"Save failed: {e}")
                _popup("Error", f"Failed to save files: {e}", size=(500, 150))
        
        if event in ("-ZIP-GENDER-", "-FILE-RENAMER-", "-AUDIO-PATCHER-"):
            scripts = {"-ZIP-GENDER-": "GenderFixer", "-FILE-RENAMER-": "wtdRenamer", "-AUDIO-PATCHER-": "AudioPatcher"}
            exe = getattr(sys, 'frozen', False)
            base = os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))
            script = os.path.join(base, scripts[event] + (".exe" if exe else ".py"))
            if exe and not os.path.exists(script):
                script = os.path.join(os.path.dirname(base), scripts[event] + ".exe")
            if os.path.exists(script):
                proc = subprocess.Popen([script] if exe else [sys.executable, script], creationflags=subprocess.CREATE_NO_WINDOW)
                if event == "-ZIP-GENDER-":
                    _gf_process = proc
                    window["-ZIP-GENDER-"].update(disabled=True)
                elif event == "-AUDIO-PATCHER-":
                    _ap_process = proc
                    window["-AUDIO-PATCHER-"].update(disabled=True)
                else:
                    _fr_process = proc
                    window["-FILE-RENAMER-"].update(disabled=True)
            else:
                _popup("Error", f"{scripts[event]} not found.")
        
        if event == "-CLEAR-LOG-":
            window["-DEBUG-LOG-"].update("")
            _log(window, "Log cleared")
        
        if event == "-DUB-EDITOR-":
            dub_path = main_cfg.get("dub_editor_path", "")
            if not dub_path or not os.path.exists(dub_path):
                default_dir = os.path.expandvars(
                    r"%LOCALAPPDATA%\Programs\electron-react-boilerplate")
                default_exe = os.path.join(default_dir, "Dub Editor SA.exe")
                if os.path.exists(default_exe):
                    dub_path = default_exe
                else:
                    dub_path = sg.filedialog.askopenfilename(
                        title="Locate Dub Editor SA.exe",
                        filetypes=[("Executable", "*.exe")],
                        initialdir=default_dir)
                    if not dub_path:
                        continue
                    if not os.path.exists(dub_path):
                        _popup("Error", "File not found.")
                        continue
                main_cfg["dub_editor_path"] = dub_path
                save_config(config)
                _log(window, f"Dub Editor path set: {dub_path}")
            _log(window, "Launching Dub Editor...")
            try:
                proc = subprocess.Popen(
                    [dub_path], creationflags=subprocess.CREATE_NO_WINDOW)
                import time as _time
                _time.sleep(0.5)
                if proc.poll() is not None:
                    _popup("Error", "Dub Editor started but exited immediately.")
                    continue
                _log(window, "Dub Editor launched. Closing AutoDub...")
                window.close()
                break
            except Exception as e:
                _popup("Error", f"Failed to launch Dub Editor:\n{e}")
                continue
        
        if _preview_process is not None and _preview_process.poll() is not None:
            ret = _preview_process.poll()
            _log(window, f"Preview process exited with code {ret}")
            marker_path = os.path.join(_preview_ipc_dir, "saved.txt") if _preview_ipc_dir else None
            saved = marker_path and os.path.exists(marker_path) and open(marker_path, 'r').read().strip() == "1"
            
            if saved and _preview_srt_path and os.path.exists(_preview_srt_path):
                with open(_preview_srt_path, 'r', encoding='utf-8') as f:
                    window["-SRT-PREVIEW-"].update(f.read())
                _unsaved_changes = True
                window["-STATUS-"].update("✓ Preview saved — changes loaded", text_color="white")
                _log(window, "Preview saved, SRT updated")
            else:
                window["-STATUS-"].update("Preview closed without saving", text_color="yellow")
                _log(window, "Preview closed without saving")
            
            _preview_process = None
            try:
                if _preview_ipc_dir and os.path.exists(_preview_ipc_dir):
                    shutil.rmtree(_preview_ipc_dir)
            except Exception:
                pass
            _preview_ipc_dir = None
            _preview_srt_path = None
            _preview_video_path = None
            window["-PREVIEW-"].update(disabled=False, text="Preview")
            update_button_states()
        
        if _gf_process is not None and _gf_process.poll() is not None:
            _gf_process = None
            window["-ZIP-GENDER-"].update(disabled=False)
        
        if _ap_process is not None and _ap_process.poll() is not None:
            _ap_process = None
            window["-AUDIO-PATCHER-"].update(disabled=False)
        
        if _fr_process is not None and _fr_process.poll() is not None:
            _fr_process = None
            window["-FILE-RENAMER-"].update(disabled=False)
        
        update_button_states()
    
    _cleanup_old_logs(main_cfg.get("log_cleanup_enabled", True), main_cfg.get("log_cleanup_days", 30), log_path)
    _close_logging()
    save_config(config)
    generator.cleanup()
    window.close()


if __name__ == "__main__":
    main()
