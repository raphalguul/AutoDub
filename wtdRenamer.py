#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WTD File Renamer — batch-rename MP4 & SRT files per WTD naming conventions."""

import os, re, sys, json, tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ── icon path ─────────────────────────────────────────────────────────

def _icon_path(name):
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'Icons', name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Icons', name)


# ── helpers ──────────────────────────────────────────────────────────

def _renamer_config_dir():
    exe = getattr(sys, 'frozen', False)
    return os.path.dirname(sys.executable) if exe else os.path.dirname(os.path.abspath(__file__))

def _renamer_load_config():
    path = os.path.join(_renamer_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
            return cfg.get("file_renamer", {})
    except:
        return {}

def _renamer_save_config(renamer_cfg):
    path = os.path.join(_renamer_config_dir(), "config.json")
    try:
        with open(path, 'r') as f:
            cfg = json.load(f)
    except:
        cfg = {"main": {}, "gender_fixer": {}, "file_renamer": {}}
    cfg["file_renamer"] = renamer_cfg
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)

def _extract_clip(name: str) -> tuple[str, int | None]:
    """Return (base_without_clip, clip_number_or_None)"""
    m = re.match(r'^(.+)-Clip(\d+)$', name)
    if m:
        return m.group(1), int(m.group(2))
    return name, None


def _sanitize(name: str, existing_clip: int | None = None) -> str:
    """Strip special chars, prepend _, preserve or assign clip number."""
    base = re.sub(r'[^a-zA-Z0-9-]', '', name)
    if not base.startswith('_'):
        base = '_' + base
    clip = existing_clip if existing_clip is not None else 1
    return f"{base}-Clip{clip:03d}"


def _find_subdir(parent: str, *candidates: str):
    """Return first existing subdirectory matching any candidate (case-insensitive)."""
    try:
        for entry in os.listdir(parent):
            if entry.lower() in {c.lower() for c in candidates}:
                return os.path.join(parent, entry)
    except PermissionError:
        pass
    return None


def _files_with_ext(directory: str, ext: str):
    """Return full paths of all files with the given extension (case-insensitive)."""
    if not directory or not os.path.isdir(directory):
        return []
    ext = ext.lower()
    result = []
    try:
        for entry in os.listdir(directory):
            if os.path.isfile(os.path.join(directory, entry)) and entry.lower().endswith(ext):
                result.append(os.path.join(directory, entry))
    except PermissionError:
        pass
    return sorted(result)


# ── GUI ──────────────────────────────────────────────────────────────

