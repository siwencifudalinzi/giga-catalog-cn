import assert from "node:assert/strict";
import test from "node:test";

import * as application from "../../public/js/app.js";

function createHistory(initialState = null, { deferTraversal = false } = {}) {
  const entries = [{ state: initialState }];
  let index = 0;
  let pendingBacks = 0;
  const popstateListeners = new Set();
  const dispatchPopstate = () => {
    for (const listener of popstateListeners) listener({ state: entries[index].state });
  };
  const applyBack = () => {
    if (index === 0) return false;
    index -= 1;
    dispatchPopstate();
    return true;
  };
  return {
    entries,
    backCalls: 0,
    forwardCalls: 0,
    externalBacks: 0,
    get state() {
      return entries[index].state;
    },
    pushState(state) {
      entries.splice(index + 1, entries.length, { state });
      index += 1;
    },
    replaceState(state) {
      entries[index] = { state };
    },
    back() {
      this.backCalls += 1;
      if (index === 0) {
        this.externalBacks += 1;
        return;
      }
      if (deferTraversal) {
        pendingBacks += 1;
        return;
      }
      applyBack();
    },
    flushBack() {
      if (!pendingBacks) return false;
      pendingBacks -= 1;
      return applyBack();
    },
    forward() {
      this.forwardCalls += 1;
      if (index >= entries.length - 1) return;
      index += 1;
      dispatchPopstate();
    },
    eventTarget: {
      addEventListener(type, listener) {
        if (type === "popstate") popstateListeners.add(listener);
      },
      removeEventListener(type, listener) {
        if (type === "popstate") popstateListeners.delete(listener);
      },
    },
  };
}

function createNavigationHarness({
  deferTraversal = false,
  initialState = { list: true },
  prepareDetail,
} = {}) {
  const history = createHistory(initialState, { deferTraversal });
  const shown = [];
  const hidden = [];
  let open = false;
  const navigation = application.createVideoDetailNavigation({
    history,
    eventTarget: history.eventTarget,
    sessionId: "test-session",
    prepareDetail: prepareDetail ?? (async (code) => ({ code })),
    showDetail(video, context) {
      open = true;
      shown.push([video.code, context]);
    },
    hideDetail(context) {
      open = false;
      hidden.push(context);
    },
    isDetailOpen: () => open,
  });
  navigation.start();
  return { history, hidden, navigation, shown };
}

