/**
 * 通用 DOM 工具。
 */

/** @param {string} id @param {Document|Element} [root] */
export function byId(id, root = document) {
  return root.getElementById ? root.getElementById(id) : root.querySelector("#" + id);
}

/** @param {string} selector @param {Document|Element} [root] */
export function qs(selector, root = document) {
  return root.querySelector(selector);
}

/** @param {string} selector @param {Document|Element} [root] */
export function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

/** @param {*} value */
export function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export const esc = escapeHtml;

/**
 * @param {Element} target
 * @param {string} type
 * @param {EventListenerOrEventListenerObject} handler
 * @param {boolean|AddEventListenerOptions} [options]
 */
export function on(target, type, handler, options) {
  target.addEventListener(type, handler, options);
  return () => target.removeEventListener(type, handler, options);
}

/**
 * 事件委托：仅在匹配选择器时调用 handler。
 * @param {Element} root
 * @param {string} selector
 * @param {string} type
 * @param {(event: Event, match: Element) => void} handler
 */
export function delegate(root, selector, type, handler) {
  const listener = event => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const match = target.closest(selector);
    if (match && root.contains(match)) handler(event, match);
  };
  root.addEventListener(type, listener);
  return () => root.removeEventListener(type, listener);
}

/** @param {string} id */
export function setText(id, text) {
  const node = document.getElementById(id);
  if (node) node.textContent = text;
}

/** @param {ScrollBehavior} [behavior] */
export function scrollToTop(behavior = "auto") {
  window.scrollTo({ top: 0, behavior });
}

/** @param {string} id */
export function focusById(id) {
  const node = document.getElementById(id);
  if (node) {
    try { node.focus(); } catch (e) { /* ignore */ }
  }
}
