import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import {
  RuntimeCatalogError,
  createRuntimeCatalogStore,
  parseBootstrap,
  parseSearchPayload,
  parseSeriesPayload,
  parseTagPayload,
} from "../../public/js/runtime-catalog.js";

const generation = "a".repeat(64);
const generatedAt = "2026-08-29T00:00:00Z";

function clone(value) {
  return structuredClone(value);
}

function validBootstrap() {
  return {
    schemaVersion: 3,
    generation,
    generatedAt,
    totals: { series: 1, videos: 2, linkedVideos: 2 },
    refresh: { mode: "incremental", sourceComplete: true, counts: { added: 2 } },
    resources: {
      subtitleDirectory: { label: "SRT ENGSUB DOWNLOAD", url: "https://files.example/subtitles" },
    },
    artifacts: {
      search: `runtime/g/${generation}/search.json`,
      tags: `runtime/g/${generation}/tags.json`,
    },
    recentVideos: [
      video("SPSF-2", 2, "第二作"),
      video("SPSF-1", 1, "女战士"),
    ],
    series: [summary()],
  };
}

function summary() {
  return {
    code: "SPSF",
    count: 2,
    firstReleaseDate: "2026-08-01",
    latestReleaseDate: "2026-08-02",
    links: { subtitle: "https://files.example/spsf" },
    artifact: `runtime/g/${generation}/series/spsf.json`,
  };
}

function video(code, number, title) {
  return {
    code,
    number,
    title,
    actors: ["演员"],
    releaseDate: `2026-08-0${number}`,
    cover: `https://images.example/${code.toLowerCase()}.jpg`,
    previewBase: `https://images.example/${code.toLowerCase()}/`,
    previewCount: 4,
    productId: number + 100,
    links: { gofile: `https://files.example/${code.toLowerCase()}` },
    series: "SPSF",
  };
}

function shardVideo(code, number, title) {
  const value = video(code, number, title);
  delete value.series;
  return value;
}

function validSeries() {
  return {
    schemaVersion: 3,
    generation,
    generatedAt,
    series: { ...summary(), videos: [shardVideo("SPSF-1", 1, "女战士"), shardVideo("SPSF-2", 2, "第二作")] },
  };
}

function validSearch() {
  return {
    schemaVersion: 3,
    generation,
    generatedAt,
    videos: [video("SPSF-1", 1, "女战士"), video("SPSF-2", 2, "第二作")],
  };
}

function multiBootstrap() {
  const value = validBootstrap();
  value.totals = { series: 2, videos: 3, linkedVideos: 3 };
  value.series.push({
    code: "NEWS",
    count: 1,
    firstReleaseDate: "2026-08-03",
    latestReleaseDate: "2026-08-03",
    artifact: `runtime/g/${generation}/series/news.json`,
  });
  const recent = video("NEWS-1", 1, "新闻");
  recent.series = "NEWS";
  recent.releaseDate = "2026-08-03";
  value.recentVideos.push(recent);
  return value;
}

function multiSearch() {
  const value = validSearch();
  const news = video("NEWS-1", 1, "新闻");
  news.series = "NEWS";
  news.releaseDate = "2026-08-03";
  value.videos.push(news);
  return value;
}

function validTags() {
  return {
    schemaVersion: 3,
    generation,
    generatedAt,
    tags: [{ id: 6, group: "genre", nameJa: "戦士", nameZh: "战士", count: 1 }],
    assignments: [["SPSF-1", [6]]],
  };
}

function assertInvalid(action) {
  assert.throws(action, (error) => (
    error instanceof RuntimeCatalogError
      && error.message === `运行目录数据无效（${error.kind}）`
      && !error.message.includes("SPSF")
  ));
}

