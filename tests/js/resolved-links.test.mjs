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
    schemaVersion: 2,
    entries: {
      "SPSF-58": {
        "standard.gofile": {
          provider: "gofile",
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
        slot: "standard.gofile",
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
        slot: "standard.gofile",
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

test("Streamtape mp4-looking watch pages remain external landing pages", () => {
  const manifest = normalizeResolvedLinkManifest({
    schemaVersion: 2,
    entries: {
      "SPSF-58": {
        "standard.streamtape": {
          provider: "streamtape",
          sourceUrlHash: HASH,
          finalUrl: "https://streamtape.com/v/dKVZ8pvyRduk8vA/SPSF-58.mp4",
          kind: "external",
          status: "verified",
          checkedAt: "2026-08-23T00:00:00Z",
        },
      },
    },
  });
  assert.equal(manifest.size, 1);
});

test("the exact Player4me landing host accepts its required content fragment", () => {
  const manifest = normalizeResolvedLinkManifest({
    schemaVersion: 2,
    entries: {
      "SPSF-52": {
        "standard.player4me": {
          provider: "player4me",
          sourceUrlHash: HASH,
          finalUrl: "https://gigaandzen.embed4me.com/#a3nxx",
          kind: "external",
          status: "verified",
          checkedAt: "2026-08-23T00:00:00Z",
        },
      },
    },
  });
  assert.equal(manifest.size, 1);
});

test("unsafe or non-watch destinations are dropped", () => {
  for (const finalUrl of [
    "http://gofile.io/d/N87ugOtd",
    "https://user:pass@gofile.io/d/N87ugOtd",
    "https://evil.example/d/N87ugOtd",
    "https://streamtape.com/get_video?id=file",
    "https://evil.embed4me.com/#a3nxx",
    "https://gigaandzen.embed4me.com/#bad-value",
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
      json: async () => ({ schemaVersion: 2, entries: {} }),
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
