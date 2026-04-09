"""Trace storage and collection system.

Records step-by-step execution data in SQLite with FTS5 search.
TraceCollector subscribes to EventBus events and builds traces automatically.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Dict, Any
import sqlite3
import json
import time
import uuid
import os


class StepType(Enum):
    ROUTE = "route"
    GENERATE = "generate"
    TOOL_CALL = "tool_call"
    VALIDATE = "validate"
    RESPOND = "respond"
    PHASE = "phase"


@dataclass
class TraceStep:
    step_type: StepType
    agent: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0
    input_summary: str = ""
    output_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    query: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    steps: List[TraceStep] = field(default_factory=list)
    status: str = "in_progress"  # in_progress, completed, failed
    outcome: Optional[str] = None
    feedback: Optional[float] = None
    total_tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_step(self, step: TraceStep) -> None:
        self.steps.append(step)

    @property
    def duration_ms(self) -> float:
        if self.ended_at:
            return (self.ended_at - self.started_at) * 1000
        return (time.time() - self.started_at) * 1000


class TraceStore:
    """Append-only SQLite trace store with FTS5 search."""

    def __init__(self, db_path: str = "traces/traces.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT UNIQUE NOT NULL,
                query TEXT DEFAULT '',
                status TEXT DEFAULT 'in_progress',
                outcome TEXT,
                feedback REAL,
                started_at REAL,
                ended_at REAL,
                total_tokens INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                created_at REAL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS trace_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_type TEXT NOT NULL,
                agent TEXT DEFAULT '',
                timestamp REAL,
                duration_ms REAL DEFAULT 0,
                input_summary TEXT DEFAULT '',
                output_summary TEXT DEFAULT '',
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
            );

            CREATE INDEX IF NOT EXISTS idx_steps_trace ON trace_steps(trace_id);
            CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
            CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at);
        """)

        # FTS5 for full-text search (may already exist)
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts USING fts5(
                    trace_id, query, outcome, content=traces, content_rowid=id
                )
            """)
        except sqlite3.OperationalError:
            pass  # FTS5 not available on this build

        self._conn.commit()

    def save(self, trace: Trace) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO traces
               (trace_id, query, status, outcome, feedback, started_at, ended_at, total_tokens, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trace.trace_id, trace.query, trace.status, trace.outcome,
             trace.feedback, trace.started_at, trace.ended_at,
             trace.total_tokens, json.dumps(trace.metadata)),
        )

        # Delete old steps then re-insert (simpler than upsert)
        self._conn.execute("DELETE FROM trace_steps WHERE trace_id = ?", (trace.trace_id,))
        for i, step in enumerate(trace.steps):
            self._conn.execute(
                """INSERT INTO trace_steps
                   (trace_id, step_index, step_type, agent, timestamp, duration_ms,
                    input_summary, output_summary, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trace.trace_id, i, step.step_type.value, step.agent,
                 step.timestamp, step.duration_ms,
                 step.input_summary[:2000], step.output_summary[:2000],
                 json.dumps(step.metadata)),
            )

        # Update FTS
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO traces_fts(trace_id, query, outcome) VALUES (?, ?, ?)",
                (trace.trace_id, trace.query, trace.outcome or ""),
            )
        except sqlite3.OperationalError:
            pass

        self._conn.commit()

    def get(self, trace_id: str) -> Optional[Trace]:
        row = self._conn.execute(
            "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_trace(row)

    def list_traces(
        self,
        status: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 100,
        since: Optional[float] = None,
    ) -> List[Trace]:
        query = "SELECT * FROM traces WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        if since:
            query += " AND started_at >= ?"
            params.append(since)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_trace(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> List[Trace]:
        try:
            rows = self._conn.execute(
                """SELECT t.* FROM traces t
                   JOIN traces_fts f ON t.trace_id = f.trace_id
                   WHERE traces_fts MATCH ?
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            return [self._row_to_trace(r) for r in rows]
        except sqlite3.OperationalError:
            # FTS not available, fall back to LIKE
            rows = self._conn.execute(
                "SELECT * FROM traces WHERE query LIKE ? LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
            return [self._row_to_trace(r) for r in rows]

    def update_feedback(self, trace_id: str, score: float) -> bool:
        cur = self._conn.execute(
            "UPDATE traces SET feedback = ? WHERE trace_id = ?",
            (score, trace_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]

    def _row_to_trace(self, row) -> Trace:
        trace = Trace(
            trace_id=row[1],
            query=row[2] or "",
            status=row[3] or "in_progress",
            outcome=row[4],
            feedback=row[5],
            started_at=row[6] or 0,
            ended_at=row[7],
            total_tokens=row[8] or 0,
            metadata=json.loads(row[9]) if row[9] else {},
        )
        # Load steps
        step_rows = self._conn.execute(
            "SELECT * FROM trace_steps WHERE trace_id = ? ORDER BY step_index",
            (trace.trace_id,),
        ).fetchall()
        for sr in step_rows:
            trace.steps.append(TraceStep(
                step_type=StepType(sr[3]),
                agent=sr[4] or "",
                timestamp=sr[5] or 0,
                duration_ms=sr[6] or 0,
                input_summary=sr[7] or "",
                output_summary=sr[8] or "",
                metadata=json.loads(sr[9]) if sr[9] else {},
            ))
        return trace

    def close(self):
        self._conn.close()


class TraceCollector:
    """Subscribes to EventBus and automatically builds Trace objects."""

    def __init__(self, event_bus, trace_store: TraceStore):
        self._event_bus = event_bus
        self._store = trace_store
        self._current_trace: Optional[Trace] = None
        self._step_starts: Dict[str, float] = {}
        self._subscribe()

    def _subscribe(self):
        from core.events import EventType
        self._event_bus.subscribe(EventType.PHASE_START, self._on_phase_start)
        self._event_bus.subscribe(EventType.PHASE_END, self._on_phase_end)
        self._event_bus.subscribe(EventType.AGENT_TURN_START, self._on_agent_turn_start)
        self._event_bus.subscribe(EventType.AGENT_TURN_END, self._on_agent_turn_end)
        self._event_bus.subscribe(EventType.TOOL_CALL_START, self._on_tool_start)
        self._event_bus.subscribe(EventType.TOOL_CALL_END, self._on_tool_end)
        self._event_bus.subscribe(EventType.TASK_START, self._on_task_start)
        self._event_bus.subscribe(EventType.TASK_END, self._on_task_end)

    def start_trace(self, query: str = "", metadata: Optional[Dict] = None) -> str:
        self._current_trace = Trace(query=query, metadata=metadata or {})
        return self._current_trace.trace_id

    def end_trace(self, status: str = "completed", outcome: Optional[str] = None) -> Optional[Trace]:
        if not self._current_trace:
            return None
        self._current_trace.status = status
        self._current_trace.outcome = outcome
        self._current_trace.ended_at = time.time()
        self._store.save(self._current_trace)
        trace = self._current_trace
        self._current_trace = None
        return trace

    @property
    def current_trace(self) -> Optional[Trace]:
        return self._current_trace

    def _add_step(self, step: TraceStep):
        if self._current_trace:
            self._current_trace.add_step(step)

    def _on_phase_start(self, event):
        self._add_step(TraceStep(
            step_type=StepType.PHASE,
            input_summary=f"Phase: {event.data.get('phase', '')} started",
            timestamp=event.timestamp,
        ))

    def _on_phase_end(self, event):
        self._add_step(TraceStep(
            step_type=StepType.PHASE,
            output_summary=f"Phase: {event.data.get('phase', '')} {event.data.get('status', '')}",
            timestamp=event.timestamp,
        ))

    def _on_agent_turn_start(self, event):
        agent = event.data.get("agent", "")
        self._step_starts[f"agent_{agent}"] = event.timestamp

    def _on_agent_turn_end(self, event):
        agent = event.data.get("agent", "")
        start = self._step_starts.pop(f"agent_{agent}", event.timestamp)
        self._add_step(TraceStep(
            step_type=StepType.GENERATE,
            agent=agent,
            timestamp=start,
            duration_ms=(event.timestamp - start) * 1000,
            output_summary=event.data.get("output_summary", ""),
        ))

    def _on_tool_start(self, event):
        tool = event.data.get("tool", "")
        self._step_starts[f"tool_{tool}"] = event.timestamp

    def _on_tool_end(self, event):
        tool = event.data.get("tool", "")
        start = self._step_starts.pop(f"tool_{tool}", event.timestamp)
        self._add_step(TraceStep(
            step_type=StepType.TOOL_CALL,
            agent=event.data.get("agent", ""),
            timestamp=start,
            duration_ms=event.data.get("duration_ms", (event.timestamp - start) * 1000),
            input_summary=f"Tool: {tool}",
            output_summary=f"success={event.data.get('success', '')}",
            metadata={"tool": tool},
        ))

    def _on_task_start(self, event):
        self._step_starts["task"] = event.timestamp

    def _on_task_end(self, event):
        start = self._step_starts.pop("task", event.timestamp)
        self._add_step(TraceStep(
            step_type=StepType.VALIDATE,
            timestamp=start,
            duration_ms=(event.timestamp - start) * 1000,
            input_summary=f"Task: {event.data.get('task_id', '')}",
            output_summary=f"success={event.data.get('success', '')}",
        ))
