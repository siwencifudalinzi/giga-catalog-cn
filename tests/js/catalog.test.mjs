import assert from "node:assert/strict";
import test from "node:test";

import {
  createCatalogModel,
  normalizeText,
  normalizeVideoCode,
} from "../../public/js/catalog.js";

test("catalog normalization helpers remain public for the V3 runtime layer", () => {
  assert.equal(normalizeText(" ＳＰＳＦ  1 "), "spsf 1");
  assert.equal(normalizeVideoCode(" spsf_001 "), "SPSF-1");
});

test("catalog search and filters resolve normalized official tags", () => {
  const model = createCatalogModel({
    tags: [
      { id: 6, group: "genre", nameJa: "陰落", nameZh: "沦陷", count: 1 },
      { id: 25, group: "genre", nameJa: "黒髪", nameZh: "黑发", count: 1 },
    ],
    series: [
      {
        code: "SPSF",
        latestReleaseDate: "2026-01-01",
        videos: [
          { code: "SPSF-1", number: 1, title: "Title", actors: [], tagIds: [6, 25] },
          { code: "SPSF-2", number: 2, title: "Other", actors: [], tagIds: [] },
        ],
      },
    ],
  });

  assert.deepEqual(model.search("沦陷").map((video) => video.code), ["SPSF-1"]);
  assert.deepEqual(model.search("黒髪").map((video) => video.code), ["SPSF-1"]);
  assert.deepEqual(
    model.filterByTags({ include: [6, 25], match: "all" }).map((video) => video.code),
    ["SPSF-1"],
  );
  assert.equal(model.getTag(6).nameZh, "沦陷");
  assert.equal(model.getTags("genre").length, 2);
});
import {
  derivePreviewUrls,
  mountSeries,
  observeDeferredCovers,
  optimizedCoverUrl,
  siteAssetUrl,
  renderSearchResults,
  renderSeriesShell,
  normalizeFeaturedCovers,
  unmountSeries,
} from "../../public/js/render.js";

function catalogFixture() {
  return {
    schemaVersion: 1,
    generatedAt: "2026-07-29T00:00:00Z",
    totals: { series: 4, videos: 5, linkedVideos: 1 },
    refresh: {
      mode: "incremental",
      sourceComplete: false,
      counts: { added: 1 },
    },
    series: [
      {
        code: "OLD",
        count: 1,
        firstReleaseDate: "2024-01-01",
        latestReleaseDate: "2024-01-01",
        videos: [
          {
            code: "OLD-1",
            number: 1,
            title: "Archive",
            actors: ["Archive Actor"],
            releaseDate: "2024-01-01",
            cover: "https://images.example/old.jpg",
          },
        ],
      },
      {
        code: "TIEB",
        count: 1,
        firstReleaseDate: "2026-06-01",
        latestReleaseDate: "2026-06-01",
        videos: [
          {
            code: "TIEB-1",
            number: 1,
            title: "Beta",
            actors: [],
            releaseDate: "2026-06-01",
            cover: "https://images.example/tie-b.jpg",
          },
        ],
      },
      {
        code: "SPSF",
        count: 2,
        firstReleaseDate: "2026-07-01",
        latestReleaseDate: "2026-07-02",
        videos: [
          {
            code: "SPSF-44",
            number: 44,
            title: "機械戦士の帰還",
            actors: ["山田 花子"],
            releaseDate: "2026-07-01",
            cover: "https://images.example/spsf-44.jpg",
            previewBase: "https://images.example/spsf-44/",
            previewCount: 18,
            links: {
              streamtape: "https://video.example/watch",
              uncensored: { gofile: "https://files.example/download" },
            },
          },
          {
            code: "SPSF-45",
            number: 45,
            title: "Second title",
            actors: ["Jane Doe"],
            releaseDate: "2026-07-02",
            cover: "https://images.example/spsf-45.jpg",
          },
        ],
      },
      {
        code: "TIEA",
        count: 1,
        firstReleaseDate: "2026-06-01",
        latestReleaseDate: "2026-06-01",
        videos: [
          {
            code: "TIEA-1",
            number: 1,
            title: "Alpha",
            actors: [],
            releaseDate: "2026-06-01",
            cover: "https://images.example/tie-a.jpg",
          },
        ],
      },
    ],
  };
}

function containerFixture() {
  return { innerHTML: "" };
}

