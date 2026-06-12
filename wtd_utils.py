import os
import sys
import json
import io
from datetime import datetime

# ── config ──────────────────────────────────────────────────────────────

def _config_dir() -> str:
    exe = getattr(sys, 'frozen', False)
    return os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(_config_dir(), "config.json")

_DEFAULT_CONFIG = {
    "main": {
        "input_dir": "",
        "output_dir": "",
        "default_model": "tiny",
        "rename_mp4": True,
        "mpv_path": "",
        "use_cuda": False,
        "cuda_fallback_cpu": True,
        "punctuate_srt": True,
        "log_cleanup_enabled": True,
        "log_cleanup_days": 30,
    },
    "gender_fixer": {
        "input_dir": "",
        "overwrite_original": False,
    },
    "file_renamer": {
        "default_folder": "",
        "remember_last": False,
    },
    "log_path": "logs",
}

def _migrate_config(cfg: dict) -> dict:
    if "main" in cfg:
        return cfg
    old = cfg.copy()
    cfg = dict(_DEFAULT_CONFIG)
    cfg["main"]["input_dir"] = old.get("input_dir", "")
    cfg["main"]["output_dir"] = old.get("output_dir", "")
    cfg["main"]["default_model"] = old.get("default_model", "large")
    cfg["main"]["rename_mp4"] = old.get("rename_mp4", True)
    cfg["main"]["mpv_path"] = old.get("mpv_path", "")
    cfg["main"]["use_cuda"] = old.get("use_cuda", True)
    cfg["main"]["suppress_silence"] = old.get("suppress_silence", True)
    cfg["main"]["punctuate_srt"] = old.get("punctuate_srt", True)
    cfg["main"]["cuda_fallback_cpu"] = old.get("cuda_fallback_cpu", False)
    cfg["main"]["log_cleanup_enabled"] = old.get("log_cleanup_enabled", True)
    cfg["main"]["log_cleanup_days"] = old.get("log_cleanup_days", 30)
    cfg["log_path"] = old.get("log_path", "logs")
    gf_path = os.path.join(_config_dir(), "gender_fixer_config.json")
    try:
        with open(gf_path, 'r') as f:
            gf_old = json.load(f)
        cfg["gender_fixer"]["input_dir"] = gf_old.get("input_dir", "")
        cfg["gender_fixer"]["overwrite_original"] = gf_old.get("overwrite_original", False)
        os.remove(gf_path)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    save_config(cfg)
    return cfg

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, 'r') as f:
            return _migrate_config(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        save_config(dict(_DEFAULT_CONFIG))
        return dict(_DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

_main_win = None

def set_main_win(win):
    global _main_win
    _main_win = win

def _popup_location(popup_size):
    global _main_win
    if _main_win is not None:
        try:
            mx, my = _main_win.current_location()
            mw, mh = _main_win.size
            pw, ph = popup_size
            return (mx + (mw - pw) // 2, my + (mh - ph) // 2)
        except:
            pass
    return None

# ── popups ──────────────────────────────────────────────────────────────

def _popup(title, message, size=(400, 120)):
    import PySimpleGUI as sg
    layout = [
        [sg.Text(message)],
        [sg.Button("Okay", key="-OK-")],
    ]
    loc = _popup_location(size)
    win = sg.Window(title, layout, modal=True, resizable=True, size=size, location=loc, finalize=True)
    win.read(close=True)

def _popup_yes_no(message, title="Confirm", size=(400, 120)):
    import PySimpleGUI as sg
    loc = _popup_location(size)
    return sg.popup_yes_no(message, title=title, location=loc)


def _popup_yes_no_iterate(message, title="Confirm", size=(450, 140)):
    """Popup with Yes, No, and 'Add Higher Number' buttons. Returns 'Yes', 'No', or 'Iterate'."""
    import PySimpleGUI as sg
    loc = _popup_location(size)
    layout = [
        [sg.Text(message)],
        [sg.Button("Yes", key="-YES-", size=(12, 1)),
         sg.Button("No", key="-NO-", size=(12, 1)),
         sg.Button("Add Higher Number", key="-ITERATE-", size=(16, 1))],
    ]
    win = sg.Window(title, layout, modal=True, resizable=True, size=size, location=loc, finalize=True)
    while True:
        ev, _ = win.read()
        if ev in (sg.WINDOW_CLOSED, "-NO-"):
            win.close()
            return "No"
        if ev == "-YES-":
            win.close()
            return "Yes"
        if ev == "-ITERATE-":
            win.close()
            return "Iterate"

# ── logging ─────────────────────────────────────────────────────────────

_log_file_path = None
_log_file_handle = None

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

def _init_logging(log_path: str = "logs"):
    global _log_file_path, _log_file_handle
    log_dir = os.path.join(_config_dir(), log_path) if not os.path.isabs(log_path) else log_path
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file_path = os.path.join(log_dir, f"wtd_log_{timestamp}.txt")
    _log_file_handle = open(_log_file_path, 'w', encoding='utf-8', buffering=1)
    sys.stdout = _TeeStream(sys.stdout, _log_file_handle)
    sys.stderr = _TeeStream(sys.stderr, _log_file_handle)

def _close_logging():
    global _log_file_handle, _log_file_path
    if hasattr(sys.stdout, 'original'):
        sys.stdout = sys.stdout.original
    if hasattr(sys.stderr, 'original'):
        sys.stderr = sys.stderr.original
    if _log_file_handle and not _log_file_handle.closed:
        _log_file_handle.close()

def _log(window, message: str):
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        current = window["-DEBUG-LOG-"].get()
        if current and not current.endswith('\n'):
            current += '\n'
        log_line = f"[{ts}] {message}\n"
        window["-DEBUG-LOG-"].update(current + log_line)
        if _log_file_handle and not _log_file_handle.closed:
            _log_file_handle.write(log_line)
            _log_file_handle.flush()
    except Exception:
        pass

# ── mpv helpers ─────────────────────────────────────────────────────────

def _resolve_mpv_path(cfg: dict = None) -> str:
    import shutil
    raw = (cfg or {}).get("mpv_path", "").strip()
    if raw:
        if os.path.isdir(raw):
            for exe in ("mpv.exe", "mpv.com"):
                candidate = os.path.join(raw, exe)
                if os.path.exists(candidate):
                    return candidate
        elif os.path.isfile(raw):
            return raw
    found = shutil.which("mpv")
    if found:
        return found
    common = [
        os.path.expandvars(r"%LOCALAPPDATA%\mpv\mpv.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\mpv\mpv.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\mpv\mpv.exe"),
        r"C:\tools\mpv\mpv.exe",
    ]
    for p in common:
        if os.path.exists(p):
            return p
    return ""

def _find_mpv(cfg: dict = None) -> bool:
    return bool(_resolve_mpv_path(cfg))

# ── cleanup ─────────────────────────────────────────────────────────────

def _cleanup_old_logs(enabled: bool = True, days: int = 30, log_path: str = "logs"):
    if not enabled:
        return
    import datetime as dt_module, ctypes
    from ctypes import wintypes
    log_dir = os.path.join(_config_dir(), log_path) if not os.path.isabs(log_path) else log_path
    if not os.path.isdir(log_dir):
        return

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('wFunc', wintypes.UINT),
            ('pFrom', wintypes.LPCWSTR),
            ('pTo', wintypes.LPCWSTR),
            ('fFlags', wintypes.WORD),
            ('fAnyOperationsAborted', wintypes.BOOL),
            ('hNameMappings', wintypes.LPVOID),
            ('lpszProgressTitle', wintypes.LPCWSTR),
        ]

    cutoff = dt_module.datetime.now() - dt_module.timedelta(days=days)
    SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x04

    for entry in os.listdir(log_dir):
        fpath = os.path.join(log_dir, entry)
        if not os.path.isfile(fpath):
            continue
        mtime = dt_module.datetime.fromtimestamp(os.path.getmtime(fpath))
        if mtime < cutoff:
            buf = ctypes.create_unicode_buffer(fpath + '\0\0')
            op = SHFILEOPSTRUCTW(
                hwnd=0,
                wFunc=FO_DELETE,
                pFrom=ctypes.addressof(buf),
                pTo=None,
                fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT,
            )
            SHFileOperationW(ctypes.byref(op))

# ── SRT helpers ─────────────────────────────────────────────────────────

def _show_split_window(window, generator, split_idx, rebuild_fn, preview_fn, speaker_col_count, popup_fn):
    import PySimpleGUI as sg
    sub = generator.subtitles[split_idx]
    split_states = [False] * (len(sub.words) - 1)
    split_layout = [
        [sg.Text(f"Split Line {sub.index}:", font=("Arial", 12, "bold"))],
        [sg.Text(f"\"{sub.text}\"", font=("Arial", 10))],
        [sg.HorizontalSeparator()],
    ]
    word_rows = []
    for wi, (wt, ws, we) in enumerate(sub.words):
        word_rows.append([sg.Text(f"  {wt}   ({ws:.2f}s - {we:.2f}s)", font=("Arial", 10))])
        if wi < len(sub.words) - 1:
            word_rows.append([
                sg.Checkbox("Split here", key=f"-SPLIT-CK-{wi}-", font=("Arial", 10), enable_events=True)
            ])
    split_layout += [[sg.Column(word_rows, size=(500, 300), scrollable=True, vertical_scroll_only=True, expand_y=False, key="-SPLIT-WORD-COL-")]]
    split_layout += [
        [sg.HorizontalSeparator()],
        [sg.Text("Preview:", font=("Arial", 10, "bold"))],
        [sg.Multiline(size=(60, 8), key="-SPLIT-PREVIEW-", disabled=True, font=("Arial", 10))],
        [sg.Button("Apply", size=(12, 1), font=("Arial", 10)), sg.Button("Cancel", size=(12, 1), font=("Arial", 10))],
    ]
    split_win = sg.Window(f"Split Line {sub.index}", split_layout, modal=True, finalize=True, resizable=True, size=(600, 600))
    split_win.refresh(); split_win["-SPLIT-WORD-COL-"].contents_changed()

    def _build_preview(words, states):
        parts, cur = [], []
        for wi, w in enumerate(words):
            cur.append(w)
            if wi < len(states) and states[wi]:
                parts.append(cur)
                cur = []
        if cur:
            parts.append(cur)
        lines = [f"  {pi+1}. [{p[0][1]:.2f}-{p[-1][2]:.2f}] {_join_words(p)}" for pi, p in enumerate(parts)]
        split_win["-SPLIT-PREVIEW-"].update("\n".join(lines) if lines else "")

    _build_preview(sub.words, split_states)
    result = False
    while True:
        sev, svals = split_win.read()
        if sev in (sg.WINDOW_CLOSED, "Cancel"):
            break
        if sev == "Apply":
            split_at = [i for i in range(len(sub.words) - 1) if svals.get(f"-SPLIT-CK-{i}-", False)]
            if not split_at:
                popup_fn("Info", "No split points selected.")
                continue
            sr_run = speaker_col_count
            old_names = {}
            for j in range(len(generator.subtitles)):
                k = f"-SP-{sr_run}-{j}-"
                if k in window.AllKeysDict:
                    n = window[k].get().strip()
                    if n:
                        old_names[j] = n
            r = generator.split_line(split_idx, split_at)
            if r:
                shift = r - 1
                restore = {}
                for old_i, name in old_names.items():
                    if old_i < split_idx:
                        restore[old_i] = name
                    elif old_i > split_idx:
                        restore[old_i + shift] = name
                rebuild_fn(restore_speakers=restore)
                preview_fn(force=True)
                result = True
                break
            else:
                popup_fn("Error", "Could not split line.")
                break
        if sev and sev.startswith("-SPLIT-CK-"):
            cur_states = [svals.get(f"-SPLIT-CK-{j}-", False) for j in range(len(sub.words) - 1)]
            _build_preview(sub.words, cur_states)
    split_win.close()
    return result

def _add_periods_to_srt(srt_text: str, dub_indices: set, split_indices: set = None) -> str:
    lines = srt_text.split("\n")
    sub_idx = -1
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.isdigit():
            sub_idx = int(stripped) - 1
            result.append(line)
            continue
        if "-->" in stripped or not stripped:
            result.append(line)
            continue
        if sub_idx + 2 in dub_indices:
            result.append(line)
            continue
        if split_indices and sub_idx in split_indices:
            result.append(line)
            continue
        if stripped.startswith("["):
            result.append(line)
            continue
        if stripped[-1:] in (".", "!", "?", "]", ":", ")", "\"", "-", ",", ";"):
            result.append(line)
            continue
        first_alpha = next((c for c in stripped if c.isalpha()), None)
        if first_alpha and first_alpha.isupper():
            line += "."
        result.append(line)
    return "\n".join(result)


def _join_words(words):
    if not words:
        return ""
    result = words[0][0] if isinstance(words[0], tuple) else words[0]
    for w in words[1:]:
        text = w[0] if isinstance(w, tuple) else w
        if text and text[0] in '.,!?;:\'")':
            result += text
        else:
            result += ' ' + text
    return result
