"""WindowedSketch: sliding-window quantile estimation over DDSketch pairs."""

from __future__ import annotations

import asyncio
import math
import threading

from hedge.sketch._ddsketch import DDSketch

_DEFAULT_WINDOW_DURATION = 30.0  # seconds


class WindowedSketch:
    """Maintains a sliding window over two DDSketches that rotate periodically.

    Quantile queries merge both sketches, giving a window that spans
    1x to 2x the configured duration. Add always writes to the current sketch.

    The rotation scheme::

        t=0:  current=A, previous=empty
        t=30: current=B, previous=A       (A covers [0,30))
        t=60: current=C, previous=B       (A is dropped)

    This class is thread-safe and can be used from both sync and async code.
    For async rotation, call ``start_async()`` after creating the sketch.

    Args:
        relative_accuracy: DDSketch relative accuracy (default: 0.01).
        window_duration: Rotation interval in seconds (default: 30.0).
    """

    def __init__(
        self,
        relative_accuracy: float = 0.01,
        window_duration: float = _DEFAULT_WINDOW_DURATION,
    ) -> None:
        if window_duration <= 0:
            window_duration = _DEFAULT_WINDOW_DURATION
        self._relative_accuracy = relative_accuracy
        self._window_duration = window_duration
        self._lock = threading.Lock()
        self._current = DDSketch(relative_accuracy)
        self._previous = DDSketch(relative_accuracy)
        # Sync rotation
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Async rotation
        self._async_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start background rotation thread (for sync usage)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._rotate_loop, daemon=True)
        self._thread.start()

    def start_async(self) -> None:
        """Start background rotation as an asyncio task (for async usage).

        Must be called from within a running event loop.
        """
        if self._async_task is not None:
            return
        self._async_task = asyncio.ensure_future(self._rotate_loop_async())

    def stop(self) -> None:
        """Stop the background rotation thread and wait for it to exit."""
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join()
            self._thread = None
        if self._async_task is not None:
            self._async_task.cancel()
            self._async_task = None

    def add(self, value: float) -> None:
        """Record a latency sample (in seconds) to the current sketch."""
        with self._lock:
            self._current.add(value)

    def quantile(self, q: float) -> float:
        """Return the estimated quantile q in [0, 1] over the sliding window.

        Returns math.nan if no data has been recorded.
        """
        with self._lock:
            if self._current.count == 0 and self._previous.count == 0:
                return math.nan
            merged = DDSketch(self._relative_accuracy)
            merged.merge(self._previous)
            merged.merge(self._current)
            return merged.quantile(q)

    def rotate(self) -> None:
        """Manually rotate the window. Mostly useful for testing."""
        with self._lock:
            self._previous = self._current
            self._current = DDSketch(self._relative_accuracy)

    def _rotate_loop(self) -> None:
        """Background thread rotation loop."""
        while not self._stop_event.wait(self._window_duration):
            self.rotate()

    async def _rotate_loop_async(self) -> None:
        """Async rotation loop."""
        try:
            while True:
                await asyncio.sleep(self._window_duration)
                self.rotate()
        except asyncio.CancelledError:
            return
