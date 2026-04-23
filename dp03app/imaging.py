"""Image a raw disk device to a .img file, with an admin-privilege prompt.

macOS blocks raw reads of /dev/rdiskN for non-root users. Rather than
making the user drop to Terminal and memorise `sudo dd`, we shell out to
`osascript` with `do shell script ... with administrator privileges`,
which fires the standard macOS GUI password / Touch ID prompt and runs
the command as root. The Python process stays unprivileged.

While dd runs we poll the output file's size against the disk's declared
size (pulled from `diskutil info -plist`) to drive a progress bar.

Windows users hit a different flow — WINDOWS.md describes imaging with
PowerShell in an elevated shell — so the `ImagingJob` class here is
macOS-only for now. We gate it behind `is_supported()`.
"""
from __future__ import annotations

import plistlib
import queue
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def is_supported() -> bool:
    """True on macOS where `osascript` + `dd` are guaranteed to exist."""
    return sys.platform == "darwin"


def get_disk_size_bytes(device: str) -> Optional[int]:
    """Return the declared size of /dev/rdiskN via diskutil. None on failure."""
    # Strip the 'r' so diskutil gets /dev/diskN (it accepts both but the
    # cooked path is the canonical one).
    canonical = device.replace("/dev/rdisk", "/dev/disk")
    try:
        out = subprocess.check_output(
            ["diskutil", "info", "-plist", canonical],
            timeout=5, stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, FileNotFoundError,
            subprocess.TimeoutExpired):
        return None
    try:
        info = plistlib.loads(out)
    except Exception:  # noqa: BLE001
        return None
    # diskutil returns both "Size" (bytes) and "TotalSize" in modern macOS.
    for key in ("TotalSize", "Size"):
        val = info.get(key)
        if isinstance(val, int) and val > 0:
            return val
    return None


def default_image_path(device: str) -> Path:
    """Suggest ~/Desktop/DP-03-<timestamp>.img for a given raw device."""
    tag = device.rsplit("/", 1)[-1] or "card"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path.home() / "Desktop" / f"DP-03_{tag}_{stamp}.img"


@dataclass
class ImagingProgress:
    """A snapshot emitted by ImagingJob while dd is running."""
    bytes_written: int
    total_bytes: int  # 0 if unknown
    fraction: float   # 0.0-1.0, 0 if unknown
    stage: str        # human-readable status


# Sentinel strings on the ERROR payload so the UI can branch cleanly.
ERROR_CANCELLED = "CANCELLED"
ERROR_TCC_DENIED = "TCC_DENIED"       # Full Disk Access / Privacy gate
ERROR_DEVICE_BUSY = "DEVICE_BUSY"     # card was in use by another process
ERROR_OTHER = "OTHER"


def classify_dd_error(stderr: str) -> str:
    """Map dd / osascript stderr text to one of the ERROR_ sentinels."""
    text = (stderr or "").lower()
    if "user canceled" in text or "(-128)" in text:
        return ERROR_CANCELLED
    if "operation not permitted" in text:
        return ERROR_TCC_DENIED
    if "resource busy" in text or "device busy" in text:
        return ERROR_DEVICE_BUSY
    return ERROR_OTHER


