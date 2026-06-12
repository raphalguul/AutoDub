"""
mpv JSON IPC wrapper for wtd_preview.
Handles playback control, dub muting, and position polling.
Uses Unix sockets (Linux/macOS) or named pipes (Windows).
No synchronous IPC from the UI thread — all values are cached via observe_property.
"""

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from typing import Any, Callable, Optional


class _SyncResponse:
    def __init__(self):
        self.response: Optional[dict] = None
        self.event = threading.Event()


class MpvPlayer:
    """Controls an mpv instance via JSON IPC."""

    def __init__(self, ipc_path: str, mpv_path: str = "mpv"):
        self.ipc_path = ipc_path
        self._mpv_path = mpv_path
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._listeners: dict[str, list[Callable]] = {}
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._dub_filter_active = False
        self._is_windows = os.name == 'nt'
        self._request_id = 0
        self._sync_responses: dict[int, _SyncResponse] = {}
        self._sync_lock = threading.Lock()
        self._mpv_stderr = None
        self._sock: Optional[socket.socket] = None
        self._fd: Optional[int] = None
        # Cached values — updated by reader thread, read from UI thread
        self._cached_time_pos = 0.0
        self._cached_duration = 0.0
        self._cached_paused = True
        self._mpv_ready = threading.Event()

    def _ipc_arg(self) -> str:
        if self._is_windows:
            unique = uuid.uuid4().hex[:8]
            self._pipe_name = f"mpv-wtd-{unique}"
            return f"\\\\.\\pipe\\{self._pipe_name}"
        else:
            return self.ipc_path

    def _read_stderr(self) -> str:
        """Read available stderr from mpv without blocking."""
        if not self._mpv_stderr:
            return ""
        try:
            data = self._mpv_stderr.read1(4096)
            if data:
                return data.decode('utf-8', errors='replace')
            return "(no stderr output)"
        except Exception:
            return "(error reading stderr)"

    def _connect(self) -> None:
        if self._is_windows:
            pipe_path = f"\\\\.\\pipe\\{self._pipe_name}"
            for attempt in range(200):
                if self.process and self.process.poll() is not None:
                    stderr_text = self._read_stderr()
                    cmd_line = " ".join(str(a) for a in getattr(self, '_mpv_args', []))
                    raise RuntimeError(
                        f"mpv exited early (code {self.process.returncode}).\n"
                        f"Stderr: {stderr_text}\n"
                        f"Command: {cmd_line}"
                    )
                try:
                    self._fd = os.open(pipe_path, os.O_RDWR | os.O_BINARY)
                    return
                except OSError:
                    time.sleep(0.1)
            raise RuntimeError(f"mpv named pipe not available: {pipe_path}")
        else:
            for attempt in range(200):
                if self.process and self.process.poll() is not None:
                    stderr_text = self._read_stderr()
                    cmd_line = " ".join(str(a) for a in getattr(self, '_mpv_args', []))
                    raise RuntimeError(
                        f"mpv exited early (code {self.process.returncode}).\n"
                        f"Stderr: {stderr_text}\n"
                        f"Command: {cmd_line}"
                    )
                if os.path.exists(self.ipc_path):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("mpv IPC socket not created: " + self.ipc_path)
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(0.5)
            self._sock.connect(self.ipc_path)

    def start(self, video_path: str) -> None:
        """Launch mpv and connect IPC. May block up to ~20s on first call."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        ipc_arg = self._ipc_arg()

        args = [
            self._mpv_path,
            '--no-terminal',
            '--input-ipc-server=' + ipc_arg,
            '--no-osc',
            '--no-osd-bar',
            '--osd-level=0',
            '--loop-file=inf',
            '--pause',
            '--keep-open=yes',
            '--ontop=no',
            video_path,
        ]
        self.process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        self._mpv_stderr = self.process.stderr
        self._mpv_args = args

        self._connect()

        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Send observe commands — cache will be populated when responses arrive
        self._send_async({"command": ["observe_property", 1, "time-pos"]})
        self._send_async({"command": ["observe_property", 2, "pause"]})
        self._send_async({"command": ["observe_property", 3, "eof-reached"]})
        self._send_async({"command": ["get_property", "duration"]})

        self._mpv_ready.set()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Wait until mpv is connected and ready."""
        return self._mpv_ready.wait(timeout)

    def stop(self) -> None:
        self._running = False
        try:
            self._send_async({"command": ["quit"]})
        except Exception:
            pass
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
        if not self._is_windows:
            try:
                if os.path.exists(self.ipc_path):
                    os.unlink(self.ipc_path)
            except Exception:
                pass

    def _read(self, n: int) -> bytes:
        if self._is_windows:
            if self._fd is None:
                return b""
            return os.read(self._fd, n)
        else:
            if self._sock is None:
                return b""
            return self._sock.recv(n)

    def _write(self, data: bytes) -> None:
        if self._is_windows:
            if self._fd is not None:
                os.write(self._fd, data)
        else:
            if self._sock is not None:
                self._sock.sendall(data)

    # --- Public API (all non-blocking, values from cache) ---

    def pause(self) -> None:
        self._send_async({"command": ["set_property", "pause", True]})

    def unpause(self) -> None:
        self._send_async({"command": ["set_property", "pause", False]})

    def toggle_pause(self) -> None:
        self._send_async({"command": ["cycle", "pause"]})

    def seek(self, seconds: float) -> None:
        self._send_async({"command": ["seek", seconds, "relative", "exact"]})

    def seek_absolute(self, seconds: float) -> None:
        self._send_async({"command": ["seek", seconds, "absolute", "exact"]})

    def get_time_pos(self) -> float:
        return self._cached_time_pos

    def get_duration(self) -> float:
        return self._cached_duration

    def is_paused(self) -> bool:
        return self._cached_paused

    def set_loop(self, enable: bool) -> None:
        if enable:
            self._send_async({"command": ["set_property", "loop-file", "inf"]})
        else:
            self._send_async({"command": ["set_property", "loop-file", "no"]})

    def set_dub_mute(self, start: float, end: float) -> None:
        filter_cmd = f"lavfi=[volume=enable='between(t,{start},{end})':volume=0]"
        self._send_async({"command": ["af", "set", filter_cmd]})
        self._dub_filter_active = True

    def clear_dub_mute(self) -> None:
        self._send_async({"command": ["af", "set", ""]})
        self._dub_filter_active = False

    def on(self, event: str, callback: Callable) -> None:
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    # --- Internal ---

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_sync(self, data: dict, timeout: float = 3.0) -> Optional[dict]:
        req_id = self._next_id()
        data["request_id"] = req_id
        sync = _SyncResponse()
        with self._sync_lock:
            self._sync_responses[req_id] = sync
        payload = json.dumps(data) + "\n"
        with self._lock:
            try:
                self._write(payload.encode('utf-8'))
            except Exception:
                with self._sync_lock:
                    self._sync_responses.pop(req_id, None)
                return None
        sync.event.wait(timeout)
        with self._sync_lock:
            self._sync_responses.pop(req_id, None)
        return sync.response

    def _send_async(self, data: dict) -> None:
        payload = json.dumps(data) + "\n"
        with self._lock:
            try:
                self._write(payload.encode('utf-8'))
            except Exception:
                pass

    def _reader_loop(self) -> None:
        buffer = ""
        while self._running:
            try:
                data = self._read(4096).decode('utf-8')
                if not data:
                    break
                buffer += data
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                        self._handle_message(msg)
                    except json.JSONDecodeError:
                        pass
            except (socket.timeout, OSError, ConnectionError, BlockingIOError):
                if self._running:
                    time.sleep(0.05)

    def _handle_message(self, msg: dict) -> None:
        req_id = msg.get("request_id")
        if req_id is not None:
            with self._sync_lock:
                sync = self._sync_responses.get(req_id)
                if sync:
                    sync.response = msg
                    sync.event.set()
            # Cache duration from get_property response
            if msg.get("command") == ["get_property", "duration"] and "data" in msg:
                self._cached_duration = float(msg["data"])

        event = msg.get("event")
        if event == "property-change":
            name = msg.get("name")
            value = msg.get("data")
            if name == "time-pos":
                if value is not None:
                    self._cached_time_pos = float(value)
                self._fire("time-pos", self._cached_time_pos)
            elif name == "pause":
                self._cached_paused = bool(value) if value is not None else True
                self._fire("pause", self._cached_paused)
            elif name == "eof-reached":
                self._fire("eof-reached", value)

    def _fire(self, event: str, data: Any) -> None:
        for cb in self._listeners.get(event, []):
            try:
                cb(data)
            except Exception:
                pass
