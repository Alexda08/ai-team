# Project Plan

## Objective

Build a production-ready Task Manager system in Python with JSON persistence, strict validation, filtering, undo/redo capabilities, cross-platform file locking, and conflict resolution.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CLI (Entry Point)                     │
├─────────────────────────────────────────────────────────┤
│              Application Layer (TaskManager)             │
│  - CRUD orchestration                                   │
│  - Undo stack management                                │
│  - Conflict detection & resolution                      │
│  - Safe save with retry logic                           │
├─────────────────────────────────────────────────────────┤
│               Domain Layer                               │
│  - Task entity (dataclass)                              │
│  - Status/Priority enums                                │
│  - Validation rules                                      │
│  - Filter logic                                          │
├─────────────────────────────────────────────────────────┤
│             Persistence Layer (TaskRepository)            │
│  - JSON file I/O                                        │
│  - File locking (cross-platform)                        │
│  - Corruption handling with backup                      │
│  - Atomic writes                                        │
└─────────────────────────────────────────────────────────┘
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `models.py` | Task dataclass, Status/Priority enums, exceptions |
| `repository.py` | TaskRepository, FileLock, JSON persistence, file locking |
| `manager.py` | TaskManager (CRUD, filters, undo, conflict resolution) |
| `cli.py` | Interactive command-line interface |
| `test_task_manager.py` | pytest test suite |

## Implementation Steps

### Step 1: Domain Layer - Models
**Description**: Create the foundational domain classes and enums.
- Define `Status` enum: `PENDING`, `IN_PROGRESS`, `DONE`
- Define `Priority` enum: `LOW`, `MEDIUM`, `HIGH`
- Create `Task` dataclass with fields: `id` (UUID), `title`, `description`, `status`, `priority`, `created_at`, `updated_at`
- Define custom exceptions: `TaskValidationError`, `DuplicateTitleError`, `TaskNotFoundError`, `UndoUnavailableError`, `ConflictError`, `BackupNotSavedError`
- Define constants: `MAX_TITLE_LENGTH=200`, `MAX_DESCRIPTION_LENGTH=5000`
- Implement `TaskConflict` dataclass for conflict resolution

**Dependencies**: None

---

### Step 2: Persistence Layer - FileLock
**Description**: Implement cross-platform file locking mechanism.
- Create `FileLock` class with implementation priority: `portalocker` → `fcntl` → `msvcrt` → `noop`
- Use `getattr(portalocker, 'LOCK_NB', 0)` for non-blocking flag compatibility
- Implement timeout with retry loop using `time.monotonic()`
- Add exponential backoff for msvcrt fallback
- Protect against reacquire without release (raise `RuntimeError`)
- Track `has_real_locking` property
- Track `lock_type` property

**Dependencies**: Step 1

---

### Step 3: Persistence Layer - TaskRepository
**Description**: Implement JSON persistence with corruption handling.
- Create `TaskRepository` class with:
  - `load()`: Read JSON, handle corruption with try/except, fallback to empty list
  - `_backup_corrupted()`: Rename corrupt file to `.json.corrupted`
  - `_deserialize()`: Full validation of all fields (id, title, status, priority, dates)
  - `_serialize()`: Convert Task to JSON-compatible dict
  - `save()`: Atomic write using temp file + `os.replace()`
  - `load_warnings` property: Return list of ignored malformed tasks
- Implement `ConcurrentModificationError` detection by comparing task IDs
- Use `FileLock` for exclusive access during writes

**Dependencies**: Step 1, Step 2

---

### Step 4: Application Layer - UndoStack
**Description**: Implement undo/redo stack mechanism.
- Create `OperationType` enum: `CREATE`, `UPDATE`, `DELETE`
- Create `UndoEntry` dataclass: `operation`, `task_snapshot` (deepcopy), `task_id`, `affected_index`
- Create `UndoStack` class with:
  - `push()`: Add entry, evict oldest if at `max_size` (default 50)
  - `pop()`: Return and remove last entry
  - `is_empty()`: Check if stack is empty
  - `size()`: Return current count
  - `remaining()`: Return slots until limit
  - `clear()`: Empty the stack
