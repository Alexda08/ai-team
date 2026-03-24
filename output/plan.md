# Project Plan

## Objective

Build a 6-step high-fashion editorial workflow app that guides users through garment selection, AI-powered model/location analysis, camera specifications, and shot details before generating images via a configurable API adapter.

## Architecture

```
EditorialWorkflowApp
├── State Management (Zustand/React Context)
│   ├── EditorialState
│   ├── usePersistedState (localStorage)
│   └── useDraftRecovery
├── API Layer
│   ├── GenerationAdapter (interface)
│   └── NanoBananaAdapter (implementable)
├── Components
│   ├── StepNavigation
│   ├── Step1Garments
│   ├── Step2Model
│   ├── Step3Location
│   ├── Step4Camera
│   ├── Step5ShotDetails
│   ├── Step6Generate
│   ├── PromptPreviewModal
│   └── DraftRecoveryBanner
└── Hooks
    ├── useAIAnalysis
    ├── useImageManager
    ├── useGeneration
    └── useStepValidation
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `EditorialState` | Central state for garments, model, location, camera, shot details |
| `PersistedState` | localStorage save/restore with thumbnail-only storage |
| `GenerationAdapter` | Pluggable interface for API calls (sync/async) |
| `AIAnalysisService` | Claude Vision integration with retry/timeout |
| `ImageManager` | File handling, thumbnails, ObjectURL lifecycle |
| `StepValidator` | Dependency checking and requirement enforcement |

## Implementation Steps

### Step 1: Project Foundation & State Management

- Initialize Next.js/React project with TypeScript
- Define `EditorialState` interface with all step fields
- Set up Zustand store or React Context for state management
- Configure Tailwind CSS with fashion-forward design tokens

**Dependencies:** None

---

### Step 2: Step Navigation & Base Layout

- Build `StepNavigation` component (1-6 indicators)
- Create `StepContent` with conditional rendering
- Implement `useStepValidation` hook for dependency checking
- Add step requirement rules (`STEP_REQUIREMENTS` object)
- Style step indicators (active/completed/disabled states)

**Dependencies:** Step 1

---

### Step 3: Step 1 — Garments (Drag & Drop)

- Build `DropZone` component with drag-over visual feedback
- Implement `useImageManager` hook:
  - Multiple file upload validation (type, size limits)
  - Thumbnail generation (200px, JPEG 70%)
  - ObjectURL management with cleanup
  - MAX_IMAGES enforcement
- Create `GarmentGrid` with remove capability
- Add keyboard accessibility (Enter/Space to trigger file input)

**Dependencies:** Step 2

---

### Step 4: Step 2 & 3 — Model & Location with AI Analysis

- Build reusable `AIAnalysisPanel` component
- Implement `useAIAnalysis` hook:
  - API call to `/api/analyze` with image payload
  - 30-second timeout via AbortController
  - Retry logic (max 2 retries, exponential backoff)
  - Manual edit fallback capability
- Create editable description textarea
- Add error state UI with retry/manual fallback buttons
- Add confidence badge display

**Dependencies:** Step 2

---

### Step 5: Step 4 — Camera Specifications

- Define `CAMERA_ANGLES` array with visual indicators
- Build `AngleGrid` (3x3 grid) with selection state
- Create dropdowns for lens, film style, aperture, framing
- Set up default values in state initialization

**Dependencies:** Step 2

---

### Step 6: Step 5 — Shot Details Form

- Build options arrays for lighting, color grade, mood, pose
- Create select components or button groups for each
- Implement `KeywordInput` with tag suggestions
- Add `artDirectionNotes` textarea
- Wire up all fields to state updates

**Dependencies:** Step 2

---

### Step 7: Step 6 — Generate & Prompt Assembly

- Implement `assemblePrompt()` function
- Build `PromptPreviewModal` with formatted display
- Show image count summary (garments + references)
- Create generate button with loading state
- Implement `useGeneration` hook with async polling support

**Dependencies:** Steps 3, 4, 5, 6

---

### Step 8: API Adapter Layer

- Define `GenerationAdapter` interface
- Implement `NanoBananaAdapter` with FormData submission
- Add `generate()` method supporting sync/async responses
- Add optional `status()` method for polling
- Leave endpoint placeholders for user configuration

**Dependencies:** Step 7

---

### Step 9: State Persistence & Draft Recovery

- Implement `usePersistedState` hook:
  - Save thumbnails only to localStorage
  - 24-hour expiry with cleanup
  - Debounced auto-save
- Build `DraftRecoveryBanner` component
- Add recover/discard actions
- Clear full images on refresh (privacy by design)

**Dependencies:** Step 1

---

### Step 10: Integration & Error Handling

- Wire all steps into complete workflow
- Add toast notifications for errors/success
- Implement step warning banners
- Add overall loading states

**Dependencies:** Steps 1-9

---

### Step 11: Post-MVP Enhancements

#### 11a: Accessibility
- Add ARIA labels to all interactive elements
- Implement keyboard navigation for angle grid
- Add focus management between steps
- Screen reader announcements for state changes

#### 11b: Mobile Experience
- Replace drag-and-drop with native file picker fallback
- Transform angle grid to scrollable button list
- Responsive layout adjustments
- Touch-friendly tap targets (min 44px)

#### 11c: Rate Limiting
- Debounce generate button (500ms)
- Request deduplication
- Disable button during active generation
- Queue management for multiple requests

**Dependencies:** Steps 1-10 (all prior work)

---

## Risks

| Risk | Mitigation |
|------|------------|
| API endpoint unknown | Adapter pattern allows pluggable implementation |
| Claude Vision cost/availability | Mock mode for development, retry logic for failures |
| localStorage quota exceeded | Thumbnail-only storage, 24hr expiry, size limits per image |
| Large image uploads | 10MB limit, compression, File objects for API (not base64) |
| Async generation timeout | 3-minute polling with progress feedback |

---

## Timeline

| Phase | Steps | Estimated Effort |
|-------|-------|------------------|
| Foundation | 1-2 | 1-2 days |
| Core Steps | 3-6 | 3-4 days |
| Generation & API | 7-8 | 2 days |
| Persistence & Polish | 9-10 | 1-2 days |
| **MVP Total** | 1-10 | **7-10 days** |
| Post-MVP | 11a-c | 2-3 days |

**Note:** Excludes design system polish, testing, and deployment setup.