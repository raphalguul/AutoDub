import PySimpleGUI as sg
import os
import sys
import json
import threading
import time
import io
from collections import deque
from datetime import datetime
from wtd_generator import WTDDubGenerator

_AP_CONFIG_DIR = None

def _icon_path(name):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'Icons', name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Icons', name)

def _ap_config_dir():
    global _AP_CONFIG_DIR
    if _AP_CONFIG_DIR is None:
        exe = getattr(sys, 'frozen', False)
        _AP_CONFIG_DIR = os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))
    return _AP_CONFIG_DIR

_DEFAULT_AP_CONFIG = {
    "input_dir": "",
    "output_dir": "",
    "overwrite": True,
    "suffix": "_patched",
    "gain_enabled": True,
    "gain_ceiling": -20.0,
    "drc_enabled": True,
    "drc_threshold": -12.0,
    "drc_ratio": 2.0,
    "drc_attack": 0.20,
    "drc_release": 1.0,
}

def _ap_load_config():
    path = os.path.join(_ap_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
        stored = cfg.get("audio_patcher", {})
        merged = dict(_DEFAULT_AP_CONFIG)
        merged.update(stored)
        return merged
    except:
        return dict(_DEFAULT_AP_CONFIG)

def _ap_save_config(ap_cfg):
    path = os.path.join(_ap_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
    except:
        cfg = {"main": {}, "gender_fixer": {}, "file_renamer": {}, "audio_patcher": {}, "log_path": "logs"}
    cfg["audio_patcher"] = ap_cfg
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)

_ap_log_file_path = None
_ap_log_file_handle = None
_ap_win = None

def _popup_location(popup_size):
    global _ap_win
    if _ap_win is not None:
        try:
            mx, my = _ap_win.current_location()
            mw, mh = _ap_win.size
            pw, ph = popup_size
            return (mx + (mw - pw) // 2, my + (mh - ph) // 2)
        except:
            pass
    return None

def _popup(title, message, size=(450, 120)):
    layout = [
        [sg.Text(message)],
        [sg.Button("Okay", key="-OK-")],
    ]
    loc = _popup_location(size)
    win = sg.Window(title, layout, modal=True, resizable=True, size=size, location=loc, finalize=True)
    win.read(close=True)

def _ap_init_logging():
    global _ap_log_file_path, _ap_log_file_handle
    log_dir = os.path.join(_ap_config_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _ap_log_file_path = os.path.join(log_dir, f"ap_log_{timestamp}.txt")
    _ap_log_file_handle = open(_ap_log_file_path, 'w', encoding='utf-8', buffering=1)
    sys.stdout = _TeeStream(sys.stdout, _ap_log_file_handle)
    sys.stderr = _TeeStream(sys.stderr, _ap_log_file_handle)

class _TeeStream:
    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file
    def write(self, data):
        if self.original:
            self.original.write(data)
        if self.log_file and not self.log_file.closed:
            self.log_file.write(data)
            self.log_file.flush()
    def flush(self):
        if self.original:
            self.original.flush()
        if self.log_file and not self.log_file.closed:
            self.log_file.flush()

def _ap_log(window, message: str):
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        current = window["-LOG-"].get() if "-LOG-" in window.AllKeysDict else ""
        if current and not current.endswith('\n'):
            current += '\n'
        log_line = f"[{ts}] {message}\n"
        if "-LOG-" in window.AllKeysDict:
            window["-LOG-"].update(current + log_line, autoscroll=True)
        if _ap_log_file_handle and not _ap_log_file_handle.closed:
            _ap_log_file_handle.write(log_line)
            _ap_log_file_handle.flush()
    except Exception:
        pass

def _ap_close_logging():
    global _ap_log_file_handle, _ap_log_file_path
    if hasattr(sys.stdout, 'original'):
        sys.stdout = sys.stdout.original
    if hasattr(sys.stderr, 'original'):
        sys.stderr = sys.stderr.original
    if _ap_log_file_handle and not _ap_log_file_handle.closed:
        _ap_log_file_handle.close()

def _settings_summary(cfg):
    gain = f"Autogain: {'ON' if cfg.get('gain_enabled', True) else 'OFF'}  Target: {cfg.get('gain_ceiling', -20.0):.1f} LUFS"
    drc_settings = cfg
    drc = (
        f"DRC: {'ON' if cfg.get('drc_enabled', True) else 'OFF'}"
        f"  Thresh: {drc_settings.get('drc_threshold', -12):.0f}dB"
        f"  Ratio: {drc_settings.get('drc_ratio', 2.0):.1f}"
        f"  Attack: {drc_settings.get('drc_attack', 0.20):.2f}s"
        f"  Release: {drc_settings.get('drc_release', 1.0):.2f}s"
    )
    return gain, drc

def main():
    sg.theme('DarkBlue3')
    sg.set_options(font=("Arial", 12))
    _ap_init_logging()
    ap_cfg = _ap_load_config()

    __version__ = "0.0.0"
    _version_path = os.path.join(os.path.dirname(__file__), "VERSION")
    if getattr(sys, 'frozen', False):
        _version_path = os.path.join(sys._MEIPASS, "VERSION")
    if os.path.isfile(_version_path):
        with open(_version_path) as f:
            __version__ = f.read().strip()

    _files = []
    _selected = []
    _last_clicked_row = None
    _input_mode = "file"
    _processing = False
    _cancel = False
    _processed = 0
    _total = 0

    gain_label, drc_label = _settings_summary(ap_cfg)

    layout = [
        [sg.Text("AudioPatcher", font=("Arial", 16, "bold")),
         sg.Push(),
         sg.Text(f"v{__version__}", font=("Arial", 10), text_color="#8892a4")],
        [sg.HorizontalSeparator()],
        [sg.Text("Input:", font=("Arial", 12, "bold"))],
        [sg.Radio("File", "-INPUT-MODE-", key="-INPUT-FILE-", default=True, enable_events=True),
         sg.Radio("Folder", "-INPUT-MODE-", key="-INPUT-FOLDER-", enable_events=True),
         sg.InputText(key="-INPUT-PATH-", size=(50, 1), disabled=True),
         sg.Button("Browse", key="-BROWSE-", button_color=("white", "#375a7f"))],
        [sg.Text("Files:", font=("Arial", 12, "bold")), sg.Text("(none selected)",
                  key="-FILE-COUNT-", text_color="white")],
        [sg.Table(values=[], headings=["Process?", "Filename"], key="-TABLE-",
                  col_widths=[6, 70], cols_justification=['c', 'l'], display_row_numbers=False,
                  enable_events=True,
                  expand_x=True, expand_y=True, pad=(0, 0),
                  auto_size_columns=False, justification='left',
                  num_rows=8, text_color="white", background_color="#1e2d44",
                  header_font=("Arial", 10))],
        [sg.HorizontalSeparator()],
        [sg.Text("Output:", font=("Arial", 12, "bold"))],
        [sg.Checkbox("Overwrite original", key="-OUT-OVERWRITE-",
                     default=ap_cfg.get("overwrite", True), enable_events=True)],
        [sg.Text("Suffix:"), sg.InputText(key="-OUT-SUFFIX-", size=(20, 1),
                 default_text=ap_cfg.get("suffix", "_patched")), sg.Text("(used when overwrite is off)")],
        [sg.Checkbox("Custom output folder", key="-OUT-CUSTOM-",
                     default=False, enable_events=True),
         sg.InputText(key="-OUT-FOLDER-", size=(40, 1), disabled=True, visible=False),
         sg.FolderBrowse("Browse", key="-BROWSE-OUT-", visible=False, initial_folder=ap_cfg.get("output_dir", ""))],
        [sg.HorizontalSeparator()],
        [sg.Text("Settings:", font=("Arial", 12, "bold"))],
        [sg.Text(gain_label, key="-GAIN-LABEL-", font=("Arial", 10))],
        [sg.Text(drc_label, key="-DRC-LABEL-", font=("Arial", 10))],
        [sg.Button("Change Settings...", key="-SETTINGS-", button_color=("white", "#375a7f"))],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Button("Process Selected Files", key="-PROCESS-", disabled=True, size=(20, 1)),
             sg.Button("Cancel", key="-CANCEL-", disabled=True, size=(10, 1)),
             sg.Text("", key="-ETA-", size=(20, 1))],
            [sg.ProgressBar(100, orientation='h', size=(40, 15), key="-PROGRESS-BAR-"),
             sg.Text("0%", key="-PROGRESS-PCT-", size=(5, 1))],
            [sg.Text("", key="-PROGRESS-FILE-", size=(60, 1))],
        ], expand_x=True)],
        [sg.Text("Log:", font=("Arial", 10, "bold"))],
        [sg.Multiline(size=(85, 10), key="-LOG-", disabled=True, autoscroll=True,
                       expand_x=True, expand_y=True, background_color="#ffffff")],
        [sg.Button("Exit", key="-EXIT-")],
    ]

    _win_icon = _icon_path('AudioPatcher.ico')
    if not os.path.exists(_win_icon):
        _win_icon = _icon_path('AutoDub.ico')
    sw, _ = sg.Window.get_screen_size()
    _win_location = ((sw - 880) // 2, 0)
    window = sg.Window("AudioPatcher", layout, size=(880, 1000), location=_win_location,
                       resizable=True, finalize=True,
                       enable_close_attempted_event=True, icon=_win_icon)
    global _ap_win
    _ap_win = window

    _tree = window["-TABLE-"].Widget
    _tree.heading(0, anchor='w')
    _tree.heading(1, anchor='w')

    def _update_table():
        rows = []
        for i, fpath in enumerate(_files):
            mark = "[X]" if _selected[i] else "[   ]"
            rows.append([mark, os.path.basename(fpath)])
        window["-TABLE-"].update(values=rows, select_rows=[])
        _tree = window["-TABLE-"].Widget
        for item in _tree.get_children():
            _tree.item(item, tags=[])
        if _last_clicked_row is not None:
            items = _tree.get_children()
            if 0 <= _last_clicked_row < len(items):
                _tree.tag_configure('hl', foreground='white', background='#375a7f')
                _tree.item(items[_last_clicked_row], tags=['hl'])
        _tree.heading(0, anchor='w')
        _tree.heading(1, anchor='w')
        sel = sum(1 for s in _selected if s)
        window["-FILE-COUNT-"].update(
            f"({sel}/{len(_files)} selected)" if _files else "(none selected)")
        window["-PROCESS-"].update(disabled=sel == 0 or _processing)

    def _build_output_path(fpath):
        if ap_cfg.get("overwrite", True):
            return fpath
        base, _ = os.path.splitext(fpath)
        suffix = ap_cfg.get("suffix", "_patched")
        return base + suffix + ".mp4"

    def _scan_folder(path):
        results = []
        try:
            for entry in sorted(os.listdir(path)):
                if entry.lower().endswith('.mp4'):
                    full = os.path.join(path, entry)
                    if os.path.isfile(full):
                        results.append(full)
        except Exception as e:
            _ap_log(window, f"Error scanning folder: {e}")
        return results

    _ap_log(window, "Application started")

    while True:
        event, values = window.read()
        if event in (sg.WINDOW_CLOSE_ATTEMPTED_EVENT, "-EXIT-"):
            if _processing:
                _popup("Info", "Processing in progress. Cancel first before exiting.")
                continue
            break
        if event == sg.WINDOW_CLOSED:
            break

        if event == "-INPUT-FILE-":
            _input_mode = "file"

        if event == "-INPUT-FOLDER-":
            _input_mode = "folder"

        if event == "-BROWSE-":
            if _input_mode == "file":
                path = sg.filedialog.askopenfilename(
                    filetypes=[("MP4 Files", "*.mp4")],
                    initialdir=ap_cfg.get("input_dir", ""),
                    parent=window.TKroot
                )
                if path:
                    ap_cfg["input_dir"] = os.path.dirname(path)
                    window["-INPUT-PATH-"].update(path)
                    _files = [path]
                    _selected = [True]
                    _last_clicked_row = None
                    _update_table()
                    _ap_log(window, f"Selected file: {path}")
            else:
                path = sg.filedialog.askdirectory(
                    initialdir=ap_cfg.get("input_dir", ""),
                    parent=window.TKroot
                )
                if path:
                    ap_cfg["input_dir"] = path
                    window["-INPUT-PATH-"].update(path)
                    _files = _scan_folder(path)
                    _selected = [True] * len(_files)
                    _last_clicked_row = None
                    _update_table()
                    _ap_log(window, f"Selected folder: {path} ({len(_files)} MP4 files)")

        if event == "-TABLE-":
            sel = values["-TABLE-"]
            if sel and len(sel) > 0:
                row = sel[0]
                if row < len(_selected):
                    _selected[row] = not _selected[row]
                    _last_clicked_row = row
                    _update_table()

        if event == "-OUT-OVERWRITE-":
            ap_cfg["overwrite"] = values["-OUT-OVERWRITE-"]

        if event == "-OUT-CUSTOM-":
            visible = values["-OUT-CUSTOM-"]
            window["-OUT-FOLDER-"].update(visible=visible)
            window["-BROWSE-OUT-"].update(visible=visible)

        if event == "-SETTINGS-":
            settings_layout = [
                [sg.Text("Autogain", font=("Arial", 10, "bold"))],
                [sg.Checkbox("Enable autogain", key="-CFG-GAIN-ENABLE-",
                             default=ap_cfg.get("gain_enabled", True))],
                [sg.Text("Target loudness (LUFS):"), sg.Slider(range=(-30, -5),
                        default_value=ap_cfg.get("gain_ceiling", -20.0),
                        key="-CFG-GAIN-CEILING-", orientation="h", size=(30, 10), resolution=0.5)],
                [sg.HorizontalSeparator()],
                [sg.Text("DRC", font=("Arial", 10, "bold"))],
                [sg.Checkbox("Enable DRC", key="-CFG-DRC-ENABLE-",
                             default=ap_cfg.get("drc_enabled", True))],
                [sg.Text("Threshold (dB):"), sg.Slider(range=(-30, 0),
                        default_value=ap_cfg.get("drc_threshold", -12.0),
                        key="-CFG-DRC-THRESHOLD-", orientation="h", size=(30, 10), resolution=0.5)],
                [sg.Text("Ratio:"), sg.Slider(range=(1.0, 10.0),
                        default_value=ap_cfg.get("drc_ratio", 2.0),
                        key="-CFG-DRC-RATIO-", orientation="h", size=(30, 10), resolution=0.5)],
                [sg.Text("Attack (s):"), sg.Slider(range=(0.01, 1.0),
                        default_value=ap_cfg.get("drc_attack", 0.20),
                        key="-CFG-DRC-ATTACK-", orientation="h", size=(30, 10), resolution=0.01)],
                [sg.Text("Release (s):"), sg.Slider(range=(0.05, 5.0),
                        default_value=ap_cfg.get("drc_release", 1.0),
                        key="-CFG-DRC-RELEASE-", orientation="h", size=(30, 10), resolution=0.05)],
                [sg.HorizontalSeparator()],
                [sg.Button("Restore settings for WTD", key="-CFG-RESTORE-")],
                [sg.Button("Save", key="-CFG-SAVE-"), sg.Button("Cancel", key="-CFG-CANCEL-")],
            ]
            _set_icon = _icon_path('AudioPatcher.ico')
            if not os.path.exists(_set_icon):
                _set_icon = _icon_path('AutoDub.ico')
            set_win = sg.Window("Audio Settings", settings_layout, modal=True, resizable=True,
                                size=(550, 480), location=_popup_location((550, 480)),
                                icon=_set_icon)
            while True:
                sev, svals = set_win.read()
                if sev in (sg.WINDOW_CLOSED, "-CFG-CANCEL-"):
                    break
                if sev == "-CFG-SAVE-":
                    ap_cfg["gain_enabled"] = svals.get("-CFG-GAIN-ENABLE-", True)
                    ap_cfg["gain_ceiling"] = float(svals.get("-CFG-GAIN-CEILING-", -20.0))
                    ap_cfg["drc_enabled"] = svals.get("-CFG-DRC-ENABLE-", True)
                    ap_cfg["drc_threshold"] = float(svals.get("-CFG-DRC-THRESHOLD-", -12.0))
                    ap_cfg["drc_ratio"] = float(svals.get("-CFG-DRC-RATIO-", 2.0))
                    ap_cfg["drc_attack"] = float(svals.get("-CFG-DRC-ATTACK-", 0.20))
                    ap_cfg["drc_release"] = float(svals.get("-CFG-DRC-RELEASE-", 1.0))
                    gain_label, drc_label = _settings_summary(ap_cfg)
                    window["-GAIN-LABEL-"].update(gain_label)
                    window["-DRC-LABEL-"].update(drc_label)
                    _ap_save_config(ap_cfg)
                    _ap_log(window, "Settings saved")
                    break
                if sev == "-CFG-RESTORE-":
                    defaults = dict(_DEFAULT_AP_CONFIG)
                    ap_cfg["gain_enabled"] = defaults["gain_enabled"]
                    ap_cfg["gain_ceiling"] = defaults["gain_ceiling"]
                    ap_cfg["drc_enabled"] = defaults["drc_enabled"]
                    ap_cfg["drc_threshold"] = defaults["drc_threshold"]
                    ap_cfg["drc_ratio"] = defaults["drc_ratio"]
                    ap_cfg["drc_attack"] = defaults["drc_attack"]
                    ap_cfg["drc_release"] = defaults["drc_release"]
                    set_win["-CFG-GAIN-ENABLE-"].update(defaults["gain_enabled"])
                    set_win["-CFG-GAIN-CEILING-"].update(defaults["gain_ceiling"])
                    set_win["-CFG-DRC-ENABLE-"].update(defaults["drc_enabled"])
                    set_win["-CFG-DRC-THRESHOLD-"].update(defaults["drc_threshold"])
                    set_win["-CFG-DRC-RATIO-"].update(defaults["drc_ratio"])
                    set_win["-CFG-DRC-ATTACK-"].update(defaults["drc_attack"])
                    set_win["-CFG-DRC-RELEASE-"].update(defaults["drc_release"])
                    gain_label, drc_label = _settings_summary(ap_cfg)
                    window["-GAIN-LABEL-"].update(gain_label)
                    window["-DRC-LABEL-"].update(drc_label)
                    _ap_save_config(ap_cfg)
                    _ap_log(window, "Settings restored to defaults")
            set_win.close()

        if event == "-PROCESS-":
            to_process = [f for i, f in enumerate(_files) if _selected[i]]
            if not to_process or _processing:
                continue
            _cancel = False
            _processing = True
            _processed = 0
            _total = len(to_process)
            _overwrite_response = None
            _overwrite_ready = threading.Event()
            window["-PROCESS-"].update(disabled=True)
            window["-CANCEL-"].update(disabled=False)
            window["-PROGRESS-BAR-"].update(0)
            window["-PROGRESS-PCT-"].update("0%")
            overwrite = ap_cfg.get("overwrite", True)
            suffix = ap_cfg.get("suffix", "_patched")
            custom_folder = values.get("-OUT-CUSTOM-", False)
            out_folder = values.get("-OUT-FOLDER-", "").strip()
            generator = WTDDubGenerator()

            def worker():
                nonlocal _processed, _overwrite_response, _overwrite_ready
                times = deque(maxlen=5)
                ok_count = 0
                fail_count = 0
                _overwrite_all = False
                for i, fpath in enumerate(to_process):
                    if _cancel:
                        window.write_event_value("-CANCELLED-", None)
                        return
                    window.write_event_value("-FILE-START-", (i, _total, fpath))
                    t0 = time.perf_counter()
                    if overwrite:
                        out_path = fpath
                    else:
                        base, _ = os.path.splitext(fpath)
                        out_path = base + suffix + ".mp4"
                    if custom_folder and out_folder:
                        os.makedirs(out_folder, exist_ok=True)
                        out_path = os.path.join(out_folder, os.path.basename(out_path))
                    if not overwrite and not _overwrite_all and os.path.exists(out_path):
                        window.write_event_value("-PROMPT-OVERWRITE-", (i, fpath, out_path))
                        _overwrite_ready.wait()
                        _overwrite_ready.clear()
                        if _overwrite_response == "No":
                            _ap_log(window, f"  - Skipped {os.path.basename(out_path)} (file exists)")
                            continue
                        elif _overwrite_response == "Yes to all":
                            _overwrite_all = True
                    generator.video_file = fpath
                    err_msg = None
                    try:
                        ok = generator.process_audio_export(
                            output_path=out_path,
                            gain_enabled=ap_cfg.get("gain_enabled", True),
                            gain_ceiling_db=ap_cfg.get("gain_ceiling", -20.0),
                            drc_enabled=ap_cfg.get("drc_enabled", True),
                            drc_threshold=ap_cfg.get("drc_threshold", -12.0),
                            drc_ratio=ap_cfg.get("drc_ratio", 2.0),
                            drc_attack=ap_cfg.get("drc_attack", 0.20),
                            drc_release=ap_cfg.get("drc_release", 1.0),
                        )
                        if not ok:
                            err_msg = generator._last_error
                    except Exception as e:
                        import traceback
                        err_msg = f"{e}\n{traceback.format_exc()}"
                        _ap_log(window, f"Export crashed: {e}\n{traceback.format_exc()}")
                        ok = False
                    elapsed = time.perf_counter() - t0
                    times.append(elapsed)
                    avg = sum(times) / len(times)
                    remaining = avg * (_total - i - 1)
                    if ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                    _processed += 1
                    window.write_event_value("-FILE-DONE-", (i, _total, fpath, out_path, ok, remaining, err_msg))
                window.write_event_value("-ALL-DONE-", (ok_count, fail_count))

            threading.Thread(target=worker, daemon=True).start()

        if event == "-CANCEL-":
            if _processing:
                _cancel = True
                _ap_log(window, "Cancelling... (finishing current file)")
                window["-CANCEL-"].update(disabled=True)

        if event == "-PROMPT-OVERWRITE-":
            i, fpath, out_path = values[event]
            layout = [
                [sg.Text(f"'{os.path.basename(out_path)}' already exists.")],
                [sg.Text("Overwrite?")],
                [sg.Button("No"), sg.Button("Yes"), sg.Button("Yes to all")],
            ]
            win = sg.Window("File exists", layout, modal=True, disable_close=True,
                            icon=_win_icon, location=_popup_location((350, 120)))
            ev, _ = win.read()
            win.close()
            _overwrite_response = ev
            _overwrite_ready.set()
            _ap_log(window, f"{os.path.basename(out_path)} exists — {ev}")

        if event == "-FILE-START-":
            i, total, fpath = values[event]
            pct = int((i / total) * 100) if total > 0 else 0
            window["-PROGRESS-BAR-"].update(pct)
            window["-PROGRESS-PCT-"].update(f"{pct}%")
            window["-PROGRESS-FILE-"].update(f"File {i+1}/{total}  {os.path.basename(fpath)}")
            _ap_log(window, f"Processing {os.path.basename(fpath)}...")

        if event == "-FILE-DONE-":
            i, total, fpath, out_path, ok, remaining, err_msg = values[event]
            pct = int(((i + 1) / total) * 100) if total > 0 else 0
            window["-PROGRESS-BAR-"].update(pct)
            window["-PROGRESS-PCT-"].update(f"{pct}%")
            if remaining > 0:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                window["-ETA-"].update(f"ETA: {mins}:{secs:02d}")
            else:
                window["-ETA-"].update("")
            out_name = os.path.basename(out_path)
            out_size = os.path.getsize(out_path) / (1024 * 1024) if ok and os.path.exists(out_path) else 0
            if ok:
                _ap_log(window, f"  ✓ {out_name} ({out_size:.1f} MB)")
            else:
                _ap_log(window, f"  ✗ {os.path.basename(fpath)} failed: {err_msg or 'unknown error'}")

        if event == "-ALL-DONE-":
            ok_count, fail_count = values[event]
            _processing = False
            window["-PROCESS-"].update(disabled=len(_files) == 0)
            window["-CANCEL-"].update(disabled=True)
            window["-PROGRESS-BAR-"].update(100)
            window["-PROGRESS-PCT-"].update("100%")
            window["-PROGRESS-FILE-"].update("")
            window["-ETA-"].update("")
            summary = f"Done — {ok_count} succeeded"
            if fail_count:
                summary += f", {fail_count} failed"
            _ap_log(window, summary)
            _popup("Complete", summary)

        if event == "-CANCELLED-":
            _processing = False
            window["-PROCESS-"].update(disabled=len(_files) == 0)
            window["-CANCEL-"].update(disabled=True)
            window["-PROGRESS-FILE-"].update("")
            window["-ETA-"].update("")
            _ap_log(window, "Cancelled by user")

    _ap_log(window, "Application exited")
    _ap_close_logging()
    window.close()

if __name__ == "__main__":
    main()
