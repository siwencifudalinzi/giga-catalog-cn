const ALLOWED_HOSTS = new Set([
  "gofile.io",
  "www.gofile.io",
  "streamtape.com",
  "gigaandzen.embed4me.com",
]);
const PROVIDER_LABELS = Object.freeze({
  gofile: "Gofile",
  streamtape: "Streamtape",
  player4me: "Player4me",
});

function keyFor(code, slot) {
  return `${code}\u0000${slot}`;
}

function normalizeFinalUrl(value) {
  if (typeof value !== "string" || value.length > 2048) {
    return null;
  }
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      !ALLOWED_HOSTS.has(url.hostname)
    ) {
      return null;
    }
    if (
      ["gofile.io", "www.gofile.io"].includes(url.hostname) &&
      !/^\/d\/[A-Za-z0-9]+\/?$/u.test(url.pathname)
    ) {
      return null;
    }
    if (
      url.hostname === "streamtape.com" &&
      !/^\/(?:v|e)\/[A-Za-z0-9_-]+(?:\/[^/?#]*)?\/?$/u.test(url.pathname)
    ) {
      return null;
    }
    if (
      url.hostname === "gigaandzen.embed4me.com" &&
      (!/^\/?$/u.test(url.pathname) || !/^#[A-Za-z0-9]+$/u.test(url.hash))
    ) {
      return null;
    }
    if (url.hostname !== "gigaandzen.embed4me.com" && url.hash) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

function providerForFinalUrl(value) {
  const host = new URL(value).hostname;
  if (["gofile.io", "www.gofile.io"].includes(host)) return "gofile";
  if (host === "streamtape.com") return "streamtape";
  if (host === "gigaandzen.embed4me.com") return "player4me";
  return null;
}

export function normalizeResolvedLinkManifest(raw) {
  const result = new Map();
  if (
    !raw ||
    raw.schemaVersion !== 2 ||
    !raw.entries ||
    typeof raw.entries !== "object" ||
    Array.isArray(raw.entries)
  ) {
    return result;
  }
  for (const [rawCode, slots] of Object.entries(raw.entries)) {
    const code = rawCode.normalize("NFKC").trim().toUpperCase();
    if (!code || !slots || typeof slots !== "object") {
      continue;
    }
    for (const [slot, entry] of Object.entries(slots)) {
      const finalUrl = normalizeFinalUrl(entry?.finalUrl);
      if (
        !/^(?:standard|uncensored)\.(?:gofile|streamtape|player4me)$/u.test(slot) ||
        providerForFinalUrl(finalUrl || "https://invalid.invalid/") !== entry?.provider ||
        entry?.kind !== "external" ||
        entry?.status !== "verified" ||
        !/^sha256:[0-9a-f]{64}$/u.test(entry?.sourceUrlHash || "") ||
        !finalUrl
      ) {
        continue;
      }
      result.set(
        keyFor(code, slot),
        Object.freeze({ ...entry, finalUrl }),
      );
    }
  }
  return result;
}

export async function sha256SourceUrl(
  sourceUrl,
  subtle = globalThis.crypto?.subtle,
) {
  if (!subtle) {
    throw new Error("Web Crypto unavailable");
  }
  const bytes = new TextEncoder().encode(sourceUrl);
  const digest = await subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
  return `sha256:${hex}`;
}

export async function resolveLinkTarget(input, manifest, subtle) {
  const fallback = {
    url: input.sourceUrl,
    label: input.label,
    resolved: false,
  };
  const code = input.code.normalize("NFKC").trim().toUpperCase();
  const entry = manifest?.get(keyFor(code, input.slot));
  if (!entry) {
    return fallback;
  }
  try {
    return (await sha256SourceUrl(input.sourceUrl, subtle)) ===
      entry.sourceUrlHash
      ? {
          url: entry.finalUrl,
          label: `直达 ${PROVIDER_LABELS[entry.provider] || input.label}`,
          resolved: true,
        }
      : fallback;
  } catch {
    return fallback;
  }
}

export function createResolvedLinkLoader(fetcher = globalThis.fetch) {
  let pending;
  return () => {
    if (!pending) {
      pending = Promise.resolve()
        .then(() =>
          fetcher(new URL("../data/resolved-links.json", import.meta.url), {
            headers: { Accept: "application/json" },
          })
        )
        .then(async (response) =>
          response?.ok
            ? normalizeResolvedLinkManifest(await response.json())
            : new Map()
        )
        .catch(() => new Map());
    }
    return pending;
  };
}