function seriesFixture(videos) {
  return {
    code: "SPSF",
    count: videos.length,
    firstReleaseDate: "2026-07-01",
    latestReleaseDate: "2026-07-31",
    videos,
  };
}

function videoFixture(number, overrides = {}) {
  return {
    code: `SPSF-${number}`,
    series: "SPSF",
    number,
    title: `Title ${number}`,
    actors: ["Actor"],
    releaseDate: "2026-07-01",
    cover: `https://images.example/spsf-${number}.jpg`,
    ...overrides,
  };
}

function classCount(html, className) {
  return (html.match(new RegExp(`class="[^"]*\\b${className}\\b`, "gu")) ?? [])
    .length;
}

test("featured covers use only the canonical local path declared for each code", () => {
  const generation = "a".repeat(64);
  const covers = normalizeFeaturedCovers({
    generation,
    covers: [
      { code: "SPSF-44", path: `/media/featured-covers/g/${generation}/spsf-44.webp` },
      { code: "THZA-00", path: `/media/featured-covers/g/${generation}/thza-0.webp` },
      { code: "SPSF-45", path: "/media/featured-covers/../../escape.webp" },
      { code: "BAD CODE", path: "/media/featured-covers/bad-code.webp" },
    ],
  });

  assert.equal(covers.get("SPSF-44"), `/media/featured-covers/g/${generation}/spsf-44.webp`);
  assert.equal(covers.get("THZA-0"), `/media/featured-covers/g/${generation}/thza-0.webp`);
  assert.equal(covers.has("SPSF-45"), false);
  assert.equal(covers.has("BAD CODE"), false);
});

test("cover loading priority keeps two images on mobile and covers the full desktop row", () => {
  const series = seriesFixture(Array.from({ length: 6 }, (_, index) => videoFixture(index + 1)));
  const mobile = containerFixture();
  const desktop = containerFixture();

  mountSeries(mobile, series, { viewportWidth: 390 });
  const mobileHtml = mobile.innerHTML;
  mountSeries(desktop, series, { viewportWidth: 1440 });

  assert.equal((mobileHtml.match(/fetchpriority="high"/gu) ?? []).length, 2);
  assert.equal((desktop.innerHTML.match(/fetchpriority="high"/gu) ?? []).length, 6);
});

test("featured-cover manifest safely falls back to the original cover when unavailable", () => {
  const series = seriesFixture([videoFixture(1)]);
  const fallback = containerFixture();
  const featured = containerFixture();

  mountSeries(fallback, series, { viewportWidth: 1440, featuredCovers: new Map() });
  const fallbackHtml = fallback.innerHTML;
  mountSeries(featured, series, {
    viewportWidth: 1440,
    featuredCovers: new Map([["SPSF-1", `/media/featured-covers/g/${generation}/spsf-1.webp`]]),
  });

  assert.match(fallbackHtml, /https:\/\/images\.example\/spsf-1\.jpg/);
  assert.match(featured.innerHTML, new RegExp(`src="/media/featured-covers/g/${generation}/spsf-1\\.webp"`, "u"));
});

test("rendering rejects a forged featured-cover map path for the video code", () => {
  const container = containerFixture();
  mountSeries(container, seriesFixture([videoFixture(1)]), {
    viewportWidth: 1440,
    featuredCovers: new Map([["SPSF-1", "/not-the-fixed-featured-path.webp"]]),
  });

  assert.doesNotMatch(container.innerHTML, /not-the-fixed-featured-path/);
  assert.match(container.innerHTML, /https:\/\/images\.example\/spsf-1\.jpg/);
});