class ImagingJob:
    """Background imaging job. Use from the Tk UI via .poll().

    Two modes:
      - "admin_prompt" (default): uses `osascript with administrator
        privileges` to run dd as root. One-click UX but the TCC
        attribution lands on osascript, which can fail with "Operation
        not permitted" on modern macOS even with Full Disk Access
        granted to Terminal.
      - "terminal": opens Terminal.app and runs `sudo dd` interactively
        there. The user types their password in Terminal. TCC
        attribution is Terminal itself, which usually succeeds if
        Terminal has FDA. The job uses sentinel files next to the
        output .img to detect success/failure.
    """

    MODE_ADMIN_PROMPT = "admin_prompt"
    MODE_TERMINAL = "terminal"

    EVENT_STARTED = "started"
    EVENT_PROGRESS = "progress"
    EVENT_DONE = "done"
    EVENT_ERROR = "error"

    def __init__(self, device: str, out_path: Path,
                 mode: str = MODE_ADMIN_PROMPT) -> None:
        if not is_supported():
            raise RuntimeError(
                "ImagingJob is only implemented on macOS right now.")
        if mode not in (self.MODE_ADMIN_PROMPT, self.MODE_TERMINAL):
            raise ValueError(f"Unknown imaging mode: {mode!r}")
        self.device = device
        self.out_path = Path(out_path)
        self.mode = mode
        self.total_bytes = get_disk_size_bytes(device) or 0
        self._queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------------- public API ----------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        target = (self._run_via_terminal if self.mode == self.MODE_TERMINAL
                  else self._run)
        self._thread = threading.Thread(
            target=target, daemon=True, name="dp03-imaging-job")
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    def poll(self) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------------- worker ----------------

    def _emit(self, event: str, payload: object = None) -> None:
        self._queue.put((event, payload))

    def _run(self) -> None:
        self._emit(self.EVENT_STARTED, self.total_bytes)
        # Make sure the output parent exists so dd doesn't explode.
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        # Wipe any stale file so our size poll is accurate.
        try:
            self.out_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

        # Build the dd command. bs=4m matches Apple's recommended block
        # size for whole-disk imaging. We escape the paths for the shell
        # osascript runs inside its shell script wrapper.
        device = self.device
        out = str(self.out_path)
        # AppleScript string escaping — double the backslashes and quotes.
        inner = (
            f"/bin/dd if={shlex.quote(device)} "
            f"of={shlex.quote(out)} bs=4m"
        )
        applescript = (
            'do shell script "' +
            inner.replace("\\", "\\\\").replace('"', '\\"') +
            '" with administrator privileges'
        )

        poll_thread = threading.Thread(
            target=self._poll_file_size, daemon=True,
            name="dp03-imaging-progress",
        )
        poll_thread.start()

        try:
            proc = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True, text=True,
            )
        except FileNotFoundError:
            self._cancel.set()
            poll_thread.join(timeout=1.0)
            self._emit(self.EVENT_ERROR,
                       "osascript not found — is this really macOS?")
            return
        except Exception as exc:  # noqa: BLE001
            self._cancel.set()
            poll_thread.join(timeout=1.0)
            self._emit(self.EVENT_ERROR, f"Imaging failed: {exc}")
            return

        # Tell the progress poller to stop.
        self._cancel.set()
        poll_thread.join(timeout=1.0)

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            kind = classify_dd_error(stderr)
            # Emit a dict so the UI can differentiate without substring-
            # matching the human text itself.
            self._emit(self.EVENT_ERROR, {
                "kind": kind,
                "stderr": stderr,
                "returncode": proc.returncode,
            })
            return

        # On success, emit a final 100% progress snapshot before DONE so the
        # UI's bar finishes cleanly.
        try:
            final_size = self.out_path.stat().st_size
        except OSError:
            final_size = 0
        self._emit(self.EVENT_PROGRESS, ImagingProgress(
            bytes_written=final_size,
            total_bytes=self.total_bytes,
            fraction=1.0,
            stage="Done.",
        ))
        self._emit(self.EVENT_DONE, self.out_path)

    def _poll_file_size(self) -> None:
        """Side thread that watches out_path growing and emits PROGRESS."""
        last_size = -1
        while not self._cancel.is_set():
            try:
                size = self.out_path.stat().st_size
            except OSError:
                size = 0
            if size != last_size:
                last_size = size
                frac = (size / self.total_bytes) if self.total_bytes else 0.0
                frac = max(0.0, min(0.999, frac))
                stage = (
                    f"Imaging… {size / (1024*1024):.1f} MB"
                    + (f" of {self.total_bytes / (1024*1024):.0f} MB"
                       if self.total_bytes else "")
                )
                self._emit(self.EVENT_PROGRESS, ImagingProgress(
                    bytes_written=size,
                    total_bytes=self.total_bytes,
                    fraction=frac,
                    stage=stage,
                ))
            time.sleep(0.4)

    # -------- Terminal mode (sidesteps osascript TCC attribution) --------

    def _run_via_terminal(self) -> None:
        """Pop Terminal.app running `sudo dd`, watch for sentinel files."""
        self._emit(self.EVENT_STARTED, self.total_bytes)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.out_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

        done_marker = Path(str(self.out_path) + ".done")
        err_marker = Path(str(self.out_path) + ".error")
        for m in (done_marker, err_marker):
            try:
                m.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

        device = self.device
        out = str(self.out_path)
        # Shell command Terminal will run. We prefix with a friendly
        # banner so the user knows what to do if sudo asks for a password.
        shell_cmd = (
            "clear; "
            "printf '\\033[1;36m=== DP-03 card imaging ===\\033[0m\\n\\n'; "
            "echo 'If macOS asks for your password, type it here and "
            "press Return.'; "
            "echo; "
            f"sudo /bin/dd if={shlex.quote(device)} of={shlex.quote(out)} "
            f"bs=4m "
            f"&& touch {shlex.quote(str(done_marker))} "
            f"|| touch {shlex.quote(str(err_marker))}; "
            "echo; "
            "echo '--- imaging finished, you can close this window ---'"
        )
        # AppleScript string escape: both backslashes and double-quotes.
        applescript = (
            'tell application "Terminal"\n'
            '  activate\n'
            '  do script "' +
            shell_cmd.replace("\\", "\\\\").replace('"', '\\"') +
            '"\n'
            'end tell'
        )

        try:
            subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            self._emit(self.EVENT_ERROR, {
                "kind": ERROR_OTHER,
                "stderr": f"Could not open Terminal: {exc}",
                "returncode": -1,
            })
            return

        # Poll: progress from file size, completion from sentinel files.
        last_size = -1
        while not self._cancel.is_set():
            if done_marker.exists():
                try:
                    done_marker.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    final_size = self.out_path.stat().st_size
                except OSError:
                    final_size = 0
                self._emit(self.EVENT_PROGRESS, ImagingProgress(
                    bytes_written=final_size,
                    total_bytes=self.total_bytes,
                    fraction=1.0,
                    stage="Done.",
                ))
                self._emit(self.EVENT_DONE, self.out_path)
                return
            if err_marker.exists():
                try:
                    err_marker.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                self._emit(self.EVENT_ERROR, {
                    "kind": ERROR_OTHER,
                    "stderr": ("dd failed in Terminal — check the Terminal "
                               "window for the error message."),
                    "returncode": -1,
                })
                return

            try:
                size = self.out_path.stat().st_size
            except OSError:
                size = 0
            if size != last_size:
                last_size = size
                frac = (size / self.total_bytes) if self.total_bytes else 0.0
                frac = max(0.0, min(0.999, frac))
                if size == 0:
                    stage = "Waiting for Terminal — enter your password there."
                else:
                    stage = (f"Imaging via Terminal… "
                             f"{size / (1024*1024):.1f} MB"
                             + (f" of {self.total_bytes / (1024*1024):.0f} MB"
                                if self.total_bytes else ""))
                self._emit(self.EVENT_PROGRESS, ImagingProgress(
                    bytes_written=size,
                    total_bytes=self.total_bytes,
                    fraction=frac,
                    stage=stage,
                ))
            time.sleep(0.5)