test("runtime store installs generation-bound shards and search records", () => {
  const bootstrap = parseBootstrap(validBootstrap());
  const store = createRuntimeCatalogStore(bootstrap);
  assert.equal(store.getRecentVideos().length, 2);
  assert.equal(store.getSeries("SPSF"), null);

  store.installSeries(parseSeriesPayload(validSeries(), bootstrap, "SPSF"));
  assert.equal(store.getSeries("spsf").videos[0].code, "SPSF-1");

  store.installSearch(parseSearchPayload(validSearch(), bootstrap));
  assert.deepEqual(store.search("女战士").map((value) => value.code), ["SPSF-1"]);
  assert.equal(store.getVideo("spsf_2").code, "SPSF-2");
});

test("every generated V3 series artifact parses against its bootstrap summary", () => {
  const dataRoot = new URL("../../public/data/", import.meta.url);
  const bootstrap = parseBootstrap(JSON.parse(
    readFileSync(new URL("catalog-bootstrap.json", dataRoot), "utf8"),
  ));
  const seriesRoot = new URL(`runtime/g/${bootstrap.generation}/series/`, dataRoot);
  const files = readdirSync(fileURLToPath(seriesRoot)).filter((name) => name.endsWith(".json"));
  assert.equal(files.length, bootstrap.series.length);
  for (const summary of bootstrap.series) {
    const payload = JSON.parse(readFileSync(new URL(summary.artifact, dataRoot), "utf8"));
    const parsed = parseSeriesPayload(payload, bootstrap, summary.code);
    assert.equal(parsed.series.artifact, summary.artifact);
  }
});

test("parsers reject malformed schemas, generations, metadata, paths, and data boundaries", () => {
  const bootstrap = parseBootstrap(validBootstrap());
  const cases = [
    () => { const value = validBootstrap(); value.schemaVersion = 2; return () => parseBootstrap(value); },
    () => { const value = validSearch(); value.generation = "b".repeat(64); return () => parseSearchPayload(value, bootstrap); },
    () => { const value = validTags(); value.generatedAt = "2026-08-30T00:00:00Z"; return () => parseTagPayload(value, bootstrap); },
    () => { const value = validBootstrap(); value.series[0].artifact = `runtime/g/${generation}/series/other.json`; return () => parseBootstrap(value); },
    () => { const value = validSeries(); value.series.code = "NEWS"; return () => parseSeriesPayload(value, bootstrap, "SPSF"); },
    () => { const value = validSearch(); value.videos.push(clone(value.videos[0])); return () => parseSearchPayload(value, bootstrap); },
    () => { const value = validSearch(); value.videos[0].series = "NEWS"; return () => parseSearchPayload(value, bootstrap); },
    () => { const value = validBootstrap(); value.recentVideos[0].cover = "javascript:alert(1)"; return () => parseBootstrap(value); },
    () => { const value = validBootstrap(); value.artifacts.search = `runtime/g/${generation}/../search.json`; return () => parseBootstrap(value); },
    () => { const value = validBootstrap(); value.totals.videos = 3; return () => parseBootstrap(value); },
  ];
  for (const buildCase of cases) {
    assertInvalid(buildCase());
  }
});

test("bootstrap rejects private, local, link-local, and credential-bearing HTTP(S) URLs", () => {
  for (const url of [
    "https://user:password@files.example/private",
    "http://:@files.example/",
    "http:\\\\@files.example/",
    "http:/\\@files.example/",
    "http:////@files.example/",
    "http://localhost/",
    "http://localhost./",
    "http://printer.local/",
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://192.168.0.1/",
    "http://192.0.0.1/",
    "http://192.0.2.1/",
    "http://198.51.100.1/",
    "http://169.254.0.1/",
    "http://[::1]/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
    "http://[::ffff:127.0.0.1]/",
  ]) {
    const value = validBootstrap();
    value.resources.subtitleDirectory.url = url;
    assertInvalid(() => parseBootstrap(value));
  }
});

test("bootstrap retains ordinary public IPv4 literals outside special-use ranges", () => {
  const value = validBootstrap();
  value.resources.subtitleDirectory.url = "https://192.0.1.5/file";
  assert.equal(
    parseBootstrap(value).resources.subtitleDirectory.url,
    "https://192.0.1.5/file",
  );
});

