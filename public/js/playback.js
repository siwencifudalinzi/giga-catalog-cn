import { normalizeFinalUrl, sha256SourceUrl } from "./resolved-links.js";

export function streamtapeEmbedUrl(sourcePage) {
  const url = normalizeFinalUrl(sourcePage);
  const match = url?.match(/^https:\/\/streamtape\.com\/[ve]\/([A-Za-z0-9_-]+)(?:\/[^?#]*)?$/u);
  return match ? "https://streamtape.com/e/" + match[1] : null;
}

export function normalizePlaybackConfig(raw) {
  const index = new Map();
  if (raw?.schemaVersion !== 1 || !Array.isArray(raw.entries)) return index;

  const seen = new Set();
  const duplicates = new Set();
  for (const entry of raw.entries) {
    if (!entry || typeof entry.code !== "string" ||
        !/^[A-Z0-9]+(?:-[A-Z0-9]+)+$/u.test(entry.code) ||
        typeof entry.slot !== "string" ||
        !/^(?:standard|uncensored)\.(?:reupload|streamtape|gofile|player4me|javryo)$/u.test(entry.slot)) continue;
    const key = entry.code + "/" + entry.slot;
    if (seen.has(key)) duplicates.add(key);
    seen.add(key);

    const embedUrl = entry.provider === "streamtape" ? streamtapeEmbedUrl(entry.sourcePage) : null;
    if (!embedUrl || embedUrl !== entry.embedUrl ||
        typeof entry.sourceUrlHash !== "string" ||
        !/^sha256:[0-9a-f]{64}$/u.test(entry.sourceUrlHash) ||
        typeof entry.verifiedAt !== "string" || !Number.isFinite(Date.parse(entry.verifiedAt)) ||
        typeof entry.verification !== "string" || !entry.verification.trim()) continue;

    if (!index.has(entry.code)) index.set(entry.code, []);
    index.get(entry.code).push(Object.freeze({ ...entry, sourcePage: normalizeFinalUrl(entry.sourcePage) }));
  }
  for (const [code, entries] of index) {
    const unique = entries.filter(entry => !duplicates.has(code + "/" + entry.slot));
    if (unique.length) index.set(code, unique);
    else index.delete(code);
  }
  return index;
}

export function playbackHref({ code, slot }) {
  return "watch/index.html?" + new URLSearchParams({ code, slot });
}

export function selectPlaybackEntry(index, query) {
  return index.get(query.get("code"))?.find(entry => entry.slot === query.get("slot"));
}

export async function matchingPlaybackEntries(code, links, index) {
  const matches = await Promise.all((index.get(code) || []).map(async entry => {
    const link = links.find(item => item.slot === entry.slot);
    return link && await sha256SourceUrl(link.url) === entry.sourceUrlHash ? entry : null;
  }));
  return matches.filter(Boolean);
}

export function createPlaybackLoader(fetcher = globalThis.fetch) {
  let pending;
  return () => {
    pending ??= Promise.resolve()
      .then(() => fetcher(new URL("../data/playback.json", import.meta.url)))
      .then(async response => response?.ok ? normalizePlaybackConfig(await response.json()) : new Map())
      .catch(() => new Map());
    return pending;
  };
}

export function warmPlayback(entry, doc = globalThis.document, connection = globalThis.navigator?.connection) {
  if (connection?.saveData || !doc?.head) return;
  const hints = [...doc.head.querySelectorAll("link[rel=preconnect], link[rel=prefetch]")];
  const origin = new URL(entry.embedUrl).origin;
  const pageUrl = new URL(playbackHref(entry), doc.baseURI).href;
  for (const [rel, href] of [["preconnect", origin], ["prefetch", pageUrl]]) {
    if (hints.some(hint => hint.rel === rel && hint.href.replace(/\/$/u, "") === href)) continue;
    const hint = doc.createElement("link");
    hint.rel = rel;
    hint.href = href;
    hint.referrerPolicy = "no-referrer";
    doc.head.append(hint);
  }
}
