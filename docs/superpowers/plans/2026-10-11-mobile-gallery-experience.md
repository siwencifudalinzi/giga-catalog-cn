# Mobile gallery experience implementation plan

> Use superpowers:executing-plans to implement the approved chat design inline; obtain one independent review before publishing.

**Goal:** Make the existing GitHub Pages catalog faster and more comfortable on phones, including a swipeable, zoomable preview viewer.

**Spec:** The mobile-first design approved in this chat on 2026-10-11. Existing catalog schemas, favorites and detail history must remain compatible.

**Architecture:** Extend the existing static ES modules and native dialogs. Load the image viewer only on demand. Keep gesture calculations and history ownership separately testable; reuse the current detail navigation and catalog caches.

**Tech stack:** Vanilla JavaScript, CSS, Python cover generation, Node test runner, Playwright CLI.

## Constraints and review focus

- No added frontend dependencies. Preserve desktop and light-theme usability.
- Back closes the image viewer before the underlying detail; forward/reopening must not queue duplicate history traversal or lose parent history state.
- At 1x, horizontal gestures switch images; when zoomed, gestures pan. Pinch is anchored to the touch midpoint and image bounds are clamped.
- Single image errors permit retry and navigation. The first broken thumbnail must not hide later previews.
- Honor reduced motion, safe areas and keyboard/focus navigation. Closing overlays preserves page and detail scroll.
- Preload only adjacent gallery images; constrain speculative catalog loads on slow/save-data connections.

## Task 1: Image viewer

Files: `public/js/image-viewer-state.js`, `public/js/image-viewer.js`, `tests/js/image-viewer.test.mjs`.

- [x] Write failing gesture, image-boundary and nested-history regression tests; run RED.
- [x] Implement zoom/pan/gesture helpers and a bounded lazy viewer with retry, keyboard and owned Back/Forward handling.
- [x] Run focused tests GREEN and the JavaScript suite; record verification.

## Task 2: Mobile interaction and presentation

Files: `public/js/app.js`, `public/js/render.js`, `public/index.html`, `public/css/style.css`.

- [x] Make preview thumbnails accessible buttons and integrate lazy viewer loading. Preserve thumbnail/detail focus and scroll; isolate failed images.
- [x] Move mobile navigation to the bottom; simplify the header; add compact hero/detail sections and progressive disclosure.
- [x] Add stable loading placeholders, restrained card/navigation motion, and polished dark/light colors.
- [x] Check mobile and desktop layouts and gallery gestures in a real browser.

## Task 3: Loading speed

Files: runtime loader and tests, featured cover cache and tests, app loading hooks.

- [x] Write regression tests for same-generation memory reuse and an expanded recent-cover cache.
- [x] Reuse validated runtime payloads in memory, respect generation changes, and gently prefetch search/series on intent.
- [x] Expand compressed cover coverage to the recent visible window while preserving atomic publication and fallbacks.
- [x] Run tests and compare local asset/request behavior; avoid speed claims without measurements.

## Task 4: Verification and release

- [ ] Run full Python and JavaScript suites; verify 390px, 320px, landscape and desktop, image retry, rapid close/reopen, Back/Forward, focus and reduced motion.
- [ ] Obtain an independent whole-change review; fix important findings and verify regressions.
- [ ] Commit only this work, fast-forward against current main, publish, confirm Actions success and deployed assets.

## Execution record

- Baseline: latest main at `36393c3`, clean isolated checkout; 159 JavaScript tests passing.
- Authorization: user approved the full design and execution; publishing to the existing GitHub website is authorized by this chat.
- Task 1: gesture/history tests RED (missing module) then GREEN; browser double-tap reproduction identified touch-generated dblclick undoing zoom. Regression RED then GREEN, real CDP touch checks pass.
- Task 2: native-dialog integration, mobile bottom nav, compact detail, per-image failures, keyed favorite-card reuse, append-only search pagination. Late tag hydration updates metadata only, preserving gallery and detail scroll.
- Task 3: memory reuse/generation, 24-cover selection/renderer, intent budget tests RED then GREEN. Generated all 24 covers with zero source failures; total 985,490 bytes. Viewer module absent from initial resource requests. Warm-browser timings are not valid cold-load speed comparisons, so no percentage/time gain claimed.
- Validation: Python 382 tests passed (one existing skip); JavaScript 172 tests, latest focused viewer suite 9/9. Browser smoke: 23 checks passed, including real swipe, double tap, pinch, zoom-pan isolation, rapid close, Back/Forward, focus/scroll, 320/390/844/1440px and reduced motion; zero script exceptions.
- Execution uses native PowerShell and this plan's execution record in place of the skill's Bash-only ledger helpers.
