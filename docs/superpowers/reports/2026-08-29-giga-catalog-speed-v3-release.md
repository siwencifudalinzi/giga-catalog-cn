# GIGA Catalog Speed V3 release preparation evidence

Date: 2026-08-29 (Asia/Shanghai)
Branch: `codex/catalog-speed-v3`
Baseline/source commit: `532e953e8bf65aedf45173b37d1bd97d536e542d`
Release commit: the commit containing this report (`chore: prepare catalog speed v3 release`)

## Scope and artifact evidence

`py scripts/build_runtime_catalog.py` completed twice from the checked-in
`public/data/catalog.json` without network mutation. Both runs produced
generation `153b8fd971118ffee6aebd78127aba323de00e7ce620b7213ae8bf0871402fcf`;
SHA-256 comparison of all 140 generated files was byte-identical and the
second run produced no additional Git diff.

| Artifact | Observed result |
| --- | --- |
| `catalog-bootstrap.json` | 40,601 bytes; schema 3; exactly 24 `recentVideos` |
| Runtime generation | 139 JSON files: search, tags, and 137 series shards |
| Canonical coverage | 3,793/3,793 video codes, no missing/extra/duplicate; shard records equal `catalog-core.json` records |
| Search coverage | 3,793/3,793 video records |
| Tags | 733 definitions and 3,721 assignments; generation metadata matches bootstrap |
| Bootstrap totals | 3,793 videos, 137 series, 2,955 linked videos |

The release verifier preparation command passed:

```text
py scripts/release.py prepare --source-commit 532e953e8bf65aedf45173b37d1bd97d536e542d
```

It produced a valid manifest covering every local public file. The temporary
`public/giga-release.json` was removed afterward because it is not a planned
Task 7 commit file. The static scan found no token/key/authorization/bearer or
private-url match. The two case-insensitive `Jwt` matches are legacy provider
enum values in `public/data/resolved-links.json`, not credentials or tokens.

## Regression and refresh gates

- `py -m unittest discover -s tests/python -p "test_*.py" -v`: **276 passed**, 1 Windows symlink-permission skip.
- `node --test`: **107 passed**, 0 failed/skipped.
- `py scripts/refresh.py --mode incremental --dry-run`: **DRY RUN UNCHANGED**, 3,793 videos / 137 series / 2,955 linked, diagnostics 0.
- `py scripts/refresh.py --mode links-only --dry-run`: **DRY RUN UNCHANGED**, 3,793 videos / 137 series / 2,955 linked, diagnostics 0.
- `git diff --check`: passed.

## Local browser measurements

The local static server was `py -m http.server 8000 --directory public`, and
the real Playwright CLI browser used Chromium. Initial navigation was checked
at each requested viewport:

| Viewport | `scrollWidth - clientWidth` | Initial video cards | Initial data requests | Console/errors |
| ---: | ---: | ---: | --- | --- |
| 320 | 0 px | 24 | bootstrap + shell/module/CSS/featured-cover assets; no core/search/tags/shard | 0 before interaction |
| 390 | 0 px | 24 | bootstrap + shell/module/CSS/featured-cover assets; no core/search/tags/shard | 0 before interaction |
| 768 | 0 px | 24 | bootstrap + shell/module/CSS/featured-cover assets; no core/search/tags/shard | 0 before interaction |
| 1440 | 0 px | 24 | bootstrap + shell/module/CSS/featured-cover assets; no core/search/tags/shard | 0 before interaction |

Measured bootstrap resource data at the initial desktop run was 40,901 bytes
transferred and 40,601 decoded bytes. The request list contained
`/data/catalog-bootstrap.json` and `/data/featured-covers.json`, but no
`catalog-core.json`, `catalog-tags.json`, `search.json`, or series shard.

Five fresh local navigations measured `DOMContentLoaded` at 39.0, 42.6, 40.0,
37.1, and 43.5 ms (median **40.0 ms**). These local timings are not a
production comparison; the design baseline remains the 2026-08-29 production
measurement of approximately 3.8–3.9 seconds and 1.7–2.5 seconds sampled
document TTFB. After populating IndexedDB, a reload with the bootstrap network
response delayed by 1,500 ms still showed 24 cards at 400 ms (`elapsedMs`: 425)
while background revalidation continued.

## Required 390px interaction checks and blockers

The initial 390px view rendered 24 cards and showed the expected cached-state
copy (`已显示缓存，正在检查更新`) after cache population. The following two
release blockers prevent claiming the remaining lazy-flow gates:

1. The generated bootstrap declares `runtime/g/<generation>/search.json` and
   `runtime/g/<generation>/series/spsf.json`, while the public files are served
   under `data/runtime/g/<generation>/...`. `public/js/runtime-loader.js` passes
   those paths directly to `fetch`, so real browser requests returned 404:
   `http://127.0.0.1:8000/runtime/g/.../search.json` and
   `.../series/spsf.json`. `SPSF-61` search and SPSF activation therefore did
   not complete; Playwright recorded 2 console errors. Unit fixtures currently
   use the root-relative `runtime/g/...` paths and do not catch this deployed
   directory mismatch.
2. With `prefers-reduced-motion: reduce` emulated, the page matched the media
   query and remained at 24 cards. At 200% effective zoom on 390px, however,
   measured `scrollWidth` was 435px versus a 390px client width (45px
   horizontal overflow), originating in the header search/tools row.

Because of these blockers, load-more-to-48 plus focus retention, successful
`SPSF-61` search, shard-only series loading, and mismatched-bootstrap cache
retention could not be honestly recorded as passing in the real browser. They
remain required rechecks after the path and responsive-layout fixes.

## Compatibility, security, rollback, and deployment boundary

The canonical public URL, complete `catalog.json`, 11:30 Asia/Shanghai refresh
schedule, link data, external-only playback classification, and private-data
boundary are unchanged. V3 artifacts contain only validated public catalog
records and generation metadata. No service worker, resolver, proxy, private
URL, credential, or deployment secret was added.

Production has **not** been verified. Verification must wait for the default
branch deployment and the two blockers above to be fixed and rechecked. The
rollback is the normal deployment workflow after reverting the integration
commit:

```powershell
git revert <merge-commit>
# run the normal release/deployment workflow and verify the previous manifest
```
