# GIGA Runtime Catalog Split Design

## Goal

Reduce first-view download and parse cost without removing official tags or weakening the full public catalog/release gate.

## Architecture

Keep `public/data/catalog.json` as the complete canonical public artifact. Generate two additional deterministic runtime artifacts from it:

- `catalog-core.json`: catalog metadata, resources, totals, series and videos, with top-level tags and per-video tag/provenance fields removed.
- `catalog-tags.json`: schema version, matching `generatedAt`, normalized tag definitions, and compact `[code, tagIds]` assignments for tagged videos.

The frontend initially requests only `catalog-core.json`. It requests and validates `catalog-tags.json` when the user opens the tag index or begins a search that may match tags. Hydration rejects mismatched generations, unknown codes, duplicate assignments, unknown tag IDs and malformed shapes. A tag-load failure leaves ordinary catalog browsing operational and exposes a retryable tag error instead of replacing the whole page.

## Publication and compatibility

`scripts/sync_official_tags.py` builds and atomically publishes the full catalog, core artifact and tag artifact together. The scheduled workflow already runs this step after the normal refresh and stages all `public/data` files. The complete `catalog.json` remains available for compatibility, auditing and release verification.

## Performance contract

- `index.html` preloads `catalog-core.json`, not the full catalog.
- Initial application startup must not request `catalog.json` or `catalog-tags.json`.
- The tag payload is fetched at most once per application generation.
- Normal browsing works before and after tag hydration.
- Tag search, filters, detail tags and card tags retain existing behavior after hydration.

## Verification

- Python unit tests verify deterministic splitting, size reduction and atomic output inclusion.
- JavaScript unit tests verify strict hydration and one-shot lazy loading.
- Existing Python/JavaScript suites remain green.
- A real production browser verifies first-load resources, timings, tag search, mobile overflow, console errors and request failures.
