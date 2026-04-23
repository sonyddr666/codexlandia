from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class QueueEvent:
    kind: str
    payload: dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self.queue: queue.Queue[QueueEvent] = queue.Queue()

    def emit(self, kind: str, **payload: Any) -> None:
        self.queue.put(QueueEvent(kind=kind, payload=payload))


class SerializedRunner:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._lock = threading.Lock()
        self._active_name: str | None = None

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_name is not None

    def run(self, name: str, target: Callable[[], Any]) -> bool:
        with self._lock:
            if self._active_name is not None:
                return False
            self._active_name = name
        thread = threading.Thread(target=self._execute, args=(name, target), daemon=True)
        thread.start()
        return True

    def _execute(self, name: str, target: Callable[[], Any]) -> None:
        self.bus.emit("worker_started", name=name)
        try:
            result = target()
            self.bus.emit("worker_result", name=name, result=result)
        except Exception as exc:  # noqa: BLE001
            self.bus.emit("worker_error", name=name, error=str(exc))
        finally:
            with self._lock:
                self._active_name = None
            self.bus.emit("worker_finished", name=name)


class AutoRefreshController:
    def __init__(self, interval_seconds: int = 10) -> None:
        self.interval_seconds = interval_seconds
        self.enabled = False
        self._scheduled = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if not enabled:
            self._scheduled = False

    def request_schedule(self, worker_busy: bool) -> bool:
        if not self.enabled or worker_busy or self._scheduled:
            return False
        self._scheduled = True
        return True

    def mark_fired(self) -> None:
        self._scheduled = False
