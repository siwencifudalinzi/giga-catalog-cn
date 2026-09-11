const ENTRY_KEYS = Object.freeze([
  "action", "at", "code", "id", "provider", "slot", "source",
]);
const SOURCES = new Set(["catalog", "resolved"]);
const ACTIONS = new Set(["added", "updated", "removed", "resolved"]);
const PROVIDERS = new Set(["reupload", "streamtape", "player4me", "vidara", "gofile"]);
const SOURCE_LABELS = Object.freeze({ catalog: "表格导入", resolved: "直达解析" });
const ACTION_LABELS = Object.freeze({
  added: "新增链接",
  updated: "链接已更新",
  removed: "链接已移除",
  resolved: "直达链接可用",
});

function isIsoDate(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function isEntry(value) {
  return Boolean(value)
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).sort().join("|") === ENTRY_KEYS.join("|")
    && typeof value.id === "string"
    && isIsoDate(value.at)
    && SOURCES.has(value.source)
    && /^[A-Z0-9]+-[0-9]+$/u.test(value.code)
    && /^(standard|uncensored)\.(reupload|streamtape|player4me|vidara|gofile)$/u.test(value.slot)
    && ACTIONS.has(value.action)
    && PROVIDERS.has(value.provider);
}

export function parseLinkUpdates(value) {
  if (
    !value
    || typeof value !== "object"
    || Array.isArray(value)
    || Object.keys(value).sort().join("|") !== "entries|generatedAt|schemaVersion"
    || value.schemaVersion !== 1
    || !isIsoDate(value.generatedAt)
    || !Array.isArray(value.entries)
    || !value.entries.every(isEntry)
  ) {
    throw new TypeError("invalid link update entry or manifest");
  }
  return Object.freeze({
    schemaVersion: 1,
    generatedAt: value.generatedAt,
    entries: Object.freeze(value.entries.map((entry) => Object.freeze({ ...entry }))),
  });
}

function beijingDate(value) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(value));
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function groupLinkUpdates(entries) {
  const groups = [];
  for (const entry of entries) {
    const date = beijingDate(entry.at);
    let group = groups.at(-1);
    if (!group || group.date !== date) {
      group = { date, items: [] };
      groups.push(group);
    }
    group.items.push({
      ...entry,
      sourceLabel: SOURCE_LABELS[entry.source],
      actionLabel: ACTION_LABELS[entry.action],
      editionLabel: entry.slot.startsWith("uncensored.") ? "无码版" : "普通版",
    });
  }
  return groups;
}

export function filterLinkUpdates(entries, source = "all") {
  if (source === "catalog" || source === "resolved") {
    return entries.filter((entry) => entry.source === source);
  }
  return [...entries];
}

export async function fetchLinkUpdates(fetchImpl = globalThis.fetch) {
  const response = await fetchImpl(new URL("../data/link-updates.json", import.meta.url), {
    cache: "no-cache",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error("link update request failed");
  return parseLinkUpdates(await response.json());
}
