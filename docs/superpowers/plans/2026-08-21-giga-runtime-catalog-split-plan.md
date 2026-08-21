# GIGA Runtime Catalog Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load a compact core catalog first and fetch official tag relationships only when tag-aware UI is used.

**Architecture:** Derive two deterministic runtime artifacts from the validated complete catalog. Hydrate the immutable frontend model only after a strictly validated lazy tag payload arrives.

**Tech Stack:** Python 3, browser ES modules, Node test runner, unittest, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-08-21-giga-runtime-catalog-split-design.md`

## Global Constraints

- Preserve `public/data/catalog.json` as the complete canonical artifact.
- Publish all derived files atomically and fail closed.
- Do not request the tag payload during ordinary first-view startup.
- Preserve existing public catalog, search, tag and responsive behavior.

---

### Task 1: Deterministic runtime artifacts

**Files:**
- Create: `src/giga_catalog/runtime_catalog.py`
- Modify: `scripts/sync_official_tags.py`
- Test: `tests/python/test_runtime_catalog.py`
- Test: `tests/python/test_sync_official_tags.py`

- [ ] Write failing tests for deterministic core/tag output, provenance stripping, complete assignments and a materially smaller core payload.
- [ ] Run the targeted tests and confirm the new module/output arguments are missing.
- [ ] Implement runtime payload builders and add both targets to the existing atomic transaction.
- [ ] Generate the two production artifacts and run targeted tests.

### Task 2: Strict frontend hydration

**Files:**
- Create: `public/js/runtime-tags.js`
- Create: `tests/js/runtime-tags.test.mjs`

- [ ] Write failing tests for valid hydration and rejection of mismatched generations, duplicates, unknown codes and unknown tags.
- [ ] Run the targeted test and confirm the module is missing.
- [ ] Implement the minimal pure hydration function.
- [ ] Run the targeted test and confirm it passes.

### Task 3: Lazy tag loading

**Files:**
- Modify: `public/js/app.js`
- Modify: `public/index.html`
- Modify: `tests/js/app.test.mjs`
- Modify: `tests/python/test_deployment_config.py`

- [ ] Write failing tests for one-shot tag fetch and core-only initial preload.
- [ ] Run targeted tests and confirm failures.
- [ ] Switch startup to the core payload and wire tag loading into tag/search/detail actions with retryable failure UI.
- [ ] Run targeted tests and browser smoke tests.

### Task 4: Release and production verification

**Files:**
- Modify: `README.md`
- Generate: `public/data/catalog-core.json`
- Generate: `public/data/catalog-tags.json`

- [ ] Run all Python tests, all JavaScript tests, syntax checks and `git diff --check`.
- [ ] Compare raw/gzip sizes and cold-browser timings against the recorded baseline.
- [ ] Commit, push to `main`, wait for GitHub Pages deployment and verify production at 390px and 1440px.
