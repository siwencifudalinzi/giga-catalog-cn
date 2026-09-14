const STATE_NAMESPACE = "gigaCatalog";

function objectState(value) {
  return value && typeof value === "object" ? value : {};
}

function detailFromState(state) {
  const detail = objectState(state)[STATE_NAMESPACE]?.videoDetail;
  if (
    !detail ||
    typeof detail !== "object" ||
    typeof detail.code !== "string" ||
    typeof detail.sessionId !== "string" ||
    typeof detail.entryId !== "string"
  ) {
    return null;
  }
  return detail;
}

function hasDetailState(state) {
  return Object.hasOwn(objectState(objectState(state)[STATE_NAMESPACE]), "videoDetail");
}

function mergeDetailState(currentState, detail) {
  const base = objectState(currentState);
  const namespace = objectState(base[STATE_NAMESPACE]);
  return {
    ...base,
    [STATE_NAMESPACE]: {
      ...namespace,
      videoDetail: detail,
    },
  };
}

function withoutDetailState(currentState) {
  const base = { ...objectState(currentState) };
  const namespace = { ...objectState(base[STATE_NAMESPACE]) };
  delete namespace.videoDetail;
  if (Object.keys(namespace).length) base[STATE_NAMESPACE] = namespace;
  else delete base[STATE_NAMESPACE];
  return base;
}

function focusableCandidate(candidate) {
  return Boolean(
    candidate &&
    candidate.isConnected !== false &&
    candidate.hidden !== true &&
    typeof candidate.focus === "function",
  );
}

export function restoreVideoDetailFocus(origin, fallback) {
  const target = focusableCandidate(origin)
    ? origin
    : focusableCandidate(fallback)
      ? fallback
      : null;
  target?.focus({ preventScroll: true });
  return target;
}

export function bindVideoDetailCloseRequests(dialog, requestClose) {
  const onClick = (event) => {
    if (event.target === dialog) {
      requestClose?.("backdrop");
    } else if (event.target?.closest?.("[data-action='close-dialog']")) {
      requestClose?.("button");
    }
  };
  const onCancel = (event) => {
    event.preventDefault();
    requestClose?.("cancel");
  };
  dialog?.addEventListener?.("click", onClick);
  dialog?.addEventListener?.("cancel", onCancel);
  return () => {
    dialog?.removeEventListener?.("click", onClick);
    dialog?.removeEventListener?.("cancel", onCancel);
  };
}

export function createVideoDialogCloseLifecycle(cleanup) {
  let openGeneration = 0;
  return {
    beginOpen() {
      openGeneration += 1;
      return openGeneration;
    },
    beginClose() {
      const closeGeneration = openGeneration;
      let delivered = false;
      return () => {
        if (delivered || closeGeneration !== openGeneration) return false;
        delivered = true;
        cleanup?.();
        return true;
      };
    },
    cleanupNow() {
      openGeneration += 1;
      cleanup?.();
    },
  };
}

export function createVideoDetailNavigation({
  history,
  eventTarget,
  sessionId,
  prepareDetail,
  showDetail,
  hideDetail,
  isDetailOpen,
  onActiveChange,
  resolveOrigin,
} = {}) {
  const ownedEntryIds = new Set();
  let nextEntryId = 0;
  let requestToken = 0;
  let targetCode = null;
  let activeCode = null;
  let activeContext = null;
  let pending = null;
  let traversalPending = false;
  let started = false;

  const isOwned = (detail) => Boolean(
    detail &&
    detail.sessionId === sessionId &&
    ownedEntryIds.has(detail.entryId),
  );

  const setActive = (code, context = null) => {
    activeCode = code;
    activeContext = context;
    onActiveChange?.(code);
  };

  const cancelPending = () => {
    requestToken += 1;
    targetCode = null;
    pending = null;
  };

  const closeDirect = (reason) => {
    const hadActivity = Boolean(targetCode || activeCode || isDetailOpen?.());
    const context = activeContext;
    const code = activeCode;
    cancelPending();
    setActive(null, null);
    if (hadActivity) hideDetail?.({ code, context, reason });
    return hadActivity;
  };

  async function open(code, context = {}, { historyMode = "push", force = false } = {}) {
    const requestedCode = String(code ?? "").trim().toUpperCase();
    if (!requestedCode) return false;
    if (traversalPending) return false;
    if (!force && activeCode === requestedCode && isDetailOpen?.() !== false) {
      if (pending && targetCode !== requestedCode) cancelPending();
      return true;
    }
    if (!force && targetCode === requestedCode && pending) return pending;

    const token = ++requestToken;
    targetCode = requestedCode;
    const task = (async () => {
      let video;
      try {
        video = await prepareDetail?.(requestedCode, context);
      } catch {
        video = null;
      }
      if (!video || token !== requestToken || targetCode !== requestedCode) {
        return false;
      }
      const renderedCode = String(video.code ?? requestedCode).trim().toUpperCase();
      showDetail?.(video, context);
      setActive(renderedCode, context);
      targetCode = renderedCode;

      if (historyMode === "push") {
        const current = detailFromState(history?.state);
        if (!(isOwned(current) && current.code === renderedCode)) {
          const entryId = `${sessionId}:${++nextEntryId}`;
          ownedEntryIds.add(entryId);
          history?.pushState?.(
            mergeDetailState(history.state, {
              code: renderedCode,
              entryId,
              sessionId,
            }),
            "",
          );
        }
      }
      return true;
    })();
    pending = task;
    try {
      return await task;
    } finally {
      if (pending === task) pending = null;
    }
  }

  function handlePopState() {
    traversalPending = false;
    const detail = detailFromState(history?.state);
    if (detail && isOwned(detail)) {
      const context = {
        reason: "popstate",
        trigger: resolveOrigin?.(detail.code) ?? null,
      };
      void open(detail.code, context, { historyMode: "none" });
      return;
    }
    if (hasDetailState(history?.state)) {
      history?.replaceState?.(withoutDetailState(history.state), "");
    }
    closeDirect("popstate");
  }

  function normalizeInitialState() {
    const detail = detailFromState(history?.state);
    if (hasDetailState(history?.state) && !isOwned(detail)) {
      history?.replaceState?.(withoutDetailState(history.state), "");
    }
  }

  function start() {
    if (started) return;
    started = true;
    normalizeInitialState();
    eventTarget?.addEventListener?.("popstate", handlePopState);
  }

  function stop() {
    if (!started) return;
    started = false;
    eventTarget?.removeEventListener?.("popstate", handlePopState);
    cancelPending();
  }

  function requestClose(reason = "request") {
    cancelPending();
    if (traversalPending) return "back";
    const detail = detailFromState(history?.state);
    if (isOwned(detail)) {
      traversalPending = true;
      history?.back?.();
      return "back";
    }
    closeDirect(reason);
    return "direct";
  }

  function leaveForListAction(reason = "list-action") {
    cancelPending();
    const detail = detailFromState(history?.state);
    if (isOwned(detail)) {
      history?.replaceState?.(withoutDetailState(history.state), "");
      if (!traversalPending) {
        traversalPending = true;
        history?.back?.();
      }
      return "back";
    }
    if (traversalPending) return "back";
    closeDirect(reason);
    return "direct";
  }

  return {
    handlePopState,
    leaveForListAction,
    open,
    requestClose,
    start,
    stop,
    whenIdle: () => pending ?? Promise.resolve(),
  };
}