test("search and favorites stay at two priority covers and never use featured paths", () => {
  const videos = Array.from({ length: 6 }, (_, index) => videoFixture(index + 1));
  const featuredCovers = new Map([["SPSF-1", "/media/featured-covers/spsf-1.webp"]]);
  const search = containerFixture();
  const favorites = containerFixture();

  renderSearchResults(search, videos, { viewportWidth: 1440, featuredCovers });
  const searchHtml = search.innerHTML;
  renderSearchResults(favorites, videos, { viewportWidth: 1440, context: "favorites", featuredCovers });

  for (const html of [searchHtml, favorites.innerHTML]) {
    assert.equal((html.match(/fetchpriority="high"/gu) ?? []).length, 2);
    assert.doesNotMatch(html, /\/media\/featured-covers\//);
  }
});

test("search matches normalized code, title, actor, and containing series", () => {
  const model = createCatalogModel(catalogFixture());

  for (const [query, expected] of [
    ["spsf-44", ["SPSF-44"]],
    ["spsf 044", ["SPSF-44"]],
    ["spsf_0044", ["SPSF-44"]],
    ["ＳＰＳＦ＿００４４", ["SPSF-44"]],
    ["機械戦士", ["SPSF-44"]],
    ["山田 花子", ["SPSF-44"]],
    ["SPSF", ["SPSF-44", "SPSF-45"]],
  ]) {
    assert.deepEqual(
      model.search(query).map((video) => video.code),
      expected,
      `query ${query}`,
    );
  }
});

test("empty and whitespace-only searches return no explicit result set", () => {
  const model = createCatalogModel(catalogFixture());

  assert.deepEqual(model.search(""), []);
  assert.deepEqual(model.search(" \t\n "), []);
});

test("recent series order is deterministic with a code tie-break", () => {
  const model = createCatalogModel(catalogFixture());

  assert.deepEqual(
    model.getRecentSeries(3).map((series) => series.code),
    ["SPSF", "TIEA", "TIEB"],
  );
  assert.deepEqual(
    model.getRecentSeries().map((series) => series.code),
    ["SPSF", "TIEA", "TIEB", "OLD"],
  );
  assert.deepEqual(model.getRecentSeries(0), []);
});

test("lookups are normalized and operate without a DOM", () => {
  const model = createCatalogModel(catalogFixture());

  assert.equal(model.getSeries(" spsf ").code, "SPSF");
  assert.equal(model.getVideo("spsf_0044").title, "機械戦士の帰還");
  assert.equal(model.getSeries("missing"), null);
  assert.equal(model.getVideo("not a code"), null);
});

test("zero suffix videos remain searchable and addressable through canonical lookups", () => {
  const payload = catalogFixture();
  payload.series.push({
    code: "THZA",
    count: 1,
    firstReleaseDate: "2025-01-24",
    latestReleaseDate: "2025-01-24",
    videos: [
      videoFixture(0, {
        code: "THZA-0",
        series: "THZA",
        title: "Zero suffix product",
        releaseDate: "2025-01-24",
      }),
    ],
  });
  const model = createCatalogModel(payload);

  assert.equal(model.getVideo(" thza_000 ")?.code, "THZA-0");
  assert.deepEqual(
    model.search("thza-00").map((video) => video.code),
    ["THZA-0"],
  );
});

test("model clones and deeply freezes every exposed source-backed value", () => {
  const payload = catalogFixture();
  const model = createCatalogModel(payload);
  const series = model.getSeries("SPSF");
  const video = model.getVideo("SPSF-44");
  const results = model.search("SPSF");

  payload.series[2].videos[0].title = "mutated source";
  payload.refresh.counts.added = 999;

  assert.equal(video.title, "機械戦士の帰還");
  assert.equal(model.metadata.refresh.counts.added, 1);
  assert.equal(Object.isFrozen(series), true);
  assert.equal(Object.isFrozen(series.videos), true);
  assert.equal(Object.isFrozen(video.actors), true);
  assert.equal(Object.isFrozen(video.links.uncensored), true);
  assert.equal(Object.isFrozen(results), true);
  assert.throws(() => {
    video.links.uncensored.gofile = "https://attacker.invalid/";
  }, TypeError);
  assert.throws(() => {
    results.push(video);
  }, TypeError);
});

test("model exposes a cloned immutable top-level subtitle resource", () => {
  const payload = catalogFixture();
  payload.resources = {
    subtitleDirectory: {
      label: "SRT ENGSUB DOWNLOAD",
      url: "https://ouo.io/BAbfv4",
    },
  };

  const model = createCatalogModel(payload);
  payload.resources.subtitleDirectory.url = "https://attacker.invalid/";

  assert.deepEqual(model.metadata.resources, {
    subtitleDirectory: {
      label: "SRT ENGSUB DOWNLOAD",
      url: "https://ouo.io/BAbfv4",
    },
  });
  assert.equal(Object.isFrozen(model.metadata.resources), true);
  assert.equal(Object.isFrozen(model.metadata.resources.subtitleDirectory), true);
});

test("series shell escapes source text and contains no eagerly rendered cards", () => {
  const html = renderSeriesShell({
    code: 'SPSF"><img src=x onerror=alert(1)>',
    count: 2,
    firstReleaseDate: "2026-07-01",
    latestReleaseDate: "2026-07-02",
    videos: [videoFixture(1), videoFixture(2)],
  });

  assert.equal(html.includes("<img src=x"), false);
  assert.match(html, /SPSF&quot;&gt;&lt;img/);
  assert.match(html, /class="series-shell"/);
  assert.match(html, /class="series-mount"/);
  assert.equal(classCount(html, "video-card"), 0);
});

test("series shell renders one safe external subtitle action without eager requests", () => {
  const html = renderSeriesShell({
    code: "SPSF",
    count: 1,
    firstReleaseDate: "2026-07-01",
    latestReleaseDate: "2026-07-02",
    links: {
      subtitle: "https://drive.google.com/open?id=series&usp=sharing",
    },
    videos: [videoFixture(1)],
  });

  assert.match(
    html,
    /class="series-subtitle-link"[^>]*href="https:\/\/drive\.google\.com\/open\?id=series&amp;usp=sharing"[^>]*target="_blank"[^>]*rel="noopener noreferrer"/u,
  );
  assert.equal(classCount(html, "series-subtitle-link"), 1);
  assert.equal(classCount(html, "video-card"), 0);
  assert.doesNotMatch(html, /<(?:img|iframe|video)\b/iu);

  const unsafe = renderSeriesShell({
    code: "SPSF",
    links: { subtitle: "javascript:alert(1)" },
  });
  assert.equal(classCount(unsafe, "series-subtitle-link"), 0);
});

test("series mounting defaults to real products and creates slots only when explicit", () => {
  const series = seriesFixture([videoFixture(1), videoFixture(3)]);
  const realContainer = containerFixture();
  const realWindow = mountSeries(realContainer, series);

  assert.equal(classCount(realContainer.innerHTML, "video-card"), 2);
  assert.equal(classCount(realContainer.innerHTML, "empty-slot"), 0);
  assert.deepEqual(
    {
      mode: realWindow.mode,
      total: realWindow.total,
      rendered: realWindow.rendered,
      hasMore: realWindow.hasMore,
    },
    { mode: "real-only", total: 2, rendered: 2, hasMore: false },
  );

  const slotContainer = containerFixture();
  const slotWindow = mountSeries(slotContainer, series, { mode: "slots" });

  assert.equal(classCount(slotContainer.innerHTML, "video-card"), 2);
  assert.equal(classCount(slotContainer.innerHTML, "empty-slot"), 97);
  assert.match(slotContainer.innerHTML, /data-number="2"/);
  assert.equal(slotWindow.total, 99);
  assert.equal(slotWindow.rendered, 99);
});

test("zero suffix videos sort first and survive explicit slot rendering", () => {
  const series = seriesFixture([videoFixture(1), videoFixture(0)]);
  const realContainer = containerFixture();
  mountSeries(realContainer, series);

  assert.ok(
    realContainer.innerHTML.indexOf('data-code="SPSF-0"') <
      realContainer.innerHTML.indexOf('data-code="SPSF-1"'),
  );

  const slotContainer = containerFixture();
  const slotWindow = mountSeries(slotContainer, series, { mode: "slots" });
  assert.equal(classCount(slotContainer.innerHTML, "video-card"), 2);
  assert.equal(classCount(slotContainer.innerHTML, "empty-slot"), 98);
  assert.equal(slotWindow.total, 100);
  assert.equal(
    (slotContainer.innerHTML.match(/data-code="SPSF-0"/gu) ?? []).length,
    2,
  );
});

test("missing declared numbers derive from code instead of masquerading as zero", () => {
  const missingNumber = videoFixture(2);
  delete missingNumber.number;
  const series = seriesFixture([missingNumber, videoFixture(1)]);
  const container = containerFixture();

  mountSeries(container, series);

  assert.ok(
    container.innerHTML.indexOf('data-code="SPSF-1"') <
      container.innerHTML.indexOf('data-code="SPSF-2"'),
  );
});

test("a missing zero number derives from its canonical code in slot mode", () => {
  const missingNumber = videoFixture(0);
  delete missingNumber.number;
  const container = containerFixture();

  const window = mountSeries(container, seriesFixture([missingNumber]), {
    mode: "slots",
  });

  assert.equal(classCount(container.innerHTML, "video-card"), 1);
  assert.equal(classCount(container.innerHTML, "empty-slot"), 99);
  assert.equal(window.total, 100);
});

test("null boolean and malformed declared numbers never masquerade as zero", () => {
  for (const declared of [null, false, true, "", "invalid"]) {
    const container = containerFixture();
    const window = mountSeries(
      container,
      seriesFixture([videoFixture(0, { number: declared })]),
      { mode: "slots" },
    );

    assert.equal(classCount(container.innerHTML, "video-card"), 0, String(declared));
    assert.equal(classCount(container.innerHTML, "empty-slot"), 99, String(declared));
    assert.equal(window.total, 99, String(declared));
  }
});

test("search and favorite result grids never honor slot-mode requests", () => {
  for (const context of ["search", "favorites"]) {
    const container = containerFixture();
    const window = renderSearchResults(
      container,
      [videoFixture(1), videoFixture(3)],
      { context, mode: "slots" },
    );

    assert.equal(classCount(container.innerHTML, "video-card"), 2);
    assert.equal(classCount(container.innerHTML, "empty-slot"), 0);
    assert.equal(window.mode, "real-only");
  }
});

test("sets above 250 items mount a 100-item page window", () => {
  const videos = Array.from({ length: 251 }, (_, index) =>
    videoFixture(index + 1),
  );
  const container = containerFixture();
  const firstWindow = mountSeries(container, seriesFixture(videos), {
    limit: 500,
  });

  assert.equal(classCount(container.innerHTML, "video-card"), 100);
  assert.deepEqual(
    {
      start: firstWindow.start,
      end: firstWindow.end,
      rendered: firstWindow.rendered,
      total: firstWindow.total,
      hasMore: firstWindow.hasMore,
      nextStart: firstWindow.nextStart,
    },
    {
      start: 0,
      end: 100,
      rendered: 100,
      total: 251,
      hasMore: true,
      nextStart: 100,
    },
  );

  const secondWindow = mountSeries(container, seriesFixture(videos), {
    start: firstWindow.nextStart,
  });
  assert.equal(classCount(container.innerHTML, "video-card"), 100);
  assert.equal(secondWindow.start, 100);
  assert.equal(secondWindow.end, 200);
});

test("video cards escape content, prioritize the first row, and show link badges", () => {
  const container = containerFixture();
  renderSearchResults(container, [
    videoFixture(44, {
      title: '<script>alert("x")</script>',
      actors: ['Actor <img src=x onerror="alert(1)">'],
      links: {
        streamtape: "https://video.example/watch",
        gofile: "https://files.example/download",
        uncensored: {
          player4me: "https://player.example/watch",
        },
      },
    }),
  ]);

  assert.equal(container.innerHTML.includes("<script>"), false);
  assert.equal(container.innerHTML.includes("<img src=x"), false);
  assert.match(container.innerHTML, /&lt;script&gt;alert\(&quot;x&quot;\)/);
  assert.match(container.innerHTML, /width="320"/);
  assert.match(container.innerHTML, /height="480"/);
  assert.match(container.innerHTML, /loading="eager"/);
  assert.match(container.innerHTML, /fetchpriority="high"/);
  assert.match(container.innerHTML, /decoding="async"/);
  assert.match(container.innerHTML, />Gofile</);
  assert.match(container.innerHTML, />Streamtape</);
  assert.match(container.innerHTML, />无码 Player4me</);
  assert.ok(
    container.innerHTML.indexOf(">Gofile<") <
      container.innerHTML.indexOf(">Streamtape<"),
  );
  assert.doesNotMatch(
    container.innerHTML,
    /class="video-card__button"[^>]*\saria-label=/u,
  );
});

test("video cards show three Chinese tags and a bounded remainder count", () => {
  const container = containerFixture();
  const names = new Map([
    [1, { nameZh: "黑丝袜" }],
    [2, { nameZh: "黑化" }],
    [3, { nameZh: "战队女英雄" }],
    [4, { nameZh: "危机" }],
  ]);

  renderSearchResults(
    container,
    [videoFixture(44, { tagIds: [1, 2, 3, 4] })],
    { tagLookup: (tagId) => names.get(tagId) ?? null },
  );

  assert.match(container.innerHTML, />黑丝袜</);
  assert.match(container.innerHTML, />黑化</);
  assert.match(container.innerHTML, />战队女英雄</);
  assert.match(container.innerHTML, />\+1</);
  assert.equal(container.innerHTML.includes("危机"), false);
});

test("cards below the first mobile row remain lazily loaded", () => {
  const container = containerFixture();
  renderSearchResults(container, [
    videoFixture(1),
    videoFixture(2),
    videoFixture(3),
  ]);

  assert.equal(
    (container.innerHTML.match(/loading="eager"/gu) ?? []).length,
    2,
  );
  assert.equal(
    (container.innerHTML.match(/fetchpriority="high"/gu) ?? []).length,
    2,
  );
  assert.equal(
    (container.innerHTML.match(/loading="lazy"/gu) ?? []).length,
    1,
  );
  assert.equal(
    (container.innerHTML.match(/ data-src=/gu) ?? []).length,
    1,
  );
  assert.match(
    container.innerHTML,
    /<img class="video-cover" data-src="https:\/\/images\.example\/spsf-3\.jpg"/u,
  );
});

test("deferred covers load only after intersection and are then unobserved", () => {
  const attributes = new Map([
    ["data-src", "https://images.example/deferred.jpg"],
  ]);
  const image = {
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    setAttribute(name, value) {
      attributes.set(name, value);
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
  };
  const container = {
    querySelectorAll(selector) {
      assert.equal(selector, "img.video-cover[data-src]");
      return [image];
    },
  };
  let callback;
  let options;
  const observed = [];
  const unobserved = [];
  class Observer {
    constructor(handler, observerOptions) {
      callback = handler;
      options = observerOptions;
    }
    observe(target) {
      observed.push(target);
    }
    unobserve(target) {
      unobserved.push(target);
    }
  }

  const observer = observeDeferredCovers(Observer, container);

  assert.ok(observer instanceof Observer);
  assert.deepEqual(observed, [image]);
  assert.equal(attributes.has("src"), false);
  assert.equal(options.root, null);
  callback([{ isIntersecting: false, target: image }]);
  assert.equal(attributes.has("src"), false);
  callback([{ isIntersecting: true, target: image }]);
  assert.equal(
    attributes.get("src"),
    "https://images.example/deferred.jpg",
  );
  assert.equal(attributes.has("data-src"), false);
  assert.deepEqual(unobserved, [image]);
});

test("deferred production covers reveal only approved same-origin paths", () => {
  const generation = "a".repeat(64);
  const sources = [
    (
      "/.netlify/images?url=" +
      encodeURIComponent(
        "https://www.giga-web.jp/db_titles/spsf/spsf48/pac_s.jpg",
      ) +
      "&w=320&h=480&fit=cover"
    ),
    `/media/featured-covers/g/${generation}/spsf-48.webp`,
    `/media/featured-covers/g/${generation}/thza-0.webp`,
    `/media/featured-covers/g/${generation}/thza-00.webp`,
    "/admin/private.jpg",
  ];
  const images = sources.map((source) => {
    const attributes = new Map([["data-src", source]]);
    return {
      attributes,
      getAttribute(name) {
        return attributes.get(name) ?? null;
      },
      setAttribute(name, value) {
        attributes.set(name, value);
      },
      removeAttribute(name) {
        attributes.delete(name);
      },
    };
  });
  let callback;
  class Observer {
    constructor(handler) {
      callback = handler;
    }
    observe() {}
    unobserve() {}
  }

  observeDeferredCovers(Observer, {
    querySelectorAll() {
      return images;
    },
  });
  callback(
    images.map((image) => ({
      isIntersecting: true,
      target: image,
    })),
  );

  assert.equal(images[0].attributes.get("src"), sources[0]);
  assert.equal(images[1].attributes.get("src"), sources[1]);
  assert.equal(images[2].attributes.get("src"), sources[2]);
  assert.equal(images[3].attributes.has("src"), false);
  assert.equal(images[4].attributes.has("src"), false);
});

test("production GIGA covers use the restricted Netlify image transform", () => {
  const source =
    "https://www.giga-web.jp/db_titles/spsf/spsf-44/spsf-44.jpg";
  assert.equal(
    optimizedCoverUrl(source, { hostname: "127.0.0.1" }),
    source,
  );
  assert.equal(
    optimizedCoverUrl("https://images.example/cover.jpg", {
      hostname: "guileless-salmiakki-c8941a.netlify.app",
    }),
    "https://images.example/cover.jpg",
  );
  assert.equal(
    optimizedCoverUrl("javascript:alert(1)", {
      hostname: "guileless-salmiakki-c8941a.netlify.app",
    }),
    null,
  );
  assert.equal(
    optimizedCoverUrl(source, {
      hostname: "guileless-salmiakki-c8941a.netlify.app",
      width: 320,
      height: 480,
    }),
    "/.netlify/images?url=https%3A%2F%2Fwww.giga-web.jp%2Fdb_titles%2Fspsf%2Fspsf-44%2Fspsf-44.jpg&w=320&h=480&fit=cover",
  );

  const originalLocation = globalThis.location;
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { hostname: "guileless-salmiakki-c8941a.netlify.app" },
  });
  try {
    const container = containerFixture();
    renderSearchResults(container, [videoFixture(44, { cover: source })]);
    assert.match(
      container.innerHTML,
      /src="\/\.netlify\/images\?url=https%3A%2F%2Fwww\.giga-web\.jp/u,
    );
    assert.match(
      container.innerHTML,
      /data-original-src="https:\/\/www\.giga-web\.jp\/db_titles\//u,
    );
  } finally {
    if (originalLocation === undefined) {
      delete globalThis.location;
    } else {
      Object.defineProperty(globalThis, "location", {
        configurable: true,
        value: originalLocation,
      });
    }
  }
});

