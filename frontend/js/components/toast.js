/**
 * 右下角轻提示。
 * @param {string} message
 * @param {boolean} [isError]
 */
export function toast(message, isError = false) {
  const box = document.getElementById("toast");
  if (!box) return;

  const node = document.createElement("div");
  node.className = "tmsg" + (isError ? " err" : "");

  const dot = document.createElement("span");
  dot.className = "tdot";

  const text = document.createElement("span");
  text.textContent = message;

  node.append(dot, text);
  box.appendChild(node);
  setTimeout(() => node.remove(), 3200);
}
