# Project Plan

## Objective
Build a minimal REST API in Node.js with pure TypeScript (no framework, using native http module) for task management with three endpoints: GET /tasks, POST /tasks, and DELETE /tasks/:id.

## Architecture

```
/src
├── types/
│   └── index.ts           # Shared types: Task, Priority, ApiResponse, custom errors
├── utils/
│   ├── http.ts            # HTTP utilities: readBody(), respond()
│   └── validation.ts      # Input validation: validateTaskInput()
├── services/
│   └── taskService.ts     # Business logic: CRUD operations on Map<string, Task>
├── router/
│   └── index.ts           # Router class: regex-based pattern matching
└── server.ts              # Entry point: http.createServer, graceful shutdown
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `types/index.ts` | Define Task, Priority, ApiResponse interfaces; ValidationError and NotFoundError classes |
| `utils/http.ts` | `readBody()` for async body parsing, `respond()` for consistent JSON responses with headers |
| `utils/validation.ts` | `validateTaskInput()` validates title (1-200 chars, trimmed), priority (enum), dueDate (valid ISO 8601, future) |
| `services/taskService.ts` | In-memory `Map<string, Task>` storage; methods: getAll(), create(), deleteById() |
| `router/index.ts` | Router class with `handle()` method, routes registered via regex patterns |
| `server.ts` | Creates HTTP server, wires router, handles graceful shutdown |

## Implementation Steps

### Step 1: Define Types
- **File**: `src/types/index.ts`
- **Description**: Define TypeScript interfaces and custom error classes
  - `Priority = "low" | "medium" | "high"`
  - `Task { id, title, priority, dueDate, createdAt }`
  - `ApiResponse<T> { success, data?, error? }`
  - `ValidationResult { valid, data?, error? }`
  - `ValidationError extends Error` for validation failures
  - `NotFoundError extends Error` for missing resources
- **Dependencies**: None

### Step 2: Create HTTP Utilities
- **File**: `src/utils/http.ts`
- **Description**: Implement `readBody(req)` returning Promise<string> by collecting chunks from `req.on('data')` and resolving on `req.on('end')`. Implement `respond(res, status, data)` setting `Content-Type: application/json` header and writing JSON response.
- **Dependencies**: Step 1 (types for error handling)

### Step 3: Implement Validation
- **File**: `src/utils/validation.ts`
- **Description**: Implement `validateTaskInput(body: unknown)`:
  - Check body is object
  - Validate title: string, trim(), 1-200 chars
  - Validate priority: must be "low" | "medium" | "high"
  - Validate dueDate: parse with `new Date()`, check `isNaN()`, check `> new Date()`
  - Return `{ valid: true, data }` or `{ valid: false, error: string }`
- **Dependencies**: Step 1 (types)

### Step 4: Build Task Service
- **File**: `src/services/taskService.ts`
- **Description**: 
  - Private `tasks: Map<string, Task>` storage
  - `getAll(): Task[]` returns all tasks as array
  - `create(data: { title, priority, dueDate }): Task` generates UUID with `crypto.randomUUID()`, adds `createdAt`, throws ValidationError if validation fails
  - `deleteById(id: string): Task` removes from Map, throws NotFoundError if missing
- **Dependencies**: Step 1 (types), Step 3 (validation)

### Step 5: Create Router
- **File**: `src/router/index.ts`
- **Description**: 
  - `Router` class with routes array: `{ method, pattern: RegExp, handler }`
  - Routes: `GET /^\\/tasks\\/?$/`, `POST /^\\/tasks\\/?$/`, `DELETE /^\\/tasks\\/(?<id>.+)$/`
  - `handle(req, res)`: reads body, iterates routes, matches method+pattern, extracts `match.groups?.id`, calls handler in try/catch
  - Error handling: ValidationError → 400, NotFoundError → 404, unknown → 500
- **Dependencies**: Step 1, Step 2, Step 4

### Step 6: Create Server Entry Point
- **File**: `src/server.ts`
- **Description**:
  - Import router, create `http.createServer((req, res) => router.handle(req, res))`
  - Listen on port from env (default 3000)
  - Graceful shutdown: close server on SIGTERM/SIGINT
  - Export for testing if needed
- **Dependencies**: Step 5

### Step 7: Configure TypeScript and Package
- **Files**: `tsconfig.json`, `package.json`
- **Description**:
  - tsconfig: target ES2022, module NodeNext, moduleResolution NodeNext, strict: true
  - package.json: scripts `{ "start": "ts-node --esm src/server.ts" }`
- **Dependencies**: None (parallel with steps 1-6)

## Risks

| Risk | Mitigation |
|------|------------|
| Memory persistence: data lost on restart | Document as intentional limitation; can add file/database layer later |
| UUID format mismatch in regex | Use `(?<id>.+)` to capture any non-empty string; service validates UUID format if needed |
| Timezone edge cases in dueDate validation | Use `new Date()` (UTC) consistently; document that comparison is in server timezone |
| Large request body causing memory issues | Set Content-Length limit or truncate in `readBody()` |
| No request timeout | Add timeout in server.ts for long-running connections |

## Timeline

| Step | Effort | Priority |
|------|--------|----------|
| 1. Types | Low | Immediate |
| 2. HTTP Utils | Low | Immediate |
| 3. Validation | Medium | Immediate |
| 4. Service | Medium | After Step 3 |
| 5. Router | Medium | After Step 4 |
| 6. Server | Low | After Step 5 |
| 7. Config | Low | Parallel |

Total estimated implementation time: 2-3 hours