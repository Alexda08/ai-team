# Project Plan

## Objective
Build a single-page web application for real-time work activity tracking with minimal friction, allowing users to record start/end times, project assignments, and descriptions, then export to CSV for manual entry into corporate systems.

## Context
- **Framework**: SvelteKit (all-in-one: frontend + API + SSR)
- **Database**: SQLite via Prisma ORM
- **Deployment**: Single folder on local server/VPS
- **Constraints**: Single device, no authentication, no offline support, manual CSV export only

## Architecture

```
timetracker/
├── src/
│   ├── routes/
│   │   ├── +page.svelte           # Single page: timer + history + filters
│   │   ├── +layout.svelte         # Shell + tab lock logic
│   │   ├── +server.ts             # Prerender disabled
│   │   └── api/
│   │       ├── activities/
│   │       │   ├── +server.ts      # GET (list), POST (create)
│   │       │   └── [id]/+server.ts # PATCH (update), DELETE
│   │       └── projects/
│   │           ├── +server.ts      # GET (list), POST (create)
│   │           └── [id]/+server.ts  # PATCH (update), DELETE
│   └── lib/
│       ├── db.server.ts           # Prisma singleton
│       ├── stores.ts              # Reactive state (active activity, filters)
│       └── utils.ts               # Date formatting, CSV generation
├── prisma/
│   └── schema.prisma              # Project + Activity models
├── data/
│   └── timetracker.db             # SQLite file (gitignored)
└── package.json
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `db.server.ts` | Prisma client singleton, server-only |
| `stores.ts` | Svelte stores for active activity, filters, projects list |
| `utils.ts` | Date formatting (UTC→local), CSV generation with BOM |
| `api/activities/` | CRUD for activities (start, stop, edit, delete) |
| `api/projects/` | CRUD for projects (create, edit, delete) |
| `+page.svelte` | Timer controls, activity list, filters, modals |
| `+layout.svelte` | Tab lock via BroadcastChannel, global state |

## Implementation Steps

### Step 1
**Title**: Project scaffolding
**Description**: Initialize SvelteKit project, install dependencies (Prisma, better-sqlite3), configure TypeScript and SvelteKit defaults.
**Dependencies**: None

### Step 2
**Title**: Database schema definition
**Description**: Define Prisma schema with `Project` (id, name, color, createdAt) and `Activity` (id, projectId, description, startedAt, endedAt, createdAt). Add relations.
**Dependencies**: 1

### Step 3
**Title**: Database initialization
**Description**: Run `prisma db push` to create SQLite file and tables. Add `data/` to .gitignore. Create `db.server.ts` with Prisma client singleton pattern (avoid multiple instances).
**Dependencies**: 2

### Step 4
**Title**: Projects API endpoints
**Description**: Implement `GET /api/projects` (list all), `POST /api/projects` (create), `PATCH /api/projects/[id]` (update name/color), `DELETE /api/projects/[id]` (delete with cascade warning in response).
**Dependencies**: 3

### Step 5
**Title**: Activities API endpoints
**Description**: Implement `GET /api/activities` (list with optional filters: date, projectId), `POST /api/activities` (create with startedAt, validate endedAt not before startedAt), `PATCH /api/activities/[id]` (update fields, validate time logic), `DELETE /api/activities/[id]`.
**Dependencies**: 3

### Step 6
**Title**: Client-side stores
**Description**: Create Svelte stores: `activeActivity` (current activity with startedAt, or null), `activities` (filtered list), `projects` (all projects), `filters` (date, projectId). Include derived store `activeActivityDuration` that recalculates elapsed time.
**Dependencies**: 3

### Step 7
**Title**: Tab lock mechanism
**Description**: Implement BroadcastChannel in `+layout.svelte` onMount. Send HEARTBEAT every 2s. If another tab's HEARTBEAT detected, set `tabLocked = true` and show modal blocking UI. Store own `tabId` in module-level variable.
**Dependencies**: 6

### Step 8
**Title**: Core UI — Header with timer controls
**Description**: Build header section: project dropdown (fetched from store), description text input (max 500 chars), main action button (▶ green "Iniciar" or ⏹ red "Detener"), and secondary "✕ Fin manual" button visible only when activity active.
**Dependencies**: 6, 7

### Step 9
**Title**: Timer real-time update
**Description**: Implement `setInterval` (1s) in client that recalculates duration from `activeActivity.startedAt` to now. Display format: `HH:MM:SS`. Clear interval on destroy or when activity stopped.
**Dependencies**: 8

### Step 10
**Title**: Activity history list
**Description**: Render chronological list (newest first) below timer. Each row shows: started time, ended time (or "—" if active), duration (calculated), project color dot, project name, description (or "—" if empty). Click opens edit modal.
**Dependencies**: 6, 9

### Step 11
**Title**: Filter bar and date navigation
**Description**: Build filter section: left/right arrows for day navigation, centered date display (click opens date picker), project dropdown filter, "Limpiar" button. Sync filter state to URL params for shareability.
**Dependencies**: 6

### Step 12
**Title**: Activity edit/delete modal
**Description**: Modal on activity click: editable fields (project, description, startedAt datetime-local, endedAt datetime-local). Save triggers PATCH. Add trash icon on hover for delete with confirmation dialog.
**Dependencies**: 10, 11

### Step 13
**Title**: Project CRUD modal
**Description**: Modal accessible from header: create/edit/delete projects. Form fields: name (required), color picker (6 preset colors). Delete shows count of associated activities and requires explicit confirmation with red "Borrar todo" button.
**Dependencies**: 4

### Step 14
**Title**: CSV export functionality
**Description**: Implement export button that generates CSV with columns: `started_at,ended_at,duration_minutes,project,project_color,description`. Use UTF-8 BOM for Excel compatibility. Timestamps in ISO 8601 with offset. Download triggers automatically.
**Dependencies**: 11

### Step 15
**Title**: Input validation and edge cases
**Description**: Add client-side validation: warn if endedAt < startedAt (prevent save), truncate description > 500 chars with toast warning, warn if endedAt is in future, block startedAt > now. Show inline error messages.
**Dependencies**: 12

### Step 16
**Title**: Resume active activity on page load
**Description**: On app load, fetch activities and check for any with endedAt = null. If found, set as activeActivity. Recalculate elapsed time from stored startedAt. Show activity as "en curso" immediately.
**Dependencies**: 6, 9

### Step 17
**Title**: Deployment configuration
**Description**: Add `adapter-node` for server deployment. Create start script. Document SQLite file location (`./data/timetracker.db`). Add README with backup instructions (manual file copy or periodic CSV export).
**Dependencies**: 1-16

## Risks

| Risk | Mitigation |
|------|------------|
| SQLite file corruption or loss | Document manual backup process; CSV export is functional backup |
| Timer drift (client time manipulation) | Server only trusts startedAt from server on creation; display is cosmetic |
| Browser crash during activity | Activity persists in DB without endedAt; resumes on reload (Step 16) |
| Multiple developers misunderstanding "simple" | Enforce single-activity constraint; clear error if user tries to start while one active |
| Timezone confusion for remote users | Store UTC, display local, export with offset; document behavior clearly |

## Timeline

| Phase | Steps | Estimated Effort |
|-------|-------|------------------|
| Foundation | 1-3 | 1-2 hours |
| Backend APIs | 4-5 | 2-3 hours |
| State & Core UI | 6-9 | 3-4 hours |
| History & Filters | 10-12 | 2-3 hours |
| Projects & Export | 13-14 | 2 hours |
| Polish & Edge Cases | 15-16 | 2 hours |
| Deployment | 17 | 1 hour |
| **Total** | | **13-17 hours** |