function deferred() {
  let resolve;
  const promise = new Promise((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

class FakeDialog {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type, target = this) {
    let prevented = false;
    const event = {
      target,
      preventDefault() {
        prevented = true;
      },
    };
    for (const listener of this.listeners.get(type) ?? []) listener(event);
    return { prevented };
  }
}

test("a successful user open creates one namespaced detail entry without losing existing state", async () => {
  assert.equal(typeof application.createVideoDetailNavigation, "function");
  const history = createHistory({ analytics: { campaign: "autumn" } });
  const shown = [];
  const navigation = application.createVideoDetailNavigation({
    history,
    sessionId: "test-session",
    prepareDetail: async (code) => ({ code }),
    showDetail: (video) => shown.push(video.code),
  });

  const opened = await navigation.open("SPSF-1", { source: "recent" });

  assert.equal(opened, true);
  assert.deepEqual(shown, ["SPSF-1"]);
  assert.equal(history.entries.length, 2);
  assert.deepEqual(history.state.analytics, { campaign: "autumn" });
  assert.equal(history.state.gigaCatalog.videoDetail.code, "SPSF-1");
  assert.equal(history.state.gigaCatalog.videoDetail.sessionId, "test-session");
});

test("Back closes without changing list context and Forward reopens without growing history", async () => {
  const listContext = {
    query: "actor name",
    visible: 72,
    scrollY: 1840,
    results: ["SPSF-1", "SPSF-2"],
  };
  const { history, hidden, navigation, shown } = createNavigationHarness({
    initialState: { listContext },
  });

  await navigation.open("SPSF-1", { source: "search" });
  assert.equal(history.entries.length, 2);
  history.back();
  assert.equal(hidden.length, 1);
  assert.deepEqual(history.state.listContext, listContext);

  history.forward();
  await navigation.whenIdle();
  assert.equal(shown.at(-1)[0], "SPSF-1");
  assert.equal(history.entries.length, 2);
  history.back();
  history.forward();
  await navigation.whenIdle();
  assert.equal(history.entries.length, 2);
  assert.deepEqual(history.entries[0].state.listContext, listContext);
});

test("all dialog dismissal gestures request the same owned-history close", () => {
  assert.equal(typeof application.bindVideoDetailCloseRequests, "function");
  const dialog = new FakeDialog();
  const reasons = [];
  const unbind = application.bindVideoDetailCloseRequests(dialog, (reason) => {
    reasons.push(reason);
  });
  const closeButton = {
    closest(selector) {
      return selector === "[data-action='close-dialog']" ? this : null;
    },
  };

  dialog.emit("click", closeButton);
  dialog.emit("click", dialog);
  const cancel = dialog.emit("cancel");

  assert.deepEqual(reasons, ["button", "backdrop", "cancel"]);
  assert.equal(cancel.prevented, true);
  unbind();
  dialog.emit("cancel");
  assert.equal(reasons.length, 3);
});

test("an intentional owned close goes Back once, while the next ordinary Back stays native", async () => {
  const { history, hidden, navigation } = createNavigationHarness();
  await navigation.open("SPSF-2", { source: "series" });

  assert.equal(navigation.requestClose("button"), "back");
  assert.equal(history.backCalls, 1);
  assert.equal(hidden.length, 1);
  assert.equal(navigation.requestClose("button"), "direct");
  assert.equal(history.backCalls, 1);

  history.back();
  assert.equal(history.externalBacks, 1);
});

test("a delayed native close from Back cannot erase a rapid Forward reopen", async () => {
  assert.equal(typeof application.createVideoDialogCloseLifecycle, "function");
  const history = createHistory({ list: true });
  const pendingCloseEvents = [];
  let dialogOpen = false;
  let renderedCode = null;
  let activeCode = null;
  const lifecycle = application.createVideoDialogCloseLifecycle(() => {
    renderedCode = null;
    activeCode = null;
  });
  const navigation = application.createVideoDetailNavigation({
    history,
    eventTarget: history.eventTarget,
    sessionId: "test-session",
    prepareDetail: async (code) => ({ code }),
    showDetail(video) {
      lifecycle.beginOpen();
      dialogOpen = true;
      renderedCode = video.code;
    },
    hideDetail() {
      pendingCloseEvents.push(lifecycle.beginClose());
      dialogOpen = false;
    },
    isDetailOpen: () => dialogOpen,
    onActiveChange: (code) => {
      activeCode = code;
    },
  });
  navigation.start();
  await navigation.open("SPSF-41");

  history.back();
  history.forward();
  await navigation.whenIdle();
  const oldCloseDelivered = pendingCloseEvents.shift()();

  assert.equal(oldCloseDelivered, false);
  assert.equal(dialogOpen, true);
  assert.equal(renderedCode, "SPSF-41");
  assert.equal(activeCode, "SPSF-41");
  assert.equal(history.state.gigaCatalog.videoDetail.code, "SPSF-41");
});

test("recent, tag, series, and favorite origins all create the same detail history", async () => {
  for (const source of ["recent", "tag", "series", "favorite"]) {
    const { history, navigation, shown } = createNavigationHarness();
    await navigation.open("SPSF-3", { source });
    assert.equal(history.entries.length, 2, source);
    assert.equal(history.state.gigaCatalog.videoDetail.code, "SPSF-3", source);
    assert.equal(shown[0][1].source, source);
  }
});

test("a stale async open cannot replace a newer target or reopen after close", async () => {
  const first = deferred();
  const second = deferred();
  const third = deferred();
  const loads = new Map([
    ["SPSF-4", first.promise],
    ["SPSF-5", second.promise],
    ["SPSF-6", third.promise],
  ]);
  const { history, navigation, shown } = createNavigationHarness({
    prepareDetail: (code) => loads.get(code),
  });

  const firstOpen = navigation.open("SPSF-4");
  const secondOpen = navigation.open("SPSF-5");
  second.resolve({ code: "SPSF-5" });
  assert.equal(await secondOpen, true);
  first.resolve({ code: "SPSF-4" });
  assert.equal(await firstOpen, false);
  assert.deepEqual(shown.map(([code]) => code), ["SPSF-5"]);
  assert.equal(history.entries.length, 2);

  history.back();
  const thirdOpen = navigation.open("SPSF-6");
  navigation.requestClose("cancel");
  third.resolve({ code: "SPSF-6" });
  assert.equal(await thirdOpen, false);
  assert.deepEqual(shown.map(([code]) => code), ["SPSF-5"]);
  assert.equal(history.entries.length, 2);
});

test("history returning to active A cancels a pending B before B can display or push", async () => {
  const pendingB = deferred();
  const { history, navigation, shown } = createNavigationHarness({
    prepareDetail: (code) => code === "SPSF-11"
      ? pendingB.promise
      : Promise.resolve({ code }),
  });
  await navigation.open("SPSF-10");
  const openingB = navigation.open("SPSF-11");

  navigation.handlePopState();
  pendingB.resolve({ code: "SPSF-11" });

  assert.equal(await openingB, false);
  assert.deepEqual(shown.map(([code]) => code), ["SPSF-10"]);
  assert.equal(history.entries.length, 2);
  assert.equal(history.state.gigaCatalog.videoDetail.code, "SPSF-10");
});

test("a list action reuses a pending close traversal instead of queuing a second Back", async () => {
  const { history, hidden, navigation } = createNavigationHarness({
    deferTraversal: true,
  });
  await navigation.open("SPSF-12");

  assert.equal(navigation.requestClose("button"), "back");
  assert.equal(navigation.leaveForListAction("actor"), "back");

  assert.equal(history.backCalls, 1);
  assert.equal(history.state.gigaCatalog?.videoDetail, undefined);
  assert.equal(hidden.length, 0);
  assert.equal(history.flushBack(), true);
  assert.equal(hidden.length, 1);
});

test("a forced tag refresh is ignored while an owned Back traversal is pending", async () => {
  let preparations = 0;
  const { history, navigation, shown } = createNavigationHarness({
    deferTraversal: true,
    prepareDetail: async (code) => {
      preparations += 1;
      return { code };
    },
  });
  await navigation.open("SPSF-13");
  navigation.requestClose("button");

  const refreshed = await navigation.open(
    "SPSF-13",
    { source: "tag-refresh" },
    { force: true, historyMode: "none" },
  );
  navigation.requestClose("cancel");

  assert.equal(refreshed, false);
  assert.equal(preparations, 1);
  assert.equal(shown.length, 1);
  assert.equal(history.backCalls, 1);
  assert.equal(history.flushBack(), true);
});

for (const reason of ["actor", "tag"]) {
  test(`${reason} exit keeps its Back traversal guarded after the detail marker is stripped`, async () => {
    let preparations = 0;
    const { history, hidden, navigation, shown } = createNavigationHarness({
      deferTraversal: true,
      prepareDetail: async (code) => {
        preparations += 1;
        return { code };
      },
    });
    await navigation.open("SPSF-14", { source: "search" });

    assert.equal(navigation.leaveForListAction(reason), "back", reason);
    assert.equal(history.state.gigaCatalog?.videoDetail, undefined, reason);
    assert.equal(navigation.requestClose("button"), "back", reason);
    assert.equal(navigation.leaveForListAction(reason), "back", reason);
    const refreshed = await navigation.open(
      "SPSF-14",
      { source: `${reason}-refresh` },
      { force: true, historyMode: "none" },
    );

    assert.equal(refreshed, false, reason);
    assert.equal(preparations, 1, reason);
    assert.equal(shown.length, 1, reason);
    assert.equal(hidden.length, 0, reason);
    assert.equal(history.backCalls, 1, reason);
    assert.equal(history.flushBack(), true, reason);
    assert.equal(hidden.length, 1, reason);
  });
}

test("a failed first load creates no entry and one successful retry creates exactly one", async () => {
  let attempts = 0;
  const { history, navigation } = createNavigationHarness({
    prepareDetail: async (code) => {
      attempts += 1;
      return attempts === 1 ? null : { code };
    },
  });

  assert.equal(await navigation.open("SPSF-7"), false);
  assert.equal(history.entries.length, 1);
  assert.equal(await navigation.open("SPSF-7"), true);
  assert.equal(history.entries.length, 2);
  assert.equal(await navigation.open("SPSF-7"), true);
  assert.equal(history.entries.length, 2);
});

test("startup strips an unowned residual detail without Back or UI mismatch", () => {
  const initialState = {
    analytics: "keep",
    gigaCatalog: {
      preference: "keep-too",
      videoDetail: { code: "SPSF-8", sessionId: "old-session" },
    },
  };
  const { history, navigation, shown } = createNavigationHarness({ initialState });

  assert.equal(history.state.analytics, "keep");
  assert.equal(history.state.gigaCatalog.preference, "keep-too");
  assert.equal(history.state.gigaCatalog.videoDetail, undefined);
  assert.deepEqual(shown, []);
  assert.equal(navigation.requestClose("button"), "direct");
  assert.equal(history.backCalls, 0);
});

test("leaving detail for an actor or tag replaces the owned detail before rendering list state", async () => {
  for (const reason of ["actor", "tag"]) {
    const { history, hidden, navigation, shown } = createNavigationHarness();
    await navigation.open("SPSF-9", { source: "search" });

    navigation.leaveForListAction(reason);

    assert.equal(history.state.gigaCatalog?.videoDetail, undefined, reason);
    assert.equal(hidden.length, 1, reason);
    assert.equal(history.backCalls, 1, reason);
    history.forward();
    await navigation.whenIdle();
    assert.equal(history.state.gigaCatalog?.videoDetail, undefined, reason);
    assert.equal(shown.length, 1, reason);
  }
});

test("focus restoration prevents scrolling and uses the visible fallback when origin is gone", () => {
  assert.equal(typeof application.restoreVideoDetailFocus, "function");
  const calls = [];
  const origin = {
    isConnected: true,
    focus(options) {
      calls.push(["origin", options]);
    },
  };
  const fallback = {
    isConnected: true,
    focus(options) {
      calls.push(["fallback", options]);
    },
  };

  assert.equal(application.restoreVideoDetailFocus(origin, fallback), origin);
  origin.isConnected = false;
  assert.equal(application.restoreVideoDetailFocus(origin, fallback), fallback);
  assert.deepEqual(calls, [
    ["origin", { preventScroll: true }],
    ["fallback", { preventScroll: true }],
  ]);
});
