import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  createPlaybackLoader,
  matchingPlaybackEntries,
  normalizePlaybackConfig,
  playbackHref,
  selectPlaybackEntry,
  warmPlayback,
} from "../../public/js/playback.js";
import { sha256SourceUrl } from "../../public/js/resolved-links.js";

const source = "https://ouo.io/example";
const entry = {
  code: "SPSF-67",
  slot: "standard.streamtape",
  provider: "streamtape",
  sourceUrlHash: await sha256SourceUrl(source),
  sourcePage: "https://streamtape.com/v/Yew1XzJLKACov0/SPSF-67.mp4",
  embedUrl: "https://streamtape.com/e/Yew1XzJLKACov0",
  verifiedAt: "2026-11-06T14:00:00.000Z",
  verification: "10.2 seconds, 305 decoded frames, no media error",
};
const config = (...entries) => ({ schemaVersion: 1, entries });

test("only audited pairs of Streamtape embed and source page are accepted", () => {
  const accepted = normalizePlaybackConfig(config(entry));
  assert.equal(accepted.get(entry.code)[0].embedUrl, entry.embedUrl);
  for (const invalid of [
    { embedUrl: "https://streamtape.com.evil.test/e/Yew1XzJLKACov0" },
    { embedUrl: entry.embedUrl + "?token=secret" },
    { embedUrl: "http://streamtape.com/e/Yew1XzJLKACov0" },
    { embedUrl: "https://streamtape.com/e/different" },
    { sourcePage: entry.sourcePage + "#unexpected" },
    { provider: "gofile" },
    { slot: "subtitle.subtitle" },
    { sourceUrlHash: "bad" },
    { verifiedAt: "bad" },
  ]) {
    assert.equal(normalizePlaybackConfig(config({ ...entry, ...invalid })).size, 0);
  }
});

test("exact code and slot select distinct versions without accepting query URLs", () => {
  const other = { ...entry, slot: "uncensored.streamtape" };
  const indexed = normalizePlaybackConfig(config(entry, other));
  const query = new URL(playbackHref(other), "https://example.test/catalog/").searchParams;
  query.set("url", "https://example.test/evil");
  assert.deepEqual(selectPlaybackEntry(indexed, query), other);
  query.set("code", "SPSF-65");
  assert.equal(selectPlaybackEntry(indexed, query), undefined);
  const duplicated = normalizePlaybackConfig(config(entry, entry, other));
  assert.deepEqual(duplicated.get(entry.code), [other]);
});

test("catalog entries require the current exact source slot and hash", async () => {
  const indexed = normalizePlaybackConfig(config(entry));
  const link = { slot: entry.slot, url: source };
  assert.deepEqual(await matchingPlaybackEntries(entry.code, [link], indexed), [entry]);
  assert.deepEqual(await matchingPlaybackEntries(entry.code, [{ ...link, url: source + "x" }], indexed), []);
  assert.deepEqual(await matchingPlaybackEntries(entry.code, [{ ...link, slot: "standard.reupload" }], indexed), []);
  assert.deepEqual(await matchingPlaybackEntries("SPSF-65", [link], indexed), []);
});

test("a failed configuration request resolves to an empty index", async () => {
  let requests = 0;
  const load = createPlaybackLoader(async () => {
    requests++;
    throw new Error("offline");
  });
  assert.equal((await load()).size, 0);
  assert.equal((await load()).size, 0);
  assert.equal(requests, 1);
});

test("warming is deduplicated by origin and href, and respects data saving", () => {
  const hints = [];
  const doc = {
    baseURI: "https://example.test/catalog/",
    head: { querySelectorAll: () => hints, append: hint => hints.push(hint) },
    createElement: () => ({}),
  };
  warmPlayback(entry, doc, { saveData: true });
  assert.equal(hints.length, 0);
  warmPlayback(entry, doc);
  warmPlayback(entry, doc);
  warmPlayback({ ...entry, slot: "uncensored.streamtape" }, doc);
  assert.deepEqual(hints.map(({ rel, href }) => ({ rel, href })), [
    { rel: "preconnect", href: "https://streamtape.com" },
    { rel: "prefetch", href: new URL(playbackHref(entry), doc.baseURI).href },
    { rel: "prefetch", href: new URL(playbackHref({ ...entry, slot: "uncensored.streamtape" }), doc.baseURI).href },
  ]);
});

test("published playback config contains only valid, unique entries", () => {
  const raw = JSON.parse(readFileSync(new URL("../../public/data/playback.json", import.meta.url)));
  const normalized = [...normalizePlaybackConfig(raw).values()].flat();
  assert.equal(normalized.length, raw.entries.length);
});
