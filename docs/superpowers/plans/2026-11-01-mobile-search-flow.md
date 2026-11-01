# Mobile search flow implementation plan

> Execute inline using superpowers:executing-plans. Obtain one fresh whole-branch review before publication.

Goal: Implement the four fixes accepted after the 2026-10-31 mobile UX review: honest search readiness, combined keywords, keyword/tag intersection, and scrolling new searches to their result heading.

Design: Keep the existing static ES modules, V3 artifacts, index cache and 24-card pagination. Normalize a complete product code before tokenizing other queries; all keyword terms must match the existing normalized record text. Runtime search accepts optional include/exclude/match tag constraints. Within the tag tab, global keyword input stays in the tag UI and filters its results; other tabs remain global search. Preserve tag match mode and sort. Show in-page loading or incomplete/error feedback without disabling the search field. Positive basic results can appear while tags load, but zero is final only after both indexes succeed; never display unfiltered results as tagged results. New search input/submit schedules one instant scroll to the active result heading, while later enhancement/pagination/detail return keeps the current scroll.

Authorization: The user approved the first four recommended fixes and existing publication authorization persists. Use the public repository explicitly; origin in the shared root points to a different private repository. Work in this isolated branch; leave the root's uncommitted work untouched.

Global constraints: No new frontend dependencies, no data-contract/storage changes, no changes to the other eight UX recommendations. Preserve compact/full-width/spaced product codes, query order, immutable results, include/all/any/exclude semantics, loading deduplication, resource generation guards, tag retries, cover laziness, favorites and nested detail/gallery history.

Review focus: rapid query changes or clearing/switching views while requests finish; tags unavailable while text results still work; failed/retried search; excluding all matches without an unfiltered flash; late tag/cover refresh and load-more must not reset reading position.

## Task 1: Query matching and feedback policy
- [x] RED tests for AND keywords, product-code normalization, tag intersection/exclusion/any, and pending/incomplete zero-result policy.
- [x] Add normalized query terms in catalog.js; apply them and optional tag filters in runtime-catalog.js.
- [x] Add testable search feedback policy in app.js; focused and full JavaScript suites GREEN.

## Task 2: Mobile UI integration
- [ ] Render visible loading/error/partial feedback and retry actions; leave input editable during resource loads.
- [ ] Route tag-tab queries through the existing tag controls and apply keyword/tag constraints, with visible keyword context and clear action.
- [ ] Scroll each newly committed search once to its results; do not scroll on load-more, enrichment or history restore.
- [ ] Browser checks cover delayed/failed/retried resources, rapid input/clear/tab changes, combined filters, and 320/390 px layouts.

## Task 3: Verification and publication
- [ ] Full Python and JavaScript suites; mobile detail/gallery/favorites smoke checks.
- [ ] Fresh read-only whole-branch review, fix important findings with regressions.
- [ ] Commit, synchronize public main, publish and verify live source identity and mobile behavior.

Execution record:
- Base e746918f; baseline JavaScript 191/191 passing. Context and accepted behavior come from output/playwright/2026-10-31-mobile-ux-review.md in the shared root and the approval in chat.
- Native PowerShell and this execution record replace Bash-only skill ledger scripts. Shared interfaces: Task 1 runtime search options and feedback policy are consumed by Task 2 UI; their names and semantics above agree.
- Task 1: four new tests observed RED for combined queries, tag options, readiness policy and failed readiness notification; GREEN full JavaScript 195/195.
