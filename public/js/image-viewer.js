import { fitImage, clampTransform, zoomAtPoint, swipeDirection, createImageViewerNavigation, shouldHandleMouseDoubleClick } from "./image-viewer-state.js";

export function createImageViewer({ isParentOpen }) {
  const dialog = document.createElement("dialog");
  dialog.className = "image-viewer";
  dialog.setAttribute("aria-label", "预览大图");
  dialog.innerHTML = `<div class="image-viewer__toolbar"><button type="button" class="icon-button" data-gallery="close" aria-label="关闭大图">×</button><span class="image-viewer__counter" role="status" aria-live="polite"></span><button type="button" class="icon-button" data-gallery="zoom" aria-label="放大图片">＋</button></div>
    <div class="image-viewer__stage"><img class="image-viewer__image" alt="" draggable="false"><div class="image-viewer__status" role="status"></div><button type="button" class="button image-viewer__retry" data-gallery="retry" hidden>重新加载</button></div>
    <button type="button" class="icon-button image-viewer__previous" data-gallery="previous" aria-label="上一张">‹</button><button type="button" class="icon-button image-viewer__next" data-gallery="next" aria-label="下一张">›</button>
    <p class="image-viewer__hint">左右滑动切换 · 双击或双指放大</p>`;
  document.body.append(dialog);
  const stage = dialog.querySelector(".image-viewer__stage");
  const picture = dialog.querySelector("img");
  const status = dialog.querySelector(".image-viewer__status");
  const retry = dialog.querySelector('[data-gallery="retry"]');
  const counter = dialog.querySelector(".image-viewer__counter");
  const previous = dialog.querySelector('[data-gallery="previous"]');
  const next = dialog.querySelector('[data-gallery="next"]');
  const zoom = dialog.querySelector('[data-gallery="zoom"]');
  const preloads = new Map();
  const pointers = new Map();
  let context = null;
  let transform = { scale: 1, x: 0, y: 0 };
  let gesture = null;
  let lastTap = null;
  let lastTouchTime = -Infinity;
  let timer;
  let frame;
  let request = 0;
  function bounds() {
    const rect = stage.getBoundingClientRect();
    const fit = fitImage(picture.naturalWidth || rect.width, picture.naturalHeight || rect.height, rect.width, rect.height);
    return { width: rect.width, height: rect.height, imageWidth: fit.width, imageHeight: fit.height };
  }
  function paint() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      picture.style.transform = `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})`;
      stage.classList.toggle("is-zoomed", transform.scale > 1.01);
      zoom.textContent = transform.scale > 1.01 ? "−" : "＋";
      zoom.setAttribute("aria-label", transform.scale > 1.01 ? "还原图片大小" : "放大图片");
      zoom.setAttribute("aria-pressed", String(transform.scale > 1.01));
    });
  }
  function localPoint(point) {
    const rect = stage.getBoundingClientRect();
    return { x: point.x - rect.left - rect.width / 2, y: point.y - rect.top - rect.height / 2 };
  }
  function toggleZoom(point = { x: 0, y: 0 }) {
    transform = zoomAtPoint(transform, transform.scale > 1.01 ? 1 : 2.5, point, bounds());
    lastTap = null;
    paint();
  }
  function preloadNeighbors() {
    const urls = context.urls.slice(Math.max(0, context.index - 1), context.index + 2);
    for (const [url, image] of preloads) {
      if (!urls.includes(url)) { image.removeAttribute("src"); preloads.delete(url); }
    }
    // The current image already has a request; only warm its immediate neighbors.
    for (const url of urls) {
      if (url === context.urls[context.index] || preloads.has(url)) continue;
      const image = new Image(); image.decoding = "async"; image.src = url; preloads.set(url, image);
    }
  }
  function renderImage() {
    const token = ++request;
    clearTimeout(timer);
    pointers.clear(); gesture = null; lastTap = null;
    transform = { scale: 1, x: 0, y: 0 }; paint();
    picture.hidden = true;
    retry.hidden = true;
    status.hidden = false;
    status.textContent = "正在加载图片…";
    stage.setAttribute("aria-busy", "true");
    counter.textContent = `${context.index + 1} / ${context.urls.length}`;
    picture.alt = `${context.code} 预览 ${context.index + 1}`;
    previous.disabled = context.index === 0;
    next.disabled = context.index === context.urls.length - 1;
    const failed = () => {
      if (token !== request || !dialog.open) return;
      clearTimeout(timer);
      picture.hidden = true; status.hidden = false;
      status.textContent = "这张图片暂时加载失败"; retry.hidden = false;
      stage.setAttribute("aria-busy", "false");
    };
    picture.onload = () => {
      if (token !== request || !dialog.open) return;
      clearTimeout(timer); picture.hidden = false; status.hidden = true; retry.hidden = true;
      stage.setAttribute("aria-busy", "false");
    };
    picture.onerror = failed;
    timer = setTimeout(failed, 20000);
    picture.src = context.urls[context.index];
    preloadNeighbors();
  }
  function show(value) {
    context = { ...value, index: Math.max(0, Math.min(value.urls.length - 1, value.index || 0)) };
    if (!dialog.open) dialog.showModal();
    renderImage();
    dialog.querySelector('[data-gallery="close"]').focus({ preventScroll: true });
  }
  function hide() {
    ++request; clearTimeout(timer); cancelAnimationFrame(frame);
    pointers.clear(); gesture = null; lastTap = null;
    for (const image of preloads.values()) image.removeAttribute("src");
    preloads.clear(); picture.onload = null; picture.onerror = null; picture.removeAttribute("src");
    if (dialog.open) dialog.close();
    const origin = context?.trigger;
    if (origin?.isConnected && isParentOpen(context.code)) origin.focus({ preventScroll: true });
    context = null;
  }
  const navigation = createImageViewerNavigation({ history: window.history, eventTarget: window, isParentOpen, show, hide });
  function change(delta) {
    if (!context) return;
    const index = context.index + delta;
    if (index < 0 || index >= context.urls.length) return;
    context.index = index; navigation.updateIndex(index); renderImage();
  }
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); navigation.requestClose(); });
  dialog.addEventListener("click", (event) => {
    const action = event.target.closest("[data-gallery]")?.dataset.gallery;
    if (action === "close") navigation.requestClose();
    if (action === "previous") change(-1);
    if (action === "next") change(1);
    if (action === "zoom") toggleZoom();
    if (action === "retry") { picture.removeAttribute("src"); renderImage(); }
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); change(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); change(1); }
    if (event.key === "0") { transform = { scale: 1, x: 0, y: 0 }; paint(); }
  });
  stage.addEventListener("dblclick", (event) => { if (shouldHandleMouseDoubleClick(event, performance.now() - lastTouchTime) && event.target === picture) { event.preventDefault(); toggleZoom(localPoint({ x: event.clientX, y: event.clientY })); } });
  function pinch() {
    const [a, b] = [...pointers.values()];
    return { distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), midpoint: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
  }
  stage.addEventListener("pointerdown", (event) => {
    if (pointers.size >= 2 || event.target.closest("button") || (event.pointerType === "mouse" && event.button !== 0)) return;
    if (event.pointerType === "touch") lastTouchTime = performance.now();
    stage.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 1) gesture = { start: { x: event.clientX, y: event.clientY }, base: { ...transform }, multi: false, time: performance.now(), touch: event.pointerType === "touch" };
    if (pointers.size === 2) gesture = { ...gesture, multi: true, base: { ...transform }, pinch: pinch() };
  });
  stage.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId) || !gesture) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size >= 2 && gesture.pinch) {
      const value = pinch();
      const point = localPoint(gesture.pinch.midpoint);
      const scale = Math.max(1, Math.min(4, gesture.base.scale * value.distance / gesture.pinch.distance));
      const ratio = scale / gesture.base.scale;
      transform = clampTransform({ scale, x: point.x - (point.x - gesture.base.x) * ratio + value.midpoint.x - gesture.pinch.midpoint.x, y: point.y - (point.y - gesture.base.y) * ratio + value.midpoint.y - gesture.pinch.midpoint.y }, bounds());
    } else if (gesture.base.scale > 1.01) {
      transform = clampTransform({ ...gesture.base, x: gesture.base.x + event.clientX - gesture.start.x, y: gesture.base.y + event.clientY - gesture.start.y }, bounds());
    }
    paint();
  });
  function endPointer(event, cancelled) {
    if (!pointers.has(event.pointerId)) return;
    pointers.delete(event.pointerId);
    if (pointers.size) {
      const remaining = [...pointers.values()][0];
      gesture = { start: remaining, base: { ...transform }, multi: true };
      return;
    }
    const value = gesture; gesture = null;
    if (!value || cancelled || value.multi) { lastTap = null; return; }
    const dx = event.clientX - value.start.x, dy = event.clientY - value.start.y;
    const direction = swipeDirection(dx, dy, value.base.scale);
    if (direction) { change(direction); return; }
    if (value.touch && Math.hypot(dx, dy) < 12 && performance.now() - value.time < 300) {
      const point = { x: event.clientX, y: event.clientY }, time = performance.now();
      if (lastTap && time - lastTap.time < 320 && Math.hypot(point.x - lastTap.x, point.y - lastTap.y) < 32) toggleZoom(localPoint(point));
      else lastTap = { ...point, time };
    } else lastTap = null;
  }
  stage.addEventListener("pointerup", (event) => endPointer(event, false));
  stage.addEventListener("pointercancel", (event) => endPointer(event, true));
  window.addEventListener("resize", () => { if (dialog.open) { transform = clampTransform(transform, bounds()); paint(); } });
  return { open: navigation.open, dismiss: navigation.dismiss };
}
