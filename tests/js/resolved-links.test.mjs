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

test("Vidara watch pages become direct external landings", async () => {
  for (const [finalUrl, provider, expectedUrl, expectedLabel] of [
    [
      "https://vidara.to/e/GSHPFUIm9UPKy",
      "vidara",
      "https://vidara.to/e/GSHPFUIm9UPKy",
      "直达 Vidara",
    ],
    [
      "https://vidara.so/v/6uTHDGn6r8BA4",
      "vidara",
      "https://vidara.so/v/6uTHDGn6r8BA4",
      "直达 Vidara",
    ],
  ]) {
    const raw = manifestWith(finalUrl);
    raw.entries["SPSF-58"]["standard.gofile"].provider = provider;
    const manifest = normalizeResolvedLinkManifest(raw);
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
        url: expectedUrl,
        label: expectedLabel,
        resolved: true,
      },
    );
  }
});

test("an unverified Player4me landing is rejected so the sheet link remains the fallback", () => {
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
  assert.equal(manifest.size, 0);
});

test("a playback-verified Player4me landing remains eligible for direct use", () => {
  const raw = {
    schemaVersion: 2,
    entries: {
      "SPSF-52": {
        "standard.player4me": {
          provider: "player4me",
          sourceUrlHash: HASH,
          finalUrl: "https://gigaandzen.embed4me.com/#a3nxx",
          kind: "external",
          status: "verified",
          playbackStatus: "verified",
          checkedAt: "2026-08-23T00:00:00Z",
        },
      },
    },
  };
  assert.equal(normalizeResolvedLinkManifest(raw).size, 1);
});

test("unsafe or non-watch destinations are dropped", () => {
  for (const [finalUrl, provider = "gofile"] of [
    ["http://gofile.io/d/N87ugOtd"],
    ["https://user:pass@gofile.io/d/N87ugOtd"],
    ["https://evil.example/d/N87ugOtd"],
    ["https://streamtape.com/get_video?id=file", "streamtape"],
    ["https://evil.embed4me.com/#a3nxx", "player4me"],
    ["https://gigaandzen.embed4me.com/#bad-value", "player4me"],
    ["https://strmup.cc/", "strmup"],
    ["https://ww19.strmup.to/", "strmup"],
    ["https://strmup.to/", "strmup"],
    ["https://strmup.to/get/t/file-id", "strmup"],
    ["https://strmup.to/edm0O2yFbplzH?ch=1&js=temporary&sid=session", "strmup"],
    ["https://vidara.to/", "vidara"],
    ["https://vidara.to/download/GSHPFUIm9UPKy", "vidara"],
    ["https://vidara.to:444/e/GSHPFUIm9UPKy", "vidara"],
    ["https://evil.vidara.to/e/GSHPFUIm9UPKy", "vidara"],
  ]) {
    const raw = manifestWith(finalUrl);
    raw.entries["SPSF-58"]["standard.gofile"].provider = provider;
    assert.equal(normalizeResolvedLinkManifest(raw).size, 0);
  }
});

test("a final landing domain must match the declared provider", () => {
  const raw = manifestWith("https://streamtape.com/v/id/SPSF-58.mp4");
  assert.equal(normalizeResolvedLinkManifest(raw).size, 0);
});

test("a stale source slot label may resolve to a different allowlisted provider", async () => {
  const raw = manifestWith("https://gigaandzen.embed4me.com/#nrf8u");
  raw.entries["SPSF-58"]["standard.gofile"].provider = "player4me";
  raw.entries["SPSF-58"]["standard.gofile"].playbackStatus = "verified";
  const manifest = normalizeResolvedLinkManifest(raw);
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
      url: "https://gigaandzen.embed4me.com/#nrf8u",
      label: "直达 Player4me",
      resolved: true,
    },
  );
});

test("a generic reupload slot can resolve to its actual allowlisted provider", async () => {
  const raw = manifestWith("https://gofile.io/d/N87ugOtd");
  raw.entries["SPSF-58"]["standard.reupload"] =
    raw.entries["SPSF-58"]["standard.gofile"];
  delete raw.entries["SPSF-58"]["standard.gofile"];
  const manifest = normalizeResolvedLinkManifest(raw);
  assert.equal(manifest.size, 1);
  assert.deepEqual(
    await resolveLinkTarget(
      {
        code: "SPSF-58",
        provider: "reupload",
        slot: "standard.reupload",
        label: "重传链接",
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
