"""Test helper (MD-162): pretend a background process starts or stops, by holding the real single-instance lock the way `background.run` does."""
from __future__ import annotations

import os
import threading
import time

from mdpipeline import background, filelock


class FakeBackgroundProcess:
    """Holds ``background.lock`` from ``start_after`` seconds until ``stop()``; ``is_running()`` then reports True, like the real process."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, start_after: float = 0.0) -> None:
        def hold() -> None:
            if self._stop.wait(start_after):
                return
            path = background._lock_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                filelock.try_lock(fd)
                self._stop.wait(120)
            finally:
                os.close(fd)

        self._thread = threading.Thread(target=hold, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            time.sleep(0.05)
