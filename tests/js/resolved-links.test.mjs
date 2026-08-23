import assert from "node:assert/strict";
import test from "node:test";

import {
  createResolvedLinkLoader,
  normalizeResolvedLinkManifest,
  resolveLinkTarget,
  sha256SourceUrl,
} from "../../public/js/resolved-links.js";

const SOURCE = "https://ouo.io/mT78vqU";
const HASH =
  "sha256:8e4a74b155b39a37bc851982ed6c75f3b6ee95f0b42528b11cc6cc62afe198fc";

function manifestWith(finalUrl) {
  return {
    schemaVersion: 1,
    entries: {
      "SPSF-58": {
        gofile: {
          sourceUrlHash: HASH,
          finalUrl,
          kind: "external",
          status: "verified",
          checkedAt: "2026-08-23T00:00:00Z",
        },
      },
    },
  };
}

test("hash binds the exact source URL", async () => {
  assert.equal(await sha256SourceUrl(SOURCE), HASH);
});

test("safe entry resolves direct and changed source falls back", async () => {
  const manifest = normalizeResolvedLinkManifest(
    manifestWith("https://gofile.io/d/N87ugOtd"),
  );
  assert.equal(manifest.size, 1);
  assert.deepEqual(
    await resolveLinkTarget(
      {
        code: "SPSF-58",
        provider: "gofile",
        label: "Gofile",
        sourceUrl: SOURCE,
      },
      manifest,
    ),
    {
      url: "https://gofile.io/d/N87ugOtd",
      label: "直达 Gofile",
      resolved: true,
    },
  );
  assert.deepEqual(
    await resolveLinkTarget(
      {
        code: "SPSF-58",
        provider: "gofile",
        label: "Gofile",
        sourceUrl: "https://ouo.io/changed",
      },
      manifest,
    ),
    {
      url: "https://ouo.io/changed",
      label: "Gofile",
      resolved: false,
    },
  );
});

test("unsafe or media-looking destinations are dropped", () => {
  for (const finalUrl of [
    "http://gofile.io/d/N87ugOtd",
    "https://user:pass@gofile.io/d/N87ugOtd",
    "https://evil.example/d/N87ugOtd",
    "https://streamtape.com/v/file/movie.mp4",
  ]) {
    assert.equal(normalizeResolvedLinkManifest(manifestWith(finalUrl)).size, 0);
  }
});

test("loader fetches once and network failure falls back empty", async () => {
  let calls = 0;
  const load = createResolvedLinkLoader(async () => {
    calls += 1;
    return {
      ok: true,
      json: async () => ({ schemaVersion: 1, entries: {} }),
    };
  });
  assert.equal((await load()).size, 0);
  assert.equal((await load()).size, 0);
  assert.equal(calls, 1);

  const failed = createResolvedLinkLoader(async () => {
    throw new Error("offline");
  });
  assert.equal((await failed()).size, 0);
});
