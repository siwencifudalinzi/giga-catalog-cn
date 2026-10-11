export function fitImage(imageWidth, imageHeight, width, height) {
  const ratio = Math.min(width / Math.max(1, imageWidth), height / Math.max(1, imageHeight));
  return { width: imageWidth * ratio, height: imageHeight * ratio };
}

export function clampTransform(value, bounds) {
  const scale = Math.max(1, Math.min(4, value.scale));
  const maxX = Math.max(0, (bounds.imageWidth * scale - bounds.width) / 2);
  const maxY = Math.max(0, (bounds.imageHeight * scale - bounds.height) / 2);
  return { scale, x: Math.max(-maxX, Math.min(maxX, value.x)), y: Math.max(-maxY, Math.min(maxY, value.y)) };
}

export function zoomAtPoint(value, scale, point, bounds) {
  const next = Math.max(1, Math.min(4, scale));
  const ratio = next / value.scale;
  return clampTransform({ scale: next, x: point.x - (point.x - value.x) * ratio, y: point.y - (point.y - value.y) * ratio }, bounds);
}

export function swipeDirection(dx, dy, scale) {
  return scale <= 1.01 && Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.4 ? (dx < 0 ? 1 : -1) : 0;
}

export function shouldHandleMouseDoubleClick(event, elapsedSinceTouch) {
  return event.pointerType !== "touch" && !event.sourceCapabilities?.firesTouchEvents && elapsedSinceTouch > 700;
}

const MARKER = "gigaImageViewer";
function withoutMarker(value) {
  const result = { ...(value && typeof value === "object" ? value : {}) };
  delete result[MARKER];
  return result;
}

// One history entry belongs to each gallery opening; image changes replace it.
export function createImageViewerNavigation({ history, eventTarget, show, hide, isParentOpen, restoreParent, sessionId = `gallery-${Date.now()}-${Math.random()}` }) {
  let active = null;
  let pendingClose = false;
  let sequence = 0;
  let restorationToken = 0;
  let restoring = false;
  const contexts = new Map();
  const marker = () => history.state?.[MARKER];
  function dismiss({ preserveRestoration = false } = {}) {
    const entry = marker();
    // Browsers run microtasks between popstate listeners. Parent hydration can
    // finish before our listener starts; retain its owned destination as well.
    if (preserveRestoration && (restoring || (entry?.sessionId === sessionId && contexts.has(entry.entryId)))) return;
    restorationToken += 1;
    restoring = false;
    if (marker()) history.replaceState(withoutMarker(history.state), "");
    if (active) hide();
    active = null;
  }
  async function handlePop(event) {
    const token = ++restorationToken;
    restoring = false;
    pendingClose = false;
    const entry = event.state?.[MARKER];
    const context = entry?.sessionId === sessionId ? contexts.get(entry.entryId) : null;
    if (context) {
      if (!isParentOpen(context.code)) {
        restoring = true;
        try { await restoreParent?.(context.code); } catch { /* Failed hydration leaves the underlying detail error available. */ }
        if (token !== restorationToken || marker()?.entryId !== entry.entryId) return;
        restoring = false;
        if (!isParentOpen(context.code)) { dismiss(); return; }
      }
      active = entry.entryId;
      show({ ...context, index: entry.index });
    } else {
      dismiss();
    }
  }
  if (marker()) history.replaceState(withoutMarker(history.state), "");
  eventTarget.addEventListener("popstate", handlePop);
  return {
    open(context) {
      if (pendingClose || restoring || !context.urls?.length || !isParentOpen(context.code)) return false;
      if (active) dismiss();
      active = `${sessionId}-${++sequence}`;
      contexts.set(active, context);
      // Bound retained descriptors while keeping recent Forward entries usable.
      if (contexts.size > 12) contexts.delete(contexts.keys().next().value);
      history.pushState({ ...withoutMarker(history.state), [MARKER]: { sessionId, entryId: active, index: context.index } }, "");
      show(context);
      return true;
    },
    updateIndex(index) {
      const entry = marker();
      if (active && entry?.entryId === active) history.replaceState({ ...history.state, [MARKER]: { ...entry, index } }, "");
    },
    requestClose() {
      if (pendingClose || (!active && !restoring)) return;
      if (restoring) { pendingClose = true; history.back(); return; }
      if (marker()?.entryId === active) { pendingClose = true; history.back(); }
      else dismiss();
    },
    dismiss,
  };
}
