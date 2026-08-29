# GIGA Catalog Speed V3 Design

## Status

Proposed for user review. This document defines the approved first-phase speed work only; it does not authorize rollout before the implementation and release gates pass.

## Goal

Make the public catalog feel fast on desktop and 390 px mobile without changing its URL, weakening the daily refresh pipeline, losing any catalog or link data, or making a cached browser appear permanently stale.

The production baseline measured on 2026-08-29 was:

- 3,793 videos and 137 series;
- `catalog-core.json` is about 1.4 MB decoded and about 242 KB transferred in the sampled browser run;
- a cold production navigation reached `DOMContentLoaded` in about 3.8–3.9 seconds, with sampled document TTFB between 1.7 and 2.5 seconds;
- the initial recent view mounted 62 video cards and produced an approximately 11,162 px tall page at 390 px width;
- the loaded search path was responsive and neither the 390 px nor 1280 px sample overflowed horizontally.

The design therefore reduces initial data, initial DOM work, and repeat-visit wait time. Visual restyling, playback expansion, account features, and a hosting migration are deliberately outside this phase.

## Alternatives considered

### A. Reduce the card count only

Render 24 cards instead of the latest complete series and retain the monolithic runtime catalog. This is the smallest change, but every first visit would still download and parse every video. It improves scrolling more than startup and leaves growth unbounded.

### B. Sharded runtime plus safe browser cache — recommended

Publish a small bootstrap payload, one immutable payload per series, and lazy search/tag payloads. Render at most 24 cards at a time and use IndexedDB to show the last validated bootstrap immediately while checking the stable bootstrap URL in the background. This directly addresses all three measured costs while preserving the canonical catalog and static hosting model.

### C. Move the catalog into a hosted database/API

Server-side queries could minimize each response, but they would add credentials, availability, cost, and a new failure surface to a catalog that is currently deterministic static data. This is unnecessary for 3,793 records and is rejected for this phase.

## Public artifact architecture

`public/data/catalog.json` remains the complete canonical public catalog and the source of truth for validation, audits, compatibility, and release verification. Existing `catalog-core.json` and `catalog-tags.json` remain generated during the transition so external consumers and rollback do not break.

The runtime builder additionally publishes:

- `public/data/catalog-bootstrap.json`: a stable, small entry document containing schema version, generation ID, `generatedAt`, totals, refresh summary, public resources, series summaries, the newest 24 complete card records, and generation-scoped artifact paths;
- `public/data/runtime/g/<generation>/search.json`: a compact global search index loaded only after the user focuses or uses search;
- `public/data/runtime/g/<generation>/tags.json`: the existing normalized tag definitions and assignments in a generation-scoped form, loaded only for tag UI or tag-aware search;
- `public/data/runtime/g/<generation>/series/<code>.json`: one validated complete core payload for each series, with the series code represented by a canonical lowercase filename.

`<generation>` is a deterministic lowercase SHA-256 identity of the runtime content, not a timestamp. Generation-scoped files are immutable by identity. `catalog-bootstrap.json` is the only mutable pointer and every referenced payload must repeat the same schema version, generation ID, and `generatedAt` before the frontend accepts it.

Series filenames are derived only from already validated canonical series codes. Runtime generation rejects duplicate output paths, unsafe codes, unresolved video references, mismatched counts, or a shard set that cannot reconstruct the canonical core catalog.

The newest 24 records are chosen globally by official `releaseDate` descending, then canonical code as a deterministic tie-break. Future-dated official products remain valid but receive a visible “预告” state; the section is named “最新目录” rather than claiming that every item is already released.

## Publication and daily synchronization

The existing 11:30 Asia/Shanghai incremental refresh, link-only refresh, weekly audit, official Chinese tag sync, full public catalog, and release manifest remain in place.

Runtime V3 generation occurs only after the canonical candidate has passed existing schema and refresh-mode validation. Publication follows this order:

1. build every V3 artifact in a private staging directory;
2. validate the complete staged generation and reconstruct its expected canonical runtime view;
3. publish the immutable generation directory;
4. atomically replace `catalog-bootstrap.json` last;
5. include the bootstrap and every generation file in the existing release manifest and deployment verification;
6. retain the current and immediately previous generation, removing older generated directories only after the new bootstrap and release inputs are complete.

Any source, build, validation, staging, or replacement failure leaves the previous bootstrap and generation usable. A links-only update must still publish a new generation when public link content changes. Private raw/state files and any future private-library fields remain outside all public runtime artifacts.

The GitHub Actions staging allowlist must explicitly include the V3 bootstrap and runtime generation directory. Tests must prove that unrelated files cannot be committed by the refresh job.

## Frontend loading flow

Startup performs these operations in parallel:

1. load the small static application shell;
2. read the most recent validated bootstrap from IndexedDB;
3. request `catalog-bootstrap.json` with revalidation semantics.

If a cached bootstrap is valid, it is rendered immediately. A valid network bootstrap with the same generation changes only connection status. A newer valid generation replaces the in-memory model, preserves a still-valid view/series selection, updates IndexedDB, and refreshes the visible result without requiring a manual browser reload.

If the cache is absent, invalid, or unavailable, the network bootstrap is the normal source. If the network fails but a valid cache exists, the app remains usable and labels the data as offline/cached. If both fail, the existing retryable load error is shown. Invalid or mismatched network data never overwrites the last valid cache.

IndexedDB stores only public generated artifacts, keyed by generation and artifact path. It retains at most the current and previous valid generations. Favorites and UI preferences keep their existing storage and migration behavior; catalog cache cleanup must never delete them. Browsers without IndexedDB continue through the network-only path.

