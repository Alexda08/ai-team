# Project Plan

## Objective

Implement a console-based task manager in Python with add, list, complete, and delete operations, with proper validation and an interactive menu.

## Context

- **Scope**: Single-file Python script, in-memory storage only (no persistence)
- **Language**: Python (type hints used)
- **Target**: Console users on any OS with Python 3.10+
- **Constraints**: Titles limited to 200 chars, case-insensitive duplicate detection, Ctrl+C handling

## Architecture

```
┌─────────────────────────────────────────────┐
│                   menu()                    │
│         (main loop + user input)            │
└─────────────────┬───────────────────────────┘
                  │
        ┌─────────▼──────────┐
        │   TaskManager      │
        │  - tasks: list     │
        │  - next_id: int    │
        │  - add_task()      │
        │  - list_tasks()    │
        │  - complete_task() │
        │  - delete_task()   │
        └─────────┬──────────┘
                  │
        ┌─────────▼──────────┐
        │       Task         │
        │  - id: int         │
        │  - title: str      │
        │  - status: str     │
        └────────────────────┘
                  │
        ┌─────────▼──────────┐
        │  normalize_title() │
        │  (helper function) │
        └────────────────────┘
```

## Modules

| Module | Purpose |
|--------|---------|
| `normalize_title()` | Helper: NFC Unicode, strip, collapse whitespace |
| `Task` | Dataclass representing a single task |
| `TaskManager` | Core logic: CRUD operations, validation, ID management |
| `menu()` | Interactive CLI loop |
| `_leer_id()` | Helper: safe integer input for task IDs |

## Implementation Steps

### Step 1: Define `normalize_title()` Helper
**Description**: Implement the title normalization function that:
- Applies NFC Unicode normalization
- Strips leading/trailing whitespace
- Collapses multiple whitespace characters (including `\u200b`) into single spaces

**Dependencies**: None

### Step 2: Define `Task` Dataclass
**Description**: Create a dataclass with:
- `id: int` — unique identifier
- `title: str` — normalized title
- `status: str` — defaults to "pending"

**Dependencies**: None

### Step 3: Implement `TaskManager` Class
**Description**: Implement the full TaskManager with:
- `MAX_TITLE_LENGTH = 200` constant
- `add_task()` — validates empty/title length/duplicates, returns `tuple[bool, str]`
- `list_tasks()` — prints all tasks with icons (✓/○) and status
- `complete_task()` — idempotent, returns `tuple[bool, str]`
- `delete_task()` — returns `tuple[bool, str]`
- `_find_by_id()` — private helper

**Dependencies**: Step 1, Step 2

### Step 4: Implement CLI Menu
**Description**: Create `menu()` function that:
- Displays numbered menu options (1-5)
- Handles user input for all operations
- Calls `TaskManager` methods and prints returned messages
- Wraps main loop in `try/except KeyboardInterrupt` for graceful Ctrl+C exit

**Dependencies**: Step 3

### Step 5: Add Entry Point
**Description**: Add `if __name__ == "__main__": menu()` to run the program.

**Dependencies**: Step 4

## Decisions

| Decision | Rationale |
|----------|-----------|
| IDs never reused after deletion | Simpler design; no ambiguity when referencing old task IDs |
| Case-insensitive duplicate detection | Prevents "Limpiar" and "limpiar" coexisting |
| `tuple[bool, str]` return type | Caller can distinguish success/failure and handle messages without parsing |
| `complete_task` idempotent | Better UX; no error on repeated completion attempts |
| In-memory only | Minimal scope; persistence out of scope unless requested |

## Risks

| Risk | Mitigation |
|------|------------|
| Unicode edge cases (e.g., `\u200b`) | Handled by `normalize_title()` collapsing all whitespace |
| Very long titles | Rejected at 200+ chars with clear error message |
| Invalid ID input (non-integer) | `_leer_id()` catches `ValueError` and returns `None` |

## Expected Outcome

- Running `python todo.py` displays the menu
- Users can add tasks (rejects empty or duplicate titles)
- Users can list tasks (shows ✓/○ icons)
- Users can complete tasks (idempotent, no error on repeat)
- Users can delete tasks
- Ctrl+C exits gracefully with a message