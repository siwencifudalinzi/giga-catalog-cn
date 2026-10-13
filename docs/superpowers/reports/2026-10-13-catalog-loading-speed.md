# Catalog loading speed — first round

The first round keeps the static ES-module application and V3 data contract. It caches immutable catalog views and normalized search rows, gives the first and next previews precedence over later images and optional link data, prepares the viewer on detail intent, and reuses valid local or already loaded covers.

## Measurements

Baseline: `3f58620`. Candidate: the loading-speed branch. Each version was served by the same local gzip server, in a fresh 390×844 mobile browser context with 3 Mbps throughput, 120 ms latency and 4× CPU slowdown. External pictures still came from the original GIGA host. These are controlled samples, not a promise for every network.

| Operation | Baseline | Candidate |
| --- | ---: | ---: |
| First preview after dialog, latest paired sample | 4,703 ms | 2,191 ms |
| Open first preview in viewer | 396 ms | 48 ms |
| Immediately advance to the second picture | 37 ms | 1,282 ms |
| Non-recent record lookup, median of 5 | 42 ms | Below browser clock resolution |
| 24 favorite lookups, median of 3 | 990.7 ms | 0.4 ms |
| Search SPSF, median of 5 | 169.8 ms | 2.6 ms |
| Tag filtering, median of 5 | 69.1 ms | 36.1 ms |

The earlier paired sample measured the first preview at 4,348/2,150 ms and viewer opening at 411/66 ms. The final scheduling gives the second preview its own head start before requesting later thumbnails and the link manifest. This reduced the candidate's immediate second-picture wait from 2,331 to 1,282 ms. The old version downloaded multiple pictures during its longer initial wait, so its second picture could already be cached. Earlier first display and instant uncached forward navigation remain a tradeoff until smaller preview derivatives are available.

Before the candidate's first preview completed, it did not request the global search, tag or link manifests, the full recent-series shard, or the original poster. The current local compressed cover was reused. Search and tags load when the user requests them; original watching links remain usable and upgrades run after the next preview settles, after a bounded image timeout/error, or on explicit link intent.

Building the reusable tag/search rows increased the one-time tag installation from 77.6 to 216.8 ms in this sample. Representative code, actor, Chinese and Japanese tag queries returned identical record sequences. Frequent queries no longer repeat that indexing work.

## Verification

- JavaScript: 188 passing tests, including immutable identity, tag replacement, full-series precedence, removed-series records, first-image load/error/timeout/abort, sequential follow-up scheduling and failed module import retry.
- Browser flow: mobile touch swipe, double tap, pinch, desktop double-click, nested Back/Forward, focus and scroll restoration, 320/390/844/1440 layouts and reduced motion.
- Browser faults: preview retry, late tags while the gallery stays open, favorite/card preservation and search pagination.
- Browser scheduling: slow/save-data budgets, deferred data, loaded-cover reuse, close/reopen cancellation, cached first images, metadata failure/retry, and separate viewer/state import failures.
- Python and deployment checks are recorded in the accompanying plan execution record.

The images are still full-size upstream JPEGs. Generating responsive preview derivatives, reducing drawer thumbnail sizes, splitting resolved link data and decoupling refresh timestamps from cache identities remain subsequent rounds.