test("bootstrap permits at-signs outside a normalized special-scheme authority", () => {
  for (const url of [
    "https://files.example/path@name",
    "https://files.example/?q=@name",
    "https://files.example/#@name",
  ]) {
    const value = validBootstrap();
    value.resources.subtitleDirectory.url = url;
    assert.equal(parseBootstrap(value).resources.subtitleDirectory.url, url);
  }
});

test("bootstrap and child payloads require real UTC RFC3339 generation timestamps", () => {
  for (const timestamp of [
    "2026-02-30T00:00:00Z",
    "2026-08-29T24:00:00Z",
    "2026-08-29T00:00:00+00:00",
    "2026-08-29",
  ]) {
    const bootstrap = validBootstrap();
    bootstrap.generatedAt = timestamp;
    assertInvalid(() => parseBootstrap(bootstrap));

    const payload = validSearch();
    payload.generatedAt = timestamp;
    assertInvalid(() => parseSearchPayload(payload, bootstrap));
  }
});

test("search validates each declared series and linked-video total before preserving store state", () => {
  const bootstrap = parseBootstrap(validBootstrap());
  const store = createRuntimeCatalogStore(bootstrap);
  store.installSearch(validSearch());
  const before = store.search("第二作").map((value) => value.code);

  const wrongTotals = validBootstrap();
  wrongTotals.totals.linkedVideos = 1;
  assertInvalid(() => parseSearchPayload(validSearch(), parseBootstrap(wrongTotals)));

  const omittedSeries = multiSearch();
  omittedSeries.videos[2] = video("SPSF-3", 3, "第三作");
  const multi = parseBootstrap(multiBootstrap());
  assertInvalid(() => parseSearchPayload(omittedSeries, multi));

  const missingLink = validSearch();
  missingLink.videos[1].title = "不应安装";
  delete missingLink.videos[1].links;
  assertInvalid(() => store.installSearch(missingLink));
  assert.deepEqual(store.search("第二作").map((value) => value.code), before);
  assert.deepEqual(store.search("不应安装"), []);
});

test("tag installation validates against installed search videos and delegates filtering", () => {
  const bootstrap = parseBootstrap(validBootstrap());
  const store = createRuntimeCatalogStore(bootstrap);
  store.installSearch(parseSearchPayload(validSearch(), bootstrap));
  store.installTags(parseTagPayload(validTags(), bootstrap));

  assert.equal(store.getTag(6).nameZh, "战士");
  assert.deepEqual(store.getTags("genre").map((tag) => tag.id), [6]);
  assert.deepEqual(store.filterByTags({ include: [6] }).map((value) => value.code), ["SPSF-1"]);

  const invalid = validTags();
  invalid.assignments = [["UNKNOWN-1", [6]]];
  assertInvalid(() => store.installTags(invalid));
  assert.deepEqual(store.filterByTags({ include: [6] }).map((value) => value.code), ["SPSF-1"]);
});

test("parsers and store clone and deeply freeze source-backed values", () => {
  const bootstrapFixture = validBootstrap();
  const bootstrap = parseBootstrap(bootstrapFixture);
  const store = createRuntimeCatalogStore(bootstrap);
  const seriesFixture = validSeries();
  const parsedSeries = parseSeriesPayload(seriesFixture, bootstrap, "SPSF");
  store.installSeries(parsedSeries);

  bootstrapFixture.recentVideos[0].title = "mutated";
  seriesFixture.series.videos[0].links.gofile = "https://attacker.invalid/";
  assert.equal(store.getRecentVideos()[0].title, "第二作");
  assert.equal(store.getSeries("SPSF").videos[0].links.gofile, "https://files.example/spsf-1");
  assert.equal(Object.isFrozen(store.metadata), true);
  assert.equal(Object.isFrozen(store.getSeries("SPSF").videos), true);
  assert.throws(() => { store.getSeries("SPSF").videos[0].title = "attacker"; }, TypeError);
});