- Track `max_size` and warn when oldest entry is dropped

**Dependencies**: Step 1

---

### Step 5: Application Layer - TaskManager Core
**Description**: Implement TaskManager with CRUD operations and validation.
- Create `TaskManager` class:
  - `__init__()`: Initialize repository, load tasks, create undo stack, store startup warnings
  - Validation methods: `_validate_title()`, `_validate_unique_title()`, `_validate_status()`, `_validate_priority()`
  - CRUD: `create()`, `get()`, `update()`, `delete()`, `list_all()`
  - Filters: `filter()` with status, priority, search (case-insensitive), limit/offset pagination
- Implement strict validation:
  - Empty or whitespace-only titles → `TaskValidationError`
  - Duplicate titles (case-insensitive) → `DuplicateTitleError`
  - Invalid status/priority values → `TaskValidationError`
  - Length limits: title ≤ 200 chars, description ≤ 5000 chars
- All mutations save via `_safe_save()`

**Dependencies**: Step 1, Step 3, Step 4

---

### Step 6: Application Layer - Safe Save & Conflict Resolution
**Description**: Implement safe save with retry, merge, and conflict detection.
- Implement `_create_emergency_backup()`: Save to `tempfile.gettempdir()/taskmanager_backups/`
- Implement `_safe_save()`:
  - Retry loop (configurable `max_retries`, default 2)
  - On `ConcurrentModificationError`: create backup, reload external tasks, detect conflicts
  - Reload `external_tasks` on each retry iteration
  - Show `load_warnings` after each reload
  - If conflicts detected → raise `ConflictError` and clear undo stack
  - If no conflicts → merge and retry
  - Log and raise `BackupNotSavedError` if all retries fail
- Implement `_detect_conflicts()`: Compare tasks with same ID but different content
- Implement `_merge_without_conflicts()`: Add external tasks with new IDs only
- Implement `resolve_conflict(task_id, resolution)`: Handle `'local'`, `'external'`, `'skip'`
- Implement `resolve_all_conflicts(resolution)`: Batch resolution using `_safe_save()`

**Dependencies**: Step 5

---

### Step 7: Application Layer - Undo Operations
**Description**: Implement undo functionality integrated with save system.
- Implement `undo()`:
  - Pop from undo stack, apply inverse operation
  - `CREATE`: Remove task from list
  - `UPDATE`: Restore task snapshot at original index
  - `DELETE`: Reinsert task at `affected_index` (or append)
  - Handle duplicate title on DELETE undo: rename to `"Title (restored #N)"`
  - Save via `_safe_save()`
- Implement `undo_status()`: Return dict with `available`, `count`, `remaining_slots`, `limit`
- Warn user when undo limit is reached

**Dependencies**: Step 5, Step 6

---

### Step 8: CLI - Core Interface
**Description**: Build interactive command-line menu.
- Create menu with options: Create, List, View, Update, Delete, Filter, Undo, Exit
- Implement each operation with proper input prompting
- Display tasks with status/priority icons (⏳🔄✅ / 🔵🟡🔴)
- Handle all exceptions with user-friendly messages:
  - `TaskValidationError`, `DuplicateTitleError`, `TaskNotFoundError`
  - `UndoUnavailableError`, `TimeoutError`
- Show `startup_warnings` on program launch
- Implement pagination input: `[Enter=sin límite]`

**Dependencies**: Step 7

---

### Step 9: CLI - Conflict Resolution Mode
**Description**: Implement interactive conflict resolution in CLI.
- On `ConflictError`:
  - Display all conflicts with field differences
  - Show resolution options: `resolve <id> local|external|skip`, `resolve-all`, `cancel`
  - Implement interactive loop until all conflicts resolved or cancelled
  - Reuse existing backup path from exception
  - On cancel, inform user of backup location