test("GitHub Pages keeps remote covers direct and prefixes local assets", () => {
  const source =
    "https://www.giga-web.jp/db_titles/spsf/spsf-44/spsf-44.jpg";
  assert.equal(
    optimizedCoverUrl(source, { hostname: "siwencifudalinzi.github.io" }),
    source,
  );
  assert.equal(
    siteAssetUrl("/media/featured-covers/g/" + "a".repeat(64) + "/spsf-44.webp", {
      baseUrl: "https://siwencifudalinzi.github.io/giga-catalog-cn/",
    }),
    "/giga-catalog-cn/media/featured-covers/g/" + "a".repeat(64) + "/spsf-44.webp",
  );
  assert.equal(
    siteAssetUrl("/media/featured-covers/g/" + "a".repeat(64) + "/spsf-44.webp", {
      baseUrl: "https://giga-catalog-cn.netlify.app/",
    }),
    "/media/featured-covers/g/" + "a".repeat(64) + "/spsf-44.webp",
  );
});

test("empty result output is safe even when its message is untrusted", () => {
  const container = containerFixture();
  const window = renderSearchResults(container, [], {
    emptyMessage: '<img src=x onerror="alert(1)">',
  });

  assert.equal(container.innerHTML.includes("<img"), false);
  assert.match(container.innerHTML, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(container.innerHTML, /class="empty-state"/);
  assert.equal(window.total, 0);
});

test("preview URLs are derived in bounded batches only when requested", () => {
  const video = videoFixture(44, {
    previewBase: "https://images.example/spsf-44/",
    previewCount: 18,
  });
  const container = containerFixture();
  mountSeries(container, seriesFixture([video]));

  assert.equal("previews" in video, false);
  assert.equal(container.innerHTML.includes("001_l.jpg"), false);
  assert.deepEqual(derivePreviewUrls(video), [
    "https://images.example/spsf-44/001_l.jpg",
    "https://images.example/spsf-44/002_l.jpg",
    "https://images.example/spsf-44/003_l.jpg",
    "https://images.example/spsf-44/004_l.jpg",
  ]);
  assert.deepEqual(derivePreviewUrls(video, { start: 4, limit: 2 }), [
    "https://images.example/spsf-44/005_l.jpg",
    "https://images.example/spsf-44/006_l.jpg",
  ]);
  assert.equal(derivePreviewUrls(video, { limit: 99 }).length, 6);
  assert.equal(Object.isFrozen(derivePreviewUrls(video)), true);
  assert.deepEqual(
    derivePreviewUrls({ previewBase: "javascript:alert(1)", previewCount: 18 }),
    [],
  );
});

test("mounting another series unmounts the previous one", () => {
  const first = containerFixture();
  const second = containerFixture();

  mountSeries(first, seriesFixture([videoFixture(1)]));
  mountSeries(second, seriesFixture([videoFixture(2)]));

  assert.equal(first.innerHTML, "");
  assert.equal(classCount(second.innerHTML, "video-card"), 1);
  unmountSeries(second);
  assert.equal(second.innerHTML, "");
});
  const generation = "b".repeat(64);