The phase does not add a service worker. This avoids stale interception of the mutable bootstrap and keeps update behavior observable. A PWA/service worker can be designed separately after V3 production measurements.

## Views and bounded rendering

The initial “最新目录” view mounts 24 real video cards. It does not mount an entire series or generate missing-number placeholders.

Series, search, tag, and favorites results use a 24-item window on desktop and mobile. “加载更多” appends the next window while preserving keyboard focus and announces the new visible count through a status region. Changing series, query, sort, or filter resets the window. Existing optional slot mode may retain placeholders, but only for the selected series and only within the active window.

Opening a series fetches exactly its generation-scoped shard. Reopening it reuses the in-memory/IndexedDB copy. Search loads `search.json` once for the current generation; tag-aware search additionally loads `tags.json`. Selecting a result may fetch its series shard before opening full details. Fetches are deduplicated, abortable when superseded, and fail without corrupting the active view.

Card images retain fixed dimensions and asynchronous decoding. At most two covers are eager on widths below 768 px and at most the first visible row is eager on larger widths. All remaining covers receive their URL only near the viewport through the existing intersection observer. External GIGA images, short links, and resolved destinations are never copied into IndexedDB outside the validated public artifact that already contains them.

## Search and interaction compatibility

Search continues to match canonical code, title, actor, and series without tags. Chinese and Japanese tag matching becomes available after the lazy tag payload validates. A loading indicator appears only when the lazy search/tag resource is genuinely outstanding; ordinary series browsing remains usable.

Existing tabs, favorites, theme, density, series selection, detail dialog, previews, link badges, subtitle actions, and safe external-link behavior remain compatible. This phase does not add the later sorting/filter toolbar, but its result-window interface must accept a future ordering/filter descriptor so the next phase does not require another data redesign.

Private URLs must not enter the bootstrap, shards, search payload, DOM `data-*`, URL parameters, logs, cache keys, or error strings. Playback classification is unchanged: short links and ordinary landing pages remain external-only, and no network resolver or protected-media bypass is introduced.

## Cache and deployment policy

On hosts where response headers are configurable:

- `catalog-bootstrap.json`: short revalidation (`max-age` no greater than 300 seconds plus `must-revalidate`);
- `data/runtime/g/<generation>/*`: one year plus `immutable`;
- HTML: revalidate;
- release manifest: `no-store`;
- existing security headers remain unchanged.

GitHub Pages may impose its own shorter cache policy. Correctness must rely on generation identity and bootstrap validation, not on a particular CDN header. The current GitHub Pages URL remains canonical; a second CDN or custom domain is not required for release.

## Error handling and rollback

- A bad bootstrap, search payload, tag payload, or series shard fails closed and identifies only the public artifact class, never a contained URL.
- A shard mismatch leaves the current view intact and offers retry.
- A failed background refresh keeps the cached generation visible and marks it as cached/offline.
- A newer bootstrap is not activated until its required initial records validate.
- Rollback is a normal Git revert/redeploy to the previous known-good commit. Because the old `catalog-core.json`, frontend path, and previous generated content remain during transition, an emergency frontend rollback does not require regenerating catalog data.
- V3 is enabled only after production release verification and browser checks pass. There is no partial rollout that points the old frontend at V3-only data.

## Verification and acceptance gates

### Deterministic data tests

- V3 generation is byte-stable across repeated runs and input order permutations.
- Shards cover every canonical video exactly once and reproduce canonical core values.
- Counts, generation IDs, paths, tags, links, and `generatedAt` agree across artifacts.
- Failure injection proves the old bootstrap/generation survives every pre-publication and replacement failure.
- Incremental, links-only, audit, and official-tag paths all update V3 artifacts in the same publication transaction.

### Frontend tests

- Startup does not request `catalog-core.json`, `catalog.json`, search, tags, or a series shard before required.
- Cached bootstrap renders before a delayed network response; a newer valid response updates automatically.
- Invalid cache/network generations fail closed and never poison IndexedDB.
- Search/tag/series requests are lazy, deduplicated, abortable, and generation-bound.
- Every result view mounts at most 24 new cards per window and preserves accessibility behavior.
- Favorites, dialogs, previews, external links, and legacy preferences remain compatible.

### Release and regression gates

- All existing Python and JavaScript suites pass.
- Refresh dry-runs for incremental and links-only modes succeed without writing.
- Release manifest validation covers every new public file; missing or altered shards fail verification.
- Static scans confirm no token, owner secret, private URL, or private state in any V3 artifact.
- Real browser checks at 320, 390, 768, and 1440 px, plus 200% zoom and reduced motion, show no horizontal overflow or inaccessible load-more state.
- Production console and failed-request checks are clean after deployment.

### Performance gates

Measured against the same production URL and browser procedure used for the baseline:

- initial decoded catalog bootstrap is at most 250 KB and the initial view mounts no more than 24 video cards;
- initial navigation does not download global search, tags, or non-visible series payloads;
- median cold `DOMContentLoaded` over five runs improves by at least 30% unless document TTFB alone consumes the target, in which case application-ready time after bootstrap response must improve by at least 50%;
- a warm visit with a valid IndexedDB bootstrap paints usable catalog content within 500 ms on the review machine while background revalidation continues;
- searching `SPSF-61`, opening a series, returning to the latest view, and loading the next 24 items all produce the expected data without page reload.

## Out of scope

- changing the public URL or requiring a paid host;
- adding a service worker/PWA installation;
- adding new sort/filter controls, personal notes, watched state, or private-library sync;
- resolving short links, proxying media bytes, bypassing human verification, or widening inline playback;
- changing the visual identity beyond the small loading, cached/offline, preview, and load-more states required by this design.
