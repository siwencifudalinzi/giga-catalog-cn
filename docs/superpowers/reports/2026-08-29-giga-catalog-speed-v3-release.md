# GIGA Catalog Speed V3 release preparation evidence

Date: 2026-08-30 (Asia/Shanghai)
Branch: `codex/catalog-speed-v3`
Task 7 baseline: `532e953e8bf65aedf45173b37d1bd97d536e542d`
Prior preparation commit: `40504025ec27ada50e513e95afe12f8b8b0b9c74`
Fix/release commit: `df089f6c743a665e4b759210e3058fec9636b095`

## Fix round and generated artifact evidence

The first release attempt exposed two real-browser blockers: generated shard
payloads lacked `series.artifact`, and the loader fetched logical
`runtime/g/...` paths from the site root instead of the public `/data/` base.
The fix round added generation-token artifact fields to each shard, replaced
the token during binding, and validates every shard artifact against the
bootstrap summary. The loader now accepts only the exact generation-bound
logical path grammar, keeps that logical path as the cache key, and fetches it
under the fixed module data base (`/data/runtime/g/...`). A narrow mobile
breakpoint stacks/wraps the header, retains 44px controls, and removes the
20rem HTML minimum-width constraint for narrow viewports.

`py scripts/build_runtime_catalog.py` was run twice. Both runs produced
generation `ebdab7b6c52031aa730681eb8924edbf8ce8021cf5d37765528621b74779fde1`
with 139 runtime JSON files (search, tags, and 137 series shards). The second
run compared all 140 expected paths (bootstrap plus the 139 runtime files)
against the first run by relative path and exact file bytes: every path and
byte sequence matched. The aggregate current-generation/bootstrap hash was
also unchanged (`20f4413b2b315a6037d8519641f5e2a67c170f6830fd68ef9ca7671d11aa64bfb`);
that digest is supplemental, not the equality check. The builder only pruned
the superseded prior generation directory.

| Artifact gate | Measured result |
| --- | --- |
| Bootstrap | 40,601 bytes; schema 3; exactly 24 `recentVideos` |
| Series | 137 summaries and 137/137 shard `series.artifact` values; every value exactly matches its bootstrap summary |
| Canonical coverage | 3,793/3,793 videos; no missing, extra, or duplicate shard records |
| Search | 3,793/3,793 video records |
| Tags | 733 definitions and 3,721 assignments |
| Totals | 3,793 videos, 137 series, 2,955 linked videos |

The Python builder assertion and the JS integration test read the current
generated files rather than fixtures; `parseSeriesPayload` passed for all 137
shards. `py scripts/release.py prepare --source-commit
df089f6c743a665e4b759210e3058fec9636b095` passed and validated every local
public file. Its temporary `public/giga-release.json` was removed because it
is not a planned Task 7 commit file.

## Regression and refresh gates

- `py -m unittest discover -s tests/python -p "test_*.py" -v`: **276 passed**, 1 Windows symlink-permission skip.
- `node --test`: **110 passed**, 0 failed/skipped.
- `py scripts/refresh.py --mode incremental --dry-run`: **DRY RUN UNCHANGED**, 3,793 videos / 137 series / 2,955 linked, diagnostics 0.
- `py scripts/refresh.py --mode links-only --dry-run`: **DRY RUN UNCHANGED**, 3,793 videos / 137 series / 2,955 linked, diagnostics 0.
- `git diff --check`: passed.
- Static V3/JS secret scan: no token/key/authorization/bearer/private-url matches. The broad `jwt` term has two benign substring matches inside opaque legacy Streamtape IDs in the pre-existing `public/data/resolved-links.json`; no credential or token value was found.

## Local browser measurements

The local server was `py -m http.server 8000 --directory public`, tested with
the real Chromium Playwright CLI browser. Each initial navigation mounted 24
cards and requested only bootstrap plus the shell/module/CSS/featured-cover
assets; no core, search, tags, or series shard was requested.

Reproduction entry point: start that server, then run
`npx --yes --package @playwright/cli playwright-cli open http://127.0.0.1:8000/`
and use `resize`, `goto`, `fill`, `click`, `eval`, `requests`, and `console` for
the checks below. The measured values and request assertions are recorded in
this report; the ignored `.superpowers/sdd/2026-08-29-giga-catalog-speed-v3-plan/task-7-report.md`
contains the task evidence notes. No raw browser log artifact is claimed.

| Viewport | Overflow (`scrollWidth - clientWidth`) | Initial cards | Console errors / failed requests |
| ---: | ---: | ---: | --- |
| 320px | 0px | 24 | 0 / 0 |
| 390px | 0px | 24 | 0 / 0 |
| 768px | 0px | 24 | 0 / 0 |
| 1440px | 0px | 24 | 0 / 0 |

Five fresh local navigations measured DOMContentLoaded at **13.3, 15.0,
16.2, 14.8, 12.3ms** (median **14.8ms**). These local measurements are not
a production comparison; the design baseline remains the 2026-08-29 sampled
production result of approximately 3.8–3.9 seconds. After cache population,
the delayed-bootstrap (1,500ms) reload showed 24 usable cards in **60ms**,
within the 500ms requirement.

At 390px, typing `SPSF-61` produced exactly one result and requested search and
tags at `/data/runtime/g/.../search.json` and `/data/runtime/g/.../tags.json`.
Opening SPSF requested exactly one series shard,
`/data/runtime/g/.../series/spsf.json`; no other series shard was requested.
The series view grew from 24 to 48 cards after Load More and focus remained on
the load-more button. A deliberately schema-mismatched bootstrap response
left the valid cached generation rendered with 24 cards; the expected parser
diagnostic was recorded, with no cache replacement. Reduced-motion emulation
matched its media query. At 200% effective zoom on 390px, all measured header
controls remained at least 44 CSS px touch size and overflow was **0px**.

## Compatibility, security, rollback, and deployment boundary

The canonical public URL, complete `catalog.json`, 11:30 Asia/Shanghai refresh
schedule, link data, external-only playback classification, and private-data
boundary are unchanged. V3 artifacts contain only validated public catalog
records and generation metadata. No service worker, resolver, proxy, private
URL, credential, or deployment secret was added. Logical runtime paths are
not accepted as arbitrary absolute/external fetch targets; only the validated
generation-bound grammar resolves under the public data base.

Production has **not** been verified. Verification must wait for the approved
default-branch deployment and the production release verifier. Rollback is:

```powershell
git revert <merge-commit>
# run the normal release/deployment workflow and verify the previous manifest
```