class WtdRenamer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WTD File Renamer")
        self.iconbitmap(_icon_path('WTD File Renamer.ico'))
        self.resizable(False, False)

        self._renamer_cfg = _renamer_load_config()
        self._folder = tk.StringVar()
        self._mp4_files: list[str] = []
        self._srt_files: list[str] = []
        self._orphan_mp4: list[str] = []
        self._orphan_srt: list[str] = []
        self._conflicts: list[tuple[str, list[str]]] = []

        self._build_ui()
        if self._renamer_cfg.get("remember_last", False) and self._renamer_cfg.get("default_folder", ""):
            self._folder.set(self._renamer_cfg["default_folder"])
            self._scan()
        self._update_state()

    # ── UI construction ──

    def _build_ui(self):
        # Folder selection
        frame_top = ttk.Frame(self, padding=(8, 0, 8, 8))
        frame_top.pack(fill="x")
        ttk.Label(frame_top, text="Target folder:").pack(side="left")
        self._entry = ttk.Entry(frame_top, textvariable=self._folder, width=50)
        self._entry.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(frame_top, text="Browse…", command=self._on_browse).pack(side="left", padx=4)

        # Results area
        frame_results = ttk.LabelFrame(self, text="Scan Results", padding=8)
        frame_results.pack(fill="both", expand=True, padx=8, pady=4)

        self._txt_results = tk.Text(frame_results, width=72, height=16, state="disabled", wrap="word")
        self._txt_results.pack(fill="both", expand=True)

        # Bottom buttons
        frame_bottom = ttk.Frame(self, padding=8)
        frame_bottom.pack(fill="x")

        self._btn_orphans = ttk.Button(frame_bottom, text="Show orphan SRT files", command=self._show_orphans, state="disabled")
        self._btn_orphans.pack(side="left", padx=4)

        ttk.Button(frame_bottom, text="Settings", command=self._settings).pack(side="left", padx=4)

        self._btn_rename = ttk.Button(frame_bottom, text="Rename", command=self._on_rename)
        self._btn_rename.pack(side="right", padx=4)

        ttk.Button(frame_bottom, text="Exit", command=self.destroy).pack(side="right", padx=4)

    # ── events ──

    def _on_browse(self):
        folder = filedialog.askdirectory(title="Select folder containing videoclips / subtitles", parent=self)
        if folder:
            self._folder.set(folder)
            self._scan()
            if self._renamer_cfg.get("remember_last", False):
                self._renamer_cfg["default_folder"] = folder
                _renamer_save_config(self._renamer_cfg)

    def _on_rename(self):
        self._btn_rename.config(state="disabled")
        self._apply_rename()
        self._scan()

    def _show_orphans(self):
        if self._orphan_srt:
            msg = "SRT files with no matching MP4:\n\n" + "\n".join(self._orphan_srt)
        else:
            msg = "No orphan SRT files."
        messagebox.showinfo("Orphan SRT Files", msg, parent=self)

    def _settings(self):
        win = tk.Toplevel(self)
        win.title("Settings")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        remember = tk.BooleanVar(value=self._renamer_cfg.get("remember_last", False))
        ttk.Checkbutton(win, text="Remember last used folder", variable=remember).pack(padx=16, pady=16, anchor="w")
        def save():
            self._renamer_cfg["remember_last"] = remember.get()
            _renamer_save_config(self._renamer_cfg)
            win.destroy()
        frame = ttk.Frame(win)
        frame.pack(pady=(0, 12))
        ttk.Button(frame, text="Save", command=save).pack(side="left", padx=4)
        ttk.Button(frame, text="Cancel", command=win.destroy).pack(side="left", padx=4)
        self.wait_window(win)

    # ── logic ──

    def _scan(self):
        folder = self._folder.get().strip()
        if not folder or not os.path.isdir(folder):
            self._set_results("Please select a valid folder.")
            self._update_state()
            return

        videoclips = _find_subdir(folder, "videoclips", "videoclip")
        subtitles = _find_subdir(folder, "subtitles", "subtitle")

        if not videoclips and not subtitles:
            self._set_results("No WTD files detected\n\n"
                              "The selected folder does not contain a 'videoclips' or 'subtitles' subfolder.")
            self._update_state()
            return

        lines = []

        # Find files
        self._mp4_files = _files_with_ext(videoclips, ".mp4")
        self._srt_files = _files_with_ext(subtitles, ".srt")

        lines.append(f"Found {len(self._mp4_files)} MP4 file(s) in videoclips")
        lines.append(f"Found {len(self._srt_files)} SRT file(s) in subtitles")
        lines.append("")

        # Scan existing files in target directories to detect conflicts
        def _scan_existing_clips(directory: str) -> dict[str, int]:
            """Return {base_name: max_clip_number} for _<base>-ClipXXX files in directory."""
            result = {}
            if not directory or not os.path.isdir(directory):
                return result
            pattern = re.compile(r'^_(.+)-Clip(\d+)$')
            try:
                for entry in os.listdir(directory):
                    if os.path.isfile(os.path.join(directory, entry)):
                        m = pattern.match(os.path.splitext(entry)[0])
                        if m:
                            base, num = m.group(1), int(m.group(2))
                            result[base] = max(result.get(base, 0), num)
            except PermissionError:
                pass
            return result

        mp4_existing = _scan_existing_clips(videoclips) if videoclips else {}
        srt_existing = _scan_existing_clips(subtitles) if subtitles else {}

        def _target_map(files: list[str], existing_clips: dict[str, int]):
            mapping: dict[str, list[str]] = {}
            for fp in files:
                base, ext = os.path.splitext(os.path.basename(fp))
                base_no_clip, clip = _extract_clip(base)
                tgt = _sanitize(base_no_clip, clip)
                mapping.setdefault(tgt, []).append(fp)
            return mapping

        mp4_targets = _target_map(self._mp4_files, mp4_existing)
        srt_targets = _target_map(self._srt_files, srt_existing)

        # Build target_sources for conflict detection (matching _apply_rename logic)
        def _build_target_sources(files: list[str], existing_clips: dict[str, int], directory: str):
            target_sources: dict[str, list[tuple[str, str, int | None]]] = {}
            for fp in files:
                base, ext = os.path.splitext(os.path.basename(fp))
                base_no_clip, clip = _extract_clip(base)
                tgt = _sanitize(base_no_clip, clip)
                target_sources.setdefault(tgt, []).append((fp, base_no_clip, clip))
            # Check against existing files in directory not being renamed
            for tgt in list(target_sources.keys()):
                tgt_path = os.path.join(directory, tgt + ".mp4" if directory == videoclips else tgt + ".srt")
                if os.path.exists(tgt_path) and all(s[0] != tgt_path for s in target_sources[tgt]):
                    target_sources[tgt].append(("__EXISTING__", None, None))
            return target_sources

        mp4_target_sources = _build_target_sources(self._mp4_files, mp4_existing, videoclips)
        srt_target_sources = _build_target_sources(self._srt_files, srt_existing, subtitles)

        # Conflicts: duplicate targets OR target conflicts with existing file
        self._conflicts = []
        for target_sources in (mp4_target_sources, srt_target_sources):
            for tgt, sources in target_sources.items():
                if len(sources) > 1:
                    self._conflicts.append((tgt, [s[0] for s in sources]))

        # Orphans — targets with no match in the other set
        self._orphan_mp4 = []
        for t, fps in mp4_targets.items():
            if t not in srt_targets:
                self._orphan_mp4.extend(fps)

        self._orphan_srt = []
        for t, fps in srt_targets.items():
            if t not in mp4_targets:
                self._orphan_srt.extend(fps)

        # Build report
        if self._conflicts:
            lines.append("⚠ CONFLICTS — multiple files map to the same name:")
            for tgt, fps in self._conflicts:
                lines.append(f"   → {tgt} : {', '.join(os.path.basename(f) for f in fps)}")
            lines.append("")

        if self._orphan_mp4:
            lines.append("MP4 files with NO matching SRT:")
            for f in self._orphan_mp4:
                lines.append(f"   • {os.path.basename(f)}")
            lines.append("")
        else:
            lines.append("Every MP4 has a matching SRT ✓")
            lines.append("")

        if self._orphan_srt:
            lines.append(f"SRT files with NO matching MP4 ({len(self._orphan_srt)}):")
            for f in self._orphan_srt:
                lines.append(f"   • {os.path.basename(f)}")
            lines.append("")
        else:
            lines.append("Every SRT has a matching MP4 ✓")
            lines.append("")

        if not self._conflicts and not self._orphan_mp4 and not self._orphan_srt:
            if self._mp4_files or self._srt_files:
                lines.append("Ready to rename.")
            else:
                lines.append("No files found to rename.")

        self._set_results("\n".join(lines))
        self._update_state()

    def _apply_rename(self):
        """Rename all files according to WTD conventions, handling conflicts."""
        all_files = self._mp4_files + self._srt_files

        # Scan existing files in target directories
        def _scan_existing_clips(directory: str) -> dict[str, int]:
            result = {}
            if not directory or not os.path.isdir(directory):
                return result
            pattern = re.compile(r'^_(.+)-Clip(\d+)$')
            try:
                for entry in os.listdir(directory):
                    if os.path.isfile(os.path.join(directory, entry)):
                        m = pattern.match(os.path.splitext(entry)[0])
                        if m:
                            base, num = m.group(1), int(m.group(2))
                            result[base] = max(result.get(base, 0), num)
            except PermissionError:
                pass
            return result

        mp4_clips = _scan_existing_clips(os.path.dirname(self._mp4_files[0])) if self._mp4_files else {}
        srt_clips = _scan_existing_clips(os.path.dirname(self._srt_files[0])) if self._srt_files else {}

        # Build rename plan with clip preservation
        plan: list[tuple[str, str, str, int | None]] = []  # src, dst, base, clip
        for fp in all_files:
            directory = os.path.dirname(fp)
            base, ext = os.path.splitext(os.path.basename(fp))
            base_no_clip, clip = _extract_clip(base)
            new_base = _sanitize(base_no_clip, clip)
            target = os.path.join(directory, new_base + ext)
            if target != fp:
                plan.append((fp, target, new_base, clip))

        if not plan:
            messagebox.showinfo("Rename", "All files already follow conventions.", parent=self)
            return

        # Build target -> list of (src, new_base, clip) mappings
        target_sources: dict[str, list[tuple[str, str, int | None]]] = {}
        for src, dst, new_base, clip in plan:
            target_sources.setdefault(dst, []).append((src, new_base, clip))

        # Also check against existing files in directory not being renamed
        for dst in list(target_sources.keys()):
            if os.path.exists(dst) and all(s[0] != dst for s in target_sources[dst]):
                target_sources[dst].append(("__EXISTING__", None, None))

        # Resolve conflicts: group by base name (without extension) so MP4/SRT pairs stay in sync
        resolved_plan: list[tuple[str, str]] = []
        conflicts = {t: s for t, s in target_sources.items() if len(s) > 1}

        if conflicts:
            # Group conflicts by base name (strip extension)
            base_conflicts: dict[str, list[tuple[str, str, int | None, str]]] = {}  # base -> [(src, new_base, clip, ext)]
            for dst, sources in conflicts.items():
                base_with_clip = os.path.splitext(os.path.basename(dst))[0]
                base_no_clip = re.sub(r'-Clip\d+$', '', base_with_clip)
                ext = os.path.splitext(dst)[1]
                for src, new_base, clip in sources:
                    base_conflicts.setdefault(base_no_clip, []).append((src, new_base, clip, ext))

            mp4_dir = os.path.dirname(self._mp4_files[0]) if self._mp4_files else None
            srt_dir = os.path.dirname(self._srt_files[0]) if self._srt_files else None

            # Resolve clip numbers per base name (shared across MP4/SRT)
            for base_no_clip, sources in base_conflicts.items():
                # Track taken clip numbers from existing files in both directories
                taken_clips: set[int] = set()
                clip = mp4_clips.get(base_no_clip)
                if clip:
                    taken_clips.add(clip)
                clip = srt_clips.get(base_no_clip)
                if clip:
                    taken_clips.add(clip)

                # Sort: existing files first, then by clip descending
                sorted_srcs = sorted(sources, key=lambda x: (x[0] == "__EXISTING__", x[2] or 0), reverse=True)

                for src, new_base, clip, ext in sorted_srcs:
                    if src == "__EXISTING__":
                        if clip is not None:
                            taken_clips.add(clip)
                        continue

                    # Find next available clip number
                    if clip is not None and clip not in taken_clips:
                        new_clip = clip
                    else:
                        new_clip = 1
                        while new_clip in taken_clips:
                            new_clip += 1
                    taken_clips.add(new_clip)

                    new_dst = os.path.join(os.path.dirname(src), f"{new_base}-Clip{new_clip:03d}{ext}")
                    resolved_plan.append((src, new_dst))

            # Non-conflicting targets keep their assigned names
            for dst, sources in target_sources.items():
                if dst not in conflicts:
                    resolved_plan.append((sources[0][0], dst))
        else:
            # No conflicts, use original plan
            resolved_plan = [(src, dst) for src, dst, _, _ in plan]

        if not resolved_plan:
            messagebox.showinfo("Rename", "All files already follow conventions.", parent=self)
            return

        # Execute renames
        skipped = 0
        renamed = 0
        overwritten = 0
        errors: list[str] = []

        for src, dst in resolved_plan:
            if os.path.exists(dst):
                ans = messagebox.askyesnocancel(
                    "File Exists",
                    f"Overwrite existing file?\n\n{dst}",
                    parent=self
                )
                if ans is None:
                    skipped += 1
                    continue
                if ans:
                    os.remove(dst)
                    overwritten += 1
                else:
                    skipped += 1
                    continue
            try:
                os.rename(src, dst)
                renamed += 1
            except OSError as e:
                errors.append(f"{src} → {dst}: {e}")

        msg = f"Renamed: {renamed}"
        if skipped:
            msg += f"\nSkipped: {skipped}"
        if overwritten:
            msg += f"\nOverwritten: {overwritten}"
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)
        messagebox.showinfo("Rename Complete", msg, parent=self)

    # ── UI helpers ──

    def _set_results(self, text: str):
        self._txt_results.config(state="normal")
        self._txt_results.delete("1.0", "end")
        self._txt_results.insert("1.0", text)
        self._txt_results.config(state="disabled")

    def _update_state(self):
        can_rename = (
            len(self._mp4_files) + len(self._srt_files) > 0
            and not self._conflicts
        )
        self._btn_rename.config(state="normal" if can_rename else "disabled")
        self._btn_orphans.config(state="normal" if self._orphan_srt else "disabled")


# ── entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    WtdRenamer().mainloop()
