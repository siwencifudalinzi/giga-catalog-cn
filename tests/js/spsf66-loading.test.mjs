import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";
import * as app from "../../public/js/app.js";

test("playback warming is scoped, respects data saving, and is deduplicated", () => {
  assert.equal(typeof app.warmSpsf66Playback, "function");
  const hints = [];
  const doc = {
    head: { querySelector: () => hints[0], append: link => hints.push(link) },
    createElement: () => ({ dataset: {} }),
  };
  app.warmSpsf66Playback("SPSF-65", doc);
  app.warmSpsf66Playback("SPSF-66", doc, { saveData: true });
  assert.equal(hints.length, 0);
  app.warmSpsf66Playback("SPSF-66", doc);
  app.warmSpsf66Playback("SPSF-66", doc);
  assert.deepEqual(hints.map(({ rel, href }) => ({ rel, href })), [
    { rel: "preconnect", href: "https://streamtape.com" },
    { rel: "prefetch", href: "watch/spsf-66.html" },
  ]);
});

function playback() {
  const html = readFileSync(new URL("../../public/watch/spsf-66.html", import.meta.url), "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/u)?.[1];
  assert.ok(script, "playback controls must initialize without an extra script request");
  const player = Object.assign(new EventTarget(), { src: "https://streamtape.com/e/XByjZOkOjliDemd" });
  const retry = new EventTarget();
  const status = { textContent: "" };
  const elements = { "#player": player, "#retry": retry, "#connection-status": status };
  const timers = new Map();
  let nextId = 0;
  vm.runInNewContext(script, {
    document: { querySelector: selector => elements[selector] },
    setTimeout: (callback, delay) => { timers.set(++nextId, { callback, delay }); return nextId; },
    clearTimeout: id => timers.delete(id),
  });
  return { player, retry, status, timers, flush() {
    for (const [id, timer] of [...timers]) { timers.delete(id); timer.callback(); }
  } };
}

test("a slow source shows feedback without restarting or declaring playback ready", () => {
  const page = playback();
  const source = page.player.src;
  assert.match(page.status.textContent, /正在连接/u);
  assert.equal([...page.timers.values()][0].delay, 15000);
  page.flush();
  assert.match(page.status.textContent, /较慢/u);
  assert.equal(page.player.src, source);
  page.player.dispatchEvent(new Event("load"));
  assert.match(page.status.textContent, /点按/u);
  assert.doesNotMatch(page.status.textContent, /已就绪|已成功|正在播放/u);
});

test("retry prevents parent navigation, resets the timer, and keeps the stable source", () => {
  const page = playback();
  let requests = 0;
  let source = page.player.src;
  Object.defineProperty(page.player, "src", { get: () => source, set: value => { source = value; requests++; } });
  page.flush();
  const click = Object.assign(new Event("click", { cancelable: true }), { button: 0 });
  page.retry.dispatchEvent(click);
  assert.equal(click.defaultPrevented, true);
  assert.equal(requests, 1);
  assert.equal(source, "https://streamtape.com/e/XByjZOkOjliDemd");
  assert.match(page.status.textContent, /正在连接/u);
  assert.equal(page.timers.size, 1);
  page.retry.dispatchEvent(Object.assign(new Event("click", { cancelable: true }), { button: 0 }));
  assert.equal(page.timers.size, 1);
  page.player.dispatchEvent(new Event("load"));
  assert.equal(page.timers.size, 0);
  const text = page.status.textContent;
  page.flush();
  assert.equal(page.status.textContent, text);
});

test("modified retry clicks retain normal link behavior", () => {
  const page = playback();
  for (const modifier of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
    const click = Object.assign(new Event("click", { cancelable: true }), { button: 0 }, modifier);
    page.retry.dispatchEvent(click);
    assert.equal(click.defaultPrevented, false);
  }
});