- Implement separate command `r` for manual conflict checking
- On `BackupNotSavedError`: display backup path and recovery instructions
- On `TimeoutError`: explain that another process may have the lock

**Dependencies**: Step 8

---

### Step 10: Testing - Unit Tests
**Description**: Create comprehensive pytest test suite.
- Fixtures: `temp_file`, `manager`
- Test classes:
  - `TestCreate`: Basic creation, custom status/priority, empty title, duplicate title (case-insensitive), invalid status/priority, persistence
  - `TestRead`: Get existing task, nonexistent task
  - `TestUpdate`: Update title, partial updates, validation errors, nonexistent task
  - `TestDelete`: Delete existing, nonexistent
  - `TestFilters`: By status, by priority, by search (title/description), combined filters, no matches, case-insensitive search
  - `TestUndo`: Create undo, update undo, delete undo, empty stack, multiple undos, order preservation
  - `TestEdgeCases`: Whitespace trimming, empty database, updated_at changes, concurrent access, empty search normalization
- Test `ConflictError` handling and resolution
- Test `BackupNotSavedError` scenario
- Note: `test_create_with_custom_status_priority` must use `Status.IN_PROGRESS`, NOT `Status.HIGH`

**Dependencies**: All previous steps

---

### Step 11: Project Structure & Configuration
**Description**: Organize files and configure project.
- Create directory structure:
  ```
  task_manager/
  ├── __init__.py
  ├── models.py
  ├── repository.py
  ├── manager.py
  ├── cli.py
  ├── test_task_manager.py
  └── tasks.json
  ```
- Add `__main__.py` for `python -m task_manager` execution
- Create `requirements.txt`: `pytest`, `portalocker` (recommended for Windows)
- Create `README.md` with usage instructions
- Add `.gitignore` for `tasks.json` and `*.json.corrupted*`
- Add type hints throughout for IDE support

**Dependencies**: Step 10

---

## Decisions

| Decision | Rationale |
|----------|-----------|
| Enums for Status/Priority | Type safety, IDE autocompletion, validation at parse time |
| deepcopy for undo snapshots | Prevent accidental mutation of saved state |
| UndoStack max_size=50 | Balance between memory usage and undo capability |
| File locking priority: portalocker → fcntl → msvcrt → noop | Robustness across platforms; portalocker is cross-platform, fcntl is Unix-standard, msvcrt is limited fallback |
| Atomic writes with os.replace() | `os.replace()` is atomic on all platforms (Python 3.3+) |
| ConflictError with manual resolution | User data should never be silently overwritten |
| Backup to temp directory | Guarantees write access, separates from working directory |
| Length limits: 200/5000 chars | Reasonable limits to prevent UI/performance issues |
| Retry count=2 default | Quick failover for transient conflicts, user intervention for persistent ones |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| JSON corruption on disk | Data loss | Auto-backup to `.json.corrupted`, graceful fallback to empty list |
| Concurrent file access | Data loss / corruption | File locking with timeout, conflict detection, merge for non-conflicting changes |
| msvcrt fallback limited locking | Race condition on Windows | Document limitation, recommend `pip install portalocker`, detect and warn |
| Undo stack overflow | Lost undo history | Warn user when limit reached, clear stack on conflict errors |
| Duplicate title after undo | Undo fails silently | Auto-rename to `"Title (restored #N)"` pattern |
| Memory with large task lists | Performance degradation | Pagination with limit/offset, no forced loading of all tasks |

## Timeline

| Phase | Steps | Complexity |
|-------|-------|------------|
| Core Implementation | 1-5 | Medium |
| Robustness Features | 6-7 | High |
| CLI & UX | 8-9 | Medium |
| Testing & Polish | 10-11 | Low |

**Estimated Total**: ~400 lines of core code + ~300 lines of tests + ~200 lines of CLI