# Project Plan

## Objective

Build a complete Python task management system (todo-list) with JSON persistence, CRUD operations, filtering, validation, undo system, interactive CLI, and comprehensive unit tests.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         CLI Layer                          │
│                    (interactive menu)                      │
├─────────────────────────────────────────────────────────────┤
│                      TaskManager                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐ │
│  │   CRUD   │  │  Filter  │  │  Undo    │  │  Validation │ │
│  │ Service  │  │ Service  │  │  Stack   │  │   Rules     │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
├───────┴─────────────┴─────────────┴────────────────────────┤
│                    JSONPersistence                         │
│              (tasks.json + tasks.bak)                      │
└─────────────────────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `models.py` | Task, TaskStatus, TaskPriority dataclasses/enums |
| `exceptions.py` | ValidationError, CLIError, SystemError |
| `persistence.py` | JSON load/save with atomic writes and recovery |
| `validators.py` | Title, description, status, priority validation |
| `undo.py` | UndoManager with snapshot-based undo stack |
| `task_manager.py` | Core CRUD + filtering orchestration |
| `cli.py` | Interactive menu-driven interface |
| `tests/` | pytest unit tests |

## Implementation Steps

### Step 1: Models and Enums
- **Description**: Define `TaskStatus` enum (pending, in_progress, done), `TaskPriority` enum (low, medium, high), and `Task` dataclass with all required fields: id (UUID), title, description, status, priority, created_at, updated_at.
- **Dependencies**: None

### Step 2: Custom Exceptions
- **Description**: Create `ValidationError` (for invalid inputs), `CLIError` (for recoverable CLI errors), `SystemError` (for irrecuperable errors). Define exit codes: 0 (normal), 1 (system error), 2 (invalid arguments).
- **Dependencies**: None

### Step 3: Validators
- **Description**: Implement validation functions with ordered checks:
  1. Empty/whitespace → "El título no puede estar vacío"
  2. Max length (200 for title, 5000 for description) → "El título no puede superar X caracteres"
  3. Duplicate title → "Ya existe una tarea con ese título"
- **Dependencies**: Step 1, Step 2

### Step 4: JSON Persistence with Atomic Writes
- **Description**: Implement `JSONPersistence` class with:
  - `load()`: Read tasks.json, detect JSONDecodeError, trigger recovery
  - `save()`: Sequence — (1) backup if file exists, (2) write to temp file with fsync, (3) atomic replace
  - `_handle_corruption()`: Auto-recovery from .bak if valid, else interactive prompt
  - Auto-create empty file if tasks.json doesn't exist
- **Dependencies**: Step 2

### Step 5: Undo System
- **Description**: Implement `UndoManager` with:
  - `UndoAction` dataclass: action_type (create/update/delete), task_snapshot (full task state), timestamp
  - `record()`: Push action to stack (max 50 items)
  - `undo()`: Pop and return last action (no redo)
  - Stack does NOT persist across program restarts
- **Dependencies**: Step 1

### Step 6: TaskManager Core
- **Description**: Implement `TaskManager` class with:
  - CRUD operations: `create()`, `read()`, `update()`, `delete()`
  - `filter_tasks()`: status, priority, search_text parameters; match_all=True for AND logic; case_insensitive search by default
  - `undo()`: Delegate to UndoManager, restore task state based on action_type
  - Integration with persistence layer
  - All operations validate inputs before executing
- **Dependencies**: Step 1, Step 3, Step 4, Step 5

### Step 7: CLI Interface
- **Description**: Implement interactive CLI with numbered menu:
  - Options 1-9: Create, List, View, Update, Delete, Search, Stats, Help, Exit
  - Option 0: Undo (only visible when stack > 0, shows count)
  - Error handling: CLIError prints warning and continues, SystemError prints error and exits(1)
  - KeyboardInterrupt handled gracefully
- **Dependencies**: Step 6

### Step 8: Unit Tests
- **Description**: Write pytest tests covering edge cases:
  - `test_create_duplicate_title_raises`
  - `test_create_empty_title_raises`
  - `test_create_whitespace_title_raises`
  - `test_create_invalid_status_raises`
  - `test_create_invalid_priority_raises`
  - `test_delete_nonexistent_raises`
  - `test_update_nonexistent_raises`
  - `test_undo_with_empty_stack_raises`
  - `test_undo_create_restores_task`
  - `test_undo_delete_restores_task`
  - `test_undo_update_restores_previous_state`
  - `test_filter_by_status`
  - `test_filter_by_priority`
  - `test_filter_by_text`
  - `test_filter_combined_and`
  - `test_filter_combined_or`
  - `test_filter_case_insensitive`
  - `test_title_exceeds_max_length`
  - `test_corruption_recovery_from_backup`
- **Dependencies**: Step 6

## Decisions

| Decision | Justification |
|----------|---------------|
| Enums for status/priority | Type safety, autocomplete, prevents typos |
| Dataclass for Task | Mutable for updates, more readable than dict |
| Snapshot-based undo | Simpler logic than inverse operations, covers all cases |
| JSON over SQLite | Human-readable, no external dependencies, sufficient for this scale |
| UUID over auto-increment | No collision in distributed scenarios |
| 50-item undo limit | Prevents memory issues, reasonable for CLI usage |
| Undo stack in memory only | Persisting requires serializing original timestamps, added complexity without proportional benefit |
| AND as default filter | Most common use case ("pending high priority tasks") |
| Case-insensitive search | Better UX, user expectation |
| Backup before write | Guarantees recovery from crash at any point |

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| JSON corruption mid-write | Low | High | Atomic write (temp + rename) + .bak |
| Undo stack memory growth | Low | Low | 50-item hard limit |
| Concurrent access race | Low | Medium | Document as single-user tool; file locking if needed |
| Recovery fails | Very Low | High | Interactive prompt allows manual intervention |
| Search performance with large dataset | Low | Low | Linear scan acceptable for typical use (<10k tasks) |

## Timeline

**Estimated Total**: 6-8 hours

| Step | Estimated Time |
|------|----------------|
| Step 1: Models and Enums | 30 min |
| Step 2: Custom Exceptions | 15 min |
| Step 3: Validators | 30 min |
| Step 4: JSON Persistence | 1 hour |
| Step 5: Undo System | 45 min |
| Step 6: TaskManager Core | 1.5 hours |
| Step 7: CLI Interface | 1 hour |
| Step 8: Unit Tests | 1.5 hours |