export function createCatalogPrefetcher({ getLoader, getGeneration, connection }) {
  let generation;
  const warmed = new Set();
  return function warm(kind, code) {
    if (connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType)
      || (Number.isFinite(connection?.downlink) && connection.downlink < 1)) return;
    const loader = getLoader();
    const current = getGeneration();
    if (!loader || !current) return;
    if (generation !== current) { generation = current; warmed.clear(); }
    const key = kind === "search" ? "search" : `series:${code}`;
    if (warmed.has(key) || warmed.size >= 3) return;
    warmed.add(key);
    // Speculative failure stays silent; explicit navigation still owns retry/UI.
    Promise.resolve().then(() => kind === "search" ? loader.ensureSearch() : loader.ensureSeries(code)).catch(() => {});
  };
}
