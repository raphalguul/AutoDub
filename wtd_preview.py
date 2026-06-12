"""
wtd_preview: Standalone preview window for WTD subtitle editing.
Launched by main.py with --video and --srt arguments.
Communicates results via exit codes: 0=save, 1=close_no_save.
"""

import argparse
import os
import sys
import threading
from typing import Optional

from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QObject
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QShortcut, QStatusBar, QVBoxLayout,
    QWidget, QFrame,
)

from preview_player import MpvPlayer
from preview_srt import (
    SubtitleEntry, parse_srt, serialize_srt,
    normalize_dub_markers, validate_single_dub,
)
from preview_timeline import TimelineView


class _PreviewSignals(QObject):
    status_changed = pyqtSignal(str)
    mpv_error = pyqtSignal(str)
    refocus_requested = pyqtSignal()


class PreviewWindow(QMainWindow):
    def __init__(self, video_path: str, srt_path: str, ipc_dir: str, mpv_path: str = "mpv"):
        super().__init__()
        self._video_path = video_path
        self._srt_path = srt_path
        self._ipc_dir = ipc_dir
        self._mpv_path = mpv_path
        self._dirty = False
        self._looping = False
        self._playing = False
        self._player: Optional[MpvPlayer] = None
        self._entries: list[SubtitleEntry] = []
        self._duration = 10.0

        # Cross-thread signal proxy
        self._signals = _PreviewSignals()
        self._signals.status_changed.connect(self._on_status)
        self._signals.mpv_error.connect(self._on_mpv_error)
        self._signals.refocus_requested.connect(self._on_refocus_requested)

        self._init_ui()
        self._load_srt()

        # Start mpv in background so UI stays responsive
        threading.Thread(target=self._init_player, daemon=True).start()

        # Poll playhead (all cached, no IPC calls)
        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll_playhead)
        self._poll_timer.start(100)

    def _init_ui(self) -> None:
        self.setWindowTitle("WTD Preview")
        self.setMinimumSize(1000, 500)
        self.resize(1200, 650)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._timeline = TimelineView()
        layout.addWidget(self._timeline, 1)

        ctrl = QFrame()
        ctrl.setFrameShape(QFrame.StyledPanel)
        ctrl.setStyleSheet("background:#3a3a40;")
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(8, 4, 8, 4)

        self._btn_play = QPushButton("Play")
        self._btn_play.setFixedWidth(80)
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self._btn_play)

        self._btn_loop = QPushButton("Loop")
        self._btn_loop.setCheckable(True)
        self._btn_loop.setChecked(False)
        self._btn_loop.clicked.connect(self._toggle_loop)
        ctrl_layout.addWidget(self._btn_loop)

        self._lbl_time = QLabel("0:00.000 / 0:00.000")
        self._lbl_time.setStyleSheet("color:#ddd; font-family:'Segoe UI';")
        ctrl_layout.addWidget(self._lbl_time)

        ctrl_layout.addStretch()

        self._btn_save = QPushButton("Save and Return (Ctrl+S)")
        self._btn_save.clicked.connect(self._save_and_return)
        self._btn_save.setStyleSheet(
            "QPushButton { background:#2d7d2d; color:white; padding:6px 16px; "
            "border-radius:4px; font-weight:bold; }"
            "QPushButton:hover { background:#3a9a3a; }"
        )
        ctrl_layout.addWidget(self._btn_save)

        self._btn_close = QPushButton("Close without Saving")
        self._btn_close.clicked.connect(self._close_no_save)
        self._btn_close.setStyleSheet(
            "QPushButton { background:#7d2d2d; color:white; padding:6px 16px; "
            "border-radius:4px; }"
            "QPushButton:hover { background:#9a3a3a; }"
        )
        ctrl_layout.addWidget(self._btn_close)

        layout.addWidget(ctrl)

        self._status = QStatusBar()
        self._status.setStyleSheet("background:#2a2a30; color:#aaa;")
        self.setStatusBar(self._status)
        self._status.showMessage("Starting mpv...")

        # Keyboard shortcuts — ApplicationShortcut so they work even when mpv has focus
        ctx = Qt.ApplicationShortcut
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_play, context=ctx)
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_and_return, context=ctx)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._zoom_in, context=ctx)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._zoom_out, context=ctx)
        QShortcut(QKeySequence("Ctrl+0"), self, self._reset_zoom, context=ctx)
        QShortcut(QKeySequence("L"), self, self._toggle_loop, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Left), self, self._seek_minus_short, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Right), self, self._seek_plus_short, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Left | Qt.ShiftModifier), self, self._seek_minus_long, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Right | Qt.ShiftModifier), self, self._seek_plus_long, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self._delete_selected, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Up), self, self._prev_marker, context=ctx)
        QShortcut(QKeySequence(Qt.Key_Down), self, self._next_marker, context=ctx)

        self._timeline.seek_requested.connect(self._seek_to)
        self._timeline.segment_changed.connect(self._mark_dirty)

        # Keep preview window on top so it's always accessible
        self.setFocusPolicy(Qt.StrongFocus)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

    def _on_status(self, msg: str) -> None:
        self._status.showMessage(msg)

    def _on_mpv_error(self, msg: str) -> None:
        QMessageBox.critical(self, "MPV Error", msg)

    def _on_refocus_requested(self) -> None:
        QTimer.singleShot(200, self._refocus)

    def _load_srt(self) -> None:
        with open(self._srt_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        fixed, changed = normalize_dub_markers(content)
        if changed:
            content = fixed
        self._entries = parse_srt(content)
        ok, msg = validate_single_dub(content)
        if not ok:
            QMessageBox.warning(self, "Dub Marker Error", msg)
        self._duration = max((e.end for e in self._entries), default=10.0) + 2.0
        self._timeline.set_entries(self._entries, self._duration)
        self._timeline.reset_zoom()

    def _init_player(self) -> None:
        ipc_path = os.path.join(self._ipc_dir, "mpv_ipc.sock")
        player = MpvPlayer(ipc_path, mpv_path=self._mpv_path)
        try:
            player.start(self._video_path)
            self._player = player
            # Bring preview window back to front after mpv steals focus
            self._signals.status_changed.emit("Ready. Press Space to play/pause.")
            self._signals.refocus_requested.emit()
        except Exception as e:
            self._signals.mpv_error.emit(
                f"Failed to start mpv:\n{e}\n\nmpv path: {self._mpv_path}"
            )

    def _refocus(self) -> None:
        self.raise_()
        self.activateWindow()
        self._timeline.setFocus()

    def _poll_playhead(self) -> None:
        if not self._player:
            return
        pos = self._player.get_time_pos()
        self._timeline.set_playhead_pos(pos)
        dur = self._player.get_duration()
        self._lbl_time.setText(self._format_time(pos, dur))

    @staticmethod
    def _format_time(pos: float, dur: float) -> str:
        def fmt(sec: float) -> str:
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = sec % 60
            return f"{h}:{m:02d}:{s:06.3f}"
        return f"{fmt(pos)} / {fmt(dur)}"

    def _toggle_play(self) -> None:
        if not self._player:
            return
        self._playing = not self._playing
        if self._playing:
            self._player.unpause()
            self._btn_play.setText("Pause")
        else:
            self._player.pause()
            self._btn_play.setText("Play")

    def _toggle_loop(self) -> None:
        self._looping = not self._looping
        if self._player:
            self._player.set_loop(self._looping)
        self._btn_loop.setChecked(self._looping)
        self._status.showMessage(f"Loop: {'ON' if self._looping else 'OFF'}")

    def _seek_to(self, seconds: float) -> None:
        if self._player:
            self._player.seek_absolute(seconds)

    def _seek_minus_short(self) -> None:
        if self._player:
            self._player.seek(-0.01)

    def _seek_plus_short(self) -> None:
        if self._player:
            self._player.seek(0.01)

    def _seek_minus_long(self) -> None:
        if self._player:
            self._player.seek(-2.0)

    def _seek_plus_long(self) -> None:
        if self._player:
            self._player.seek(2.0)

    def _prev_marker(self) -> None:
        pos = self._player.get_time_pos() if self._player else 0.0
        markers = [e for e in self._entries if e.is_dub]
        if not markers:
            return
        prev = max((m for m in markers if m.start < pos - 0.1), key=lambda m: m.start, default=None)
        self._seek_to(prev.start if prev else markers[-1].start)

    def _next_marker(self) -> None:
        pos = self._player.get_time_pos() if self._player else 0.0
        markers = [e for e in self._entries if e.is_dub]
        if not markers:
            return
        nxt = min((m for m in markers if m.start > pos + 0.1), key=lambda m: m.start, default=None)
        self._seek_to(nxt.start if nxt else markers[0].start)

    def _delete_selected(self) -> None:
        selected = [s for s in self._timeline._segments if s.isSelected()]
        if not selected:
            return
        reply = QMessageBox.question(
            self, "Delete Segment",
            f"Delete {len(selected)} selected segment(s)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for seg in selected:
            if seg.entry in self._entries:
                self._entries.remove(seg.entry)
        self._timeline.set_entries(self._entries, self._duration)
        self._mark_dirty()

    def _zoom_in(self) -> None: self._timeline.zoom_in()
    def _zoom_out(self) -> None: self._timeline.zoom_out()
    def _reset_zoom(self) -> None: self._timeline.reset_zoom()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._btn_close.setStyleSheet(
                "QPushButton { background:#cc3333; color:white; padding:6px 16px; "
                "border-radius:4px; font-weight:bold; }"
                "QPushButton:hover { background:#ee4444; }"
            )
            self._status.showMessage("Unsaved changes")

    def _save_and_return(self) -> None:
        content = serialize_srt(self._entries)
        fixed, changed = normalize_dub_markers(content)
        if changed:
            content = fixed
            self._status.showMessage("Normalized the dub marker")
        with open(self._srt_path, 'w', encoding='utf-8') as f:
            f.write(content)
        with open(os.path.join(self._ipc_dir, "saved.txt"), 'w') as f:
            f.write("1")
        self._player_cleanup()
        sys.exit(0)

    def _close_no_save(self) -> None:
        if self._dirty:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Discard unsaved changes and close?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        with open(os.path.join(self._ipc_dir, "saved.txt"), 'w') as f:
            f.write("0")
        self._player_cleanup()
        sys.exit(1)

    def _player_cleanup(self) -> None:
        self._poll_timer.stop()
        if self._player:
            try:
                self._player.stop()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        self._close_no_save()
        event.ignore()


def main():
    parser = argparse.ArgumentParser(description="WTD Preview Window")
    parser.add_argument("--video", required=True)
    parser.add_argument("--srt", required=True)
    parser.add_argument("--ipc-dir", required=True)
    parser.add_argument("--mpv-path", default="mpv")
    args = parser.parse_args()

    if not os.path.exists(args.video):
        print(f"Video not found: {args.video}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.srt):
        print(f"SRT not found: {args.srt}", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("WTD Preview")
    win = PreviewWindow(args.video, args.srt, args.ipc_dir, args.mpv_path)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
