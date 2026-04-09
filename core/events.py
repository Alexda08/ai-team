from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any
import threading
import time


class EventType(Enum):
    # Phase lifecycle
    PHASE_START = auto()
    PHASE_END = auto()

    # Agent turns
    AGENT_TURN_START = auto()
    AGENT_TURN_END = auto()

    # Message bus
    MESSAGE_PUBLISHED = auto()

    # LLM inference
    INFERENCE_START = auto()
    INFERENCE_END = auto()

    # Tool execution
    TOOL_CALL_START = auto()
    TOOL_CALL_END = auto()

    # Trace lifecycle
    TRACE_STEP = auto()
    TRACE_COMPLETE = auto()

    # Validation
    VALIDATION_START = auto()
    VALIDATION_END = auto()

    # Task execution
    TASK_START = auto()
    TASK_END = auto()

    # Evaluation
    EVAL_START = auto()
    EVAL_END = auto()

    # Security
    CAPABILITY_DENIED = auto()
    BOUNDARY_BLOCKED = auto()

    # Learning
    LEARNING_CYCLE_START = auto()
    LEARNING_CYCLE_END = auto()


Subscriber = Callable[["Event"], None]


@dataclass
class Event:
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Subscribers are invoked synchronously in registration order.
    Optional history recording for testing/debugging.
    """

    def __init__(self, record_history: bool = False):
        self._subscribers: Dict[EventType, List[Subscriber]] = {}
        self._lock = threading.Lock()
        self._record_history = record_history
        self._history: List[Event] = []

    def subscribe(self, event_type: EventType, callback: Subscriber) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Subscriber) -> None:
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                except ValueError:
                    pass

    def publish(self, event_type: EventType, data: Optional[Dict[str, Any]] = None) -> Event:
        event = Event(event_type=event_type, data=data or {})
        with self._lock:
            if self._record_history:
                self._history.append(event)
            listeners = list(self._subscribers.get(event_type, []))

        for callback in listeners:
            try:
                callback(event)
            except Exception as e:
                print(f"[EventBus] Subscriber error on {event_type.name}: {e}")

        return event

    @property
    def history(self) -> List[Event]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()


# Module-level singleton
_bus: Optional[EventBus] = None


def get_event_bus(record_history: bool = False) -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(record_history=record_history)
    return _bus


def reset_event_bus() -> None:
    global _bus
    _bus = None
