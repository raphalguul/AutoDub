import PySimpleGUI as sg
import os
import sys
import zipfile
import tempfile
import shutil
import json
import time

SUBTITLES_FOLDER = "Subtitles"

_GF_CONFIG_DIR = None

def _icon_path(name):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'Icons', name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Icons', name)

def _gf_config_dir():
    global _GF_CONFIG_DIR
    if _GF_CONFIG_DIR is None:
        exe = getattr(sys, 'frozen', False)
        _GF_CONFIG_DIR = os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))
    return _GF_CONFIG_DIR

def _gf_load_config():
    path = os.path.join(_gf_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
            return cfg.get("gender_fixer", {"input_dir": "", "overwrite_original": False}).copy()
    except:
        return {"input_dir": "", "overwrite_original": False}

def _gf_save_config(gf_cfg):
    path = os.path.join(_gf_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
    except:
        cfg = {"main": {}, "gender_fixer": {}, "log_path": "logs"}
    cfg["gender_fixer"] = gf_cfg
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)

def extract_zip(zip_path: str) -> str:
    dest = tempfile.mkdtemp(prefix="zipfix_")
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(dest)
    return dest


def repack_zip(zip_path: str, source_dir: str):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(source_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, source_dir)
                z.write(full, rel)


def detect_gender(filepath: str) -> str:
    """Return 'male', 'female', or '' if neither found."""
    t0 = time.perf_counter()
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    has_male = '[male_dub]' in content
    has_female = '[female_dub]' in content
    if has_male and not has_female:
        result = 'male'
    elif has_female and not has_male:
        result = 'female'
    else:
        result = ''
    _gf_logv(_gf_win, f"detect_gender({os.path.basename(filepath)}) -> {result}", t0)
    return result


def set_gender(filepath: str, gender: str, old_gender: str):
    """Swap gender marker in file. old_gender is the current one to replace."""
    t0 = time.perf_counter()
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    if old_gender == 'male':
        content = content.replace('[male_dub]', '[female_dub]')
    elif old_gender == 'female':
        content = content.replace('[female_dub]', '[male_dub]')
    else:
        if gender == 'male':
            content = content + '\n[male_dub]\n'
        elif gender == 'female':
            content = content + '\n[female_dub]\n'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    _gf_logv(_gf_win, f"set_gender({os.path.basename(filepath)}, {old_gender}->{gender})", t0)


def detect_speaker(filepath: str) -> str:
    """Return the last speaker name that appears before the first dub entry."""
    t0 = time.perf_counter()
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    blocks = content.strip().split('\n\n')
    last_speaker = ''
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        text_lines = lines[2:]
        if any('[male_dub]' in l or '[female_dub]' in l for l in text_lines):
            for l in text_lines:
                if '[male_dub]' in l or '[female_dub]' in l:
                    if ':' in l.split('[male_dub]')[0].split('[female_dub]')[0]:
                        speaker = l.split(':')[0].strip()
                        if speaker:
                            last_speaker = speaker
                    break
                if ':' in l:
                    speaker = l.split(':')[0].strip()
                    if speaker:
                        last_speaker = speaker
            break
        first_text = text_lines[0].strip()
        if ':' in first_text:
            speaker = first_text.split(':')[0].strip()
            if speaker:
                last_speaker = speaker
    _gf_logv(_gf_win, f"detect_speaker({os.path.basename(filepath)}) -> '{last_speaker}'", t0)
    return last_speaker


import sys
import io
from datetime import datetime

_gf_log_file_path = None
_gf_log_file_handle = None
_gf_win = None

def _popup_location(popup_size):
    """Return (x, y) to center a popup over _gf_win, or None."""
    global _gf_win
    if _gf_win is not None:
        try:
            mx, my = _gf_win.current_location()
            mw, mh = _gf_win.size
            pw, ph = popup_size
            return (mx + (mw - pw) // 2, my + (mh - ph) // 2)
        except:
            pass
    return None

def _popup(title, message, size=(450, 120)):
    """Custom popup with resizable window and specified size"""
    layout = [
        [sg.Text(message)],
        [sg.Button("Okay", key="-OK-")],
    ]
    loc = _popup_location(size)
    win = sg.Window(title, layout, modal=True, resizable=True, size=size, location=loc, finalize=True)
    win.read(close=True)

def _popup_yes_no(message, title="Confirm", yes_text="Yes", no_text="No", cancel_text=None, size=(400, 120)):
    """Custom yes/no popup with optional cancel button"""
    buttons = [sg.Button(yes_text, key="-YES-"), sg.Button(no_text, key="-NO-")]
    if cancel_text is not None:
        buttons.append(sg.Button(cancel_text, key="-CANCEL-"))
    layout = [
        [sg.Text(message)],
        buttons,
    ]
    loc = _popup_location(size)
    win = sg.Window(title, layout, modal=True, resizable=True, size=size, location=loc, finalize=True)
    event, _ = win.read(close=True)
    return event

def _gf_init_logging():
    """Initialize logging to file with timestamp-based filename"""
    global _gf_log_file_path, _gf_log_file_handle
    import os
    exe = getattr(sys, 'frozen', False)
    app_dir = os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(app_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _gf_log_file_path = os.path.join(log_dir, f"gf_log_{timestamp}.txt")
    _gf_log_file_handle = open(_gf_log_file_path, 'w', encoding='utf-8', buffering=1)
    sys.stdout = _TeeStream(sys.stdout, _gf_log_file_handle)
    sys.stderr = _TeeStream(sys.stderr, _gf_log_file_handle)

class _TeeStream:
    """Write to both original stream and log file"""
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

def _gf_log(window, message: str):
    """Add timestamped message to debug log and file"""
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        current = window["-DEBUG-LOG-"].get() if "-DEBUG-LOG-" in window.AllKeysDict else ""
        if current and not current.endswith('\n'):
            current += '\n'
        log_line = f"[{ts}] {message}\n"
        if "-DEBUG-LOG-" in window.AllKeysDict:
            window["-DEBUG-LOG-"].update(current + log_line)
        if _gf_log_file_handle and not _gf_log_file_handle.closed:
            _gf_log_file_handle.write(log_line)
            _gf_log_file_handle.flush()
    except Exception:
        pass

def _gf_logv(window, label, start=None):
    if start is not None:
        elapsed = time.perf_counter() - start
        _gf_log(window, f"[{elapsed*1000:7.1f}ms] {label}")
    else:
        _gf_log(window, label)

def _gf_close_logging():
    """Close log file handle"""
    global _gf_log_file_handle, _gf_log_file_path
    if hasattr(sys.stdout, 'original'):
        sys.stdout = sys.stdout.original
    if hasattr(sys.stderr, 'original'):
        sys.stderr = sys.stderr.original
    if _gf_log_file_handle and not _gf_log_file_handle.closed:
        _gf_log_file_handle.close()

def main():
    sg.theme('DarkBlue3')
    sg.set_options(font=("Arial", 12))
    _gf_init_logging()
    gf_config = _gf_load_config()
    
    zip_path = ""
    extract_dir = None
    srt_files = []
    source_type = None  # 'zip' or 'folder'
    _selected_idx = None
    _selected_display_idx = None
    _selected_button_key = None
    _dirty = False
    _editing_idx = None
    _show_female_only = False
    _show_no_speaker_only = False
    _display_rows = []  # list of [actual_idx, fpath, fname, gender, speaker]
    _subs_source = None  # actual SRT source path (root or Subtitles subfolder)

    layout = [
        [sg.Text("GenderFixer", font=("Arial", 14, "bold"))],
        [sg.Text("Source:"), sg.InputText(key="-SOURCE-", size=(55, 1), disabled=True), sg.Button("Browse Zip", key="-BROWSE-ZIP-"), sg.Button("Browse Folder", key="-BROWSE-FOLDER-")],
        [sg.Button("Update Table", key="-UPDATE-TABLE-"), sg.Button("Only Show Dubs Without Speakers", key="-SHOW-NO-SPEAKER-"), sg.Button("Hide Male Dubs", key="-SHOW-FEMALE-ONLY-")],
        [sg.Table(values=[], headings=["File", "M", "F", "Speaker"], key="-TABLE-",
                  col_widths=[38, 3, 3, 15], display_row_numbers=False,
                  enable_events=True, enable_click_events=True,
                  expand_x=True, expand_y=True, pad=(0, 0),
                  auto_size_columns=False, justification='center',
                  num_rows=30, text_color="white", background_color="#283b5b",
                  header_font=("Arial", 12, "bold"),
                  selected_row_colors=("white", "#375a7f")),
         sg.Column([
            [sg.Text("Preview", font=("Arial", 12, "bold"))],
            [sg.Multiline(size=(40, 10), key="-PREVIEW-", disabled=True, autoscroll=True, expand_y=True)],
            [sg.Button("Edit", key="-EDIT-", disabled=True),
             sg.Button("Confirm", key="-SAVE-EDIT-", visible=False),
             sg.Button("Discard", key="-DISCARD-EDIT-", visible=False)],
        ], expand_y=True)],
        [sg.Button("Make Changes Permanent", key="-SAVE-", disabled=True), sg.Button("Settings", key="-SETTINGS-"), sg.Button("Close", key="-CLOSE-")],
    ]

    window = sg.Window("GenderFixer", layout, size=(1100, 800), resizable=False, finalize=True, enable_close_attempted_event=True, icon=_icon_path('GenderFixer.ico'))
    global _gf_win
    _gf_win = window

    _gf_log(window, "Application started")

    def update_button_states():
        """Update Close button color based on unsaved changes"""
        if _dirty:
            window["-CLOSE-"].update(button_color=("white", "red"))
        else:
            window["-CLOSE-"].update(button_color=('#FFFFFF', '#283b5b'))
        
        window.TKroot.update_idletasks()

    # Initial button state update
    update_button_states()

    def _update_table():
        table_values = [
            [
                row[2],
                "[X]" if row[3] == 'male' else "[   ]",
                "[X]" if row[3] == 'female' else "[   ]",
                row[4],
            ]
            for row in _display_rows
        ]
        window["-TABLE-"].update(values=table_values)

    def _save_edit(win, idx, files):
        nonlocal _dirty
        """Save preview content to file and re-detect gender/speaker."""
        content = win["-PREVIEW-"].get()
        fpath, _ = files[idx]
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        gender = detect_gender(fpath)
        speaker = detect_speaker(fpath)
        if _selected_display_idx is not None and _selected_display_idx < len(_display_rows):
            _display_rows[_selected_display_idx][3] = gender
            _display_rows[_selected_display_idx][4] = speaker
            _update_table()
        _dirty = True
        win["-SAVE-"].update(disabled=False)
        update_button_states()

    def _enter_edit(win, idx):
        """Enter edit mode for the given file index."""
        win["-PREVIEW-"].update(disabled=False)
        win["-EDIT-"].update(visible=False)
        win["-SAVE-EDIT-"].update(visible=True)
        win["-DISCARD-EDIT-"].update(visible=True)
        nonlocal _editing_idx
        _editing_idx = idx

    def _exit_edit(win, idx, files):
        """Exit edit mode, save and restore radios."""
        _save_edit(win, idx, files)
        win["-PREVIEW-"].update(disabled=True)
        win["-SAVE-EDIT-"].update(visible=False)
        win["-DISCARD-EDIT-"].update(visible=False)
        win["-EDIT-"].update(visible=True)
        nonlocal _editing_idx
        _editing_idx = None

    def _discard_edit(win, idx, files):
        """Exit edit mode without saving, reload file and re-detect."""
        fpath, _ = files[idx]
        with open(fpath, 'r', encoding='utf-8') as f:
            win["-PREVIEW-"].update(f.read())
        gender = detect_gender(fpath)
        speaker = detect_speaker(fpath)
        if _selected_display_idx is not None and _selected_display_idx < len(_display_rows):
            _display_rows[_selected_display_idx][3] = gender
            _display_rows[_selected_display_idx][4] = speaker
            _update_table()
        win["-PREVIEW-"].update(disabled=True)
        win["-SAVE-EDIT-"].update(visible=False)
        win["-DISCARD-EDIT-"].update(visible=False)
        win["-EDIT-"].update(visible=True)
        nonlocal _editing_idx
        _editing_idx = None

    def build_table(files):
        nonlocal _display_rows
        t0 = time.perf_counter()
        _display_rows = []
        for i, (fpath, fname) in enumerate(files):
            gender = detect_gender(fpath)
            if _show_female_only and gender != 'female':
                continue
            if _show_no_speaker_only and detect_speaker(fpath):
                continue
            speaker = detect_speaker(fpath)
            _display_rows.append([i, fpath, fname, gender, speaker])
        _gf_logv(window, f"build_table: scanned {len(files)} files, {len(_display_rows)} displayed", t0)
        t1 = time.perf_counter()
        _update_table()
        _gf_logv(window, f"build_table: UI update done", t1)

    while True:
        event, values = window.read()
        if event in (sg.WINDOW_CLOSE_ATTEMPTED_EVENT, "-CLOSE-"):
            if _dirty:
                resp = _popup_yes_no("Unsaved changes will be lost. Close anyway?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
            break
        if event == sg.WINDOW_CLOSED:
            break

        if event == "-BROWSE-ZIP-":
            if _dirty or _editing_idx is not None:
                resp = _popup_yes_no("Discard changes and load a new zip?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
                if _editing_idx is not None:
                    _discard_edit(window, _editing_idx, srt_files)
            path = sg.filedialog.askopenfilename(
                filetypes=[("ZIP Files", "*.zip")],
                initialdir=gf_config.get("input_dir", ""),
                parent=window.TKroot
            )
            if path:
                _selected_idx = None
                _selected_display_idx = None
                _selected_button_key = None
                _editing_idx = None
                _dirty = False
                _subs_source = None
                update_button_states()
                window["-PREVIEW-"].update("")
                window["-EDIT-"].update(disabled=True, visible=True)
                window["-SAVE-EDIT-"].update(visible=False)
                window["-DISCARD-EDIT-"].update(visible=False)
                source_type = 'zip'
                window["-SOURCE-"].update(path)
                if extract_dir:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                t_extract = time.perf_counter()
                extract_dir = extract_zip(path)
                zip_size = os.path.getsize(path)
                _gf_logv(window, f"extracted zip ({zip_size/1024/1024:.1f}MB) to temp dir", t_extract)
                subs_dir = os.path.join(extract_dir, SUBTITLES_FOLDER)
                if not os.path.isdir(subs_dir):
                    _popup("Error", f"'{SUBTITLES_FOLDER}' folder not found inside the zip.")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    extract_dir = None
                    srt_files = []
                    continue
                srt_files = [(os.path.join(subs_dir, f), f) for f in sorted(os.listdir(subs_dir)) if f.lower().endswith('.srt')]
                if not srt_files:
                    _popup("Info", "No .srt files found in the Subtitles folder.")
                _gf_logv(window, f"found {len(srt_files)} .srt files in zip")
                build_table(srt_files)
                window["-SAVE-"].update(disabled=True, text="Make Changes Permanent")

        if event == "-BROWSE-FOLDER-":
            if _dirty or _editing_idx is not None:
                resp = _popup_yes_no("Discard changes and load a new folder?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
                if _editing_idx is not None:
                    _discard_edit(window, _editing_idx, srt_files)
            path = sg.filedialog.askdirectory(initialdir=gf_config.get("input_dir", ""), parent=window.TKroot)
            if path:
                _selected_idx = None
                _selected_display_idx = None
                _selected_button_key = None
                _editing_idx = None
                _dirty = False
                _subs_source = None
                update_button_states()
                window["-PREVIEW-"].update("")
                window["-EDIT-"].update(disabled=True, visible=True)
                window["-SAVE-EDIT-"].update(visible=False)
                window["-DISCARD-EDIT-"].update(visible=False)
                source_type = 'folder'
                _subs_source = path
                window["-SOURCE-"].update(path)
                if extract_dir:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                extract_dir = tempfile.mkdtemp(prefix="zipfix_")
                srt_files = []
                t_copy = time.perf_counter()
                for f in sorted(os.listdir(path)):
                    if f.lower().endswith('.srt'):
                        shutil.copy2(os.path.join(path, f), os.path.join(extract_dir, f))
                        srt_files.append((os.path.join(extract_dir, f), f))
                _gf_logv(window, f"copied {len(srt_files)} .srt files from root folder", t_copy)
                if not srt_files:
                    # Fallback: check case-insensitive "subtitles" subfolder
                    found = None
                    for entry in os.listdir(path):
                        if entry.lower() == "subtitles" and os.path.isdir(os.path.join(path, entry)):
                            found = os.path.join(path, entry)
                            break
                    if found:
                        t_copy = time.perf_counter()
                        for f in sorted(os.listdir(found)):
                            if f.lower().endswith('.srt'):
                                shutil.copy2(os.path.join(found, f), os.path.join(extract_dir, f))
                                srt_files.append((os.path.join(extract_dir, f), f))
                        _gf_logv(window, f"copied {len(srt_files)} .srt files from '{os.path.basename(found)}' subfolder", t_copy)
                        if srt_files:
                            _subs_source = found
                if not srt_files:
                    _popup("Info", "No .srt files found in the folder.")
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    extract_dir = None
                    srt_files = []
                    continue
                build_table(srt_files)
                window["-SAVE-"].update(disabled=True, text="Make Changes Permanent")

        if event == "-UPDATE-TABLE-":
            if _editing_idx is not None or _dirty:
                resp = _popup_yes_no("Discard changes and rebuild table?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
                if _editing_idx is not None:
                    _discard_edit(window, _editing_idx, srt_files)
            _dirty = False
            build_table(srt_files)
            _selected_idx = None
            _selected_display_idx = None
            _selected_button_key = None
            _editing_idx = None
            window["-PREVIEW-"].update("")
            window["-EDIT-"].update(disabled=True, visible=True)
            window["-SAVE-EDIT-"].update(visible=False)
            window["-DISCARD-EDIT-"].update(visible=False)

        if event == "-SHOW-FEMALE-ONLY-":
            if _editing_idx is not None or _dirty:
                resp = _popup_yes_no("Discard changes and switch view?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
                if _editing_idx is not None:
                    _discard_edit(window, _editing_idx, srt_files)
            _show_female_only = not _show_female_only
            window["-SHOW-FEMALE-ONLY-"].update("✓ Hide Male Dubs" if _show_female_only else "Hide Male Dubs", button_color=("white", "green") if _show_female_only else ("white", "#283b5b"))
            _dirty = False
            build_table(srt_files)
            _selected_idx = None
            _selected_display_idx = None
            _selected_button_key = None
            _editing_idx = None
            window["-PREVIEW-"].update("")
            window["-EDIT-"].update(disabled=True, visible=True)
            window["-SAVE-EDIT-"].update(visible=False)
            window["-DISCARD-EDIT-"].update(visible=False)

        if event == "-SHOW-NO-SPEAKER-":
            if _editing_idx is not None or _dirty:
                resp = _popup_yes_no("Discard changes and switch view?", title="Unsaved Changes")
                if resp != "-YES-":
                    continue
                if _editing_idx is not None:
                    _discard_edit(window, _editing_idx, srt_files)
            _show_no_speaker_only = not _show_no_speaker_only
            window["-SHOW-NO-SPEAKER-"].update("✓ Only Show Dubs Without Speakers" if _show_no_speaker_only else "Only Show Dubs Without Speakers", button_color=("white", "green") if _show_no_speaker_only else ("white", "#283b5b"))
            _dirty = False
            build_table(srt_files)
            _selected_idx = None
            _selected_display_idx = None
            _selected_button_key = None
            _editing_idx = None
            window["-PREVIEW-"].update("")
            window["-EDIT-"].update(disabled=True, visible=True)
            window["-SAVE-EDIT-"].update(visible=False)
            window["-DISCARD-EDIT-"].update(visible=False)

        if event == '-TABLE-' and values['-TABLE-'] and len(values['-TABLE-']) > 0:
            sel_row = values['-TABLE-'][0]
            if sel_row is None or sel_row < 0 or sel_row >= len(_display_rows):
                continue
            actual_row = _display_rows[sel_row]
            actual_idx = actual_row[0]
            fpath = actual_row[1]
            if _editing_idx is not None and _editing_idx != actual_idx:
                resp = _popup_yes_no("Do you want to confirm changes to the current file?", title="You Made Changes", cancel_text="Cancel")
                if resp == "-YES-":
                    _exit_edit(window, _editing_idx, srt_files)
                    window["-TABLE-"].update(select_rows=[sel_row])
                elif resp == "-NO-":
                    _discard_edit(window, _editing_idx, srt_files)
                    window["-TABLE-"].update(select_rows=[sel_row])
                else:  # -CANCEL-
                    if _selected_display_idx is not None:
                        window["-TABLE-"].update(select_rows=[_selected_display_idx])
                    continue
            if _editing_idx is not None and _editing_idx == actual_idx:
                continue
            _selected_display_idx = sel_row
            _selected_idx = actual_idx
            try:
                t_read = time.perf_counter()
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                window["-PREVIEW-"].update(content)
                _gf_logv(window, f"preview load ({actual_row[2]}: {len(content)} chars)", t_read)
            except Exception as e:
                window["-PREVIEW-"].update(f"Error reading file: {e}")
                _gf_logv(window, f"preview load failed: {e}")
            window["-EDIT-"].update(disabled=False, visible=True)

        if isinstance(event, tuple) and event[0] == '-TABLE-' and len(event) >= 3:
            t_row, col = event[2]
            if col not in (1, 2) or _editing_idx is not None:
                continue
            if t_row is None or t_row < 0 or t_row >= len(_display_rows):
                continue
            target = 'male' if col == 1 else 'female'
            actual_row = _display_rows[t_row]
            if actual_row[3] == target:
                continue
            set_gender(actual_row[1], target, actual_row[3])
            new_speaker = detect_speaker(actual_row[1])
            actual_row[3] = target
            actual_row[4] = new_speaker
            _update_table()
            window["-TABLE-"].update(select_rows=[t_row])
            if _selected_display_idx is not None and t_row == _selected_display_idx:
                try:
                    with open(actual_row[1], 'r', encoding='utf-8') as f:
                        window["-PREVIEW-"].update(f.read())
                except Exception as e:
                    window["-PREVIEW-"].update(f"Error reading file: {e}")
            _dirty = True
            window["-SAVE-"].update(disabled=False)
            update_button_states()

        if event == "-EDIT-" and _selected_idx is not None:
            _enter_edit(window, _selected_idx)

        if event == "-SAVE-EDIT-" and _selected_idx is not None:
            _exit_edit(window, _selected_idx, srt_files)
            build_table(srt_files)
            _selected_idx = None
            _selected_display_idx = None

        if event == "-DISCARD-EDIT-" and _selected_idx is not None:
            _discard_edit(window, _selected_idx, srt_files)

        if event == "-SETTINGS-":
            settings_layout = [
                [sg.Text("Default input folder:"), sg.InputText(key="-GF-INPUT-", default_text=gf_config.get("input_dir", "")), sg.FolderBrowse()],
                [sg.Checkbox("Overwrite original files without confirmation", key="-GF-OVERWRITE-", default=gf_config.get("overwrite_original", False))],
                [sg.Button("Save"), sg.Button("Cancel")],
            ]
            set_win = sg.Window("GenderFixer Settings", settings_layout, modal=True, resizable=True, size=(600, 200), location=_popup_location((600, 200)), icon=_icon_path('GenderFixer.ico'))
            while True:
                sev, svals = set_win.read()
                if sev in (sg.WINDOW_CLOSED, "Cancel"):
                    break
                if sev == "Save":
                    gf_config["input_dir"] = svals["-GF-INPUT-"].strip()
                    gf_config["overwrite_original"] = svals.get("-GF-OVERWRITE-", False)
                    _gf_save_config(gf_config)
                    break
            set_win.close()

        if event == "-SAVE-" and extract_dir:
            if not gf_config.get("overwrite_original", False):
                resp = _popup_yes_no("This will overwrite your original files.", title="Confirm Overwrite", yes_text="Continue", no_text="Cancel")
                if resp != "-YES-":
                    continue
            source_path = window["-SOURCE-"].get()
            try:
                if source_type == 'zip':
                    t_save = time.perf_counter()
                    repack_zip(source_path, extract_dir)
                    _gf_logv(window, f"repacked zip ({len(srt_files)} files)", t_save)
                else:
                    dest = _subs_source if _subs_source and os.path.isdir(_subs_source) else source_path
                    t_save = time.perf_counter()
                    for f in os.listdir(extract_dir):
                        if f.lower().endswith('.srt'):
                            shutil.copy2(os.path.join(extract_dir, f), os.path.join(dest, f))
                    _gf_logv(window, f"copied {len(srt_files)} .srt files to destination", t_save)
                _dirty = False
                window["-SAVE-"].update(disabled=True)
                update_button_states()
                save_path = dest if source_type == 'folder' else source_path
                _popup("Success", f"✓ Changes saved to:\n{save_path}", size=(500, 150))
            except Exception as e:
                _popup("Error", f"Failed to save: {e}", size=(500, 150))

    _gf_log(window, "Application exited")
    _gf_close_logging()
    if extract_dir:
        shutil.rmtree(extract_dir, ignore_errors=True)
    window.close()


if __name__ == "__main__":
    main()
