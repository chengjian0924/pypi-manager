import { cycleTheme, themeLabel, getThemeMode, uiStore } from "../store/ui.js";
import { toast } from "./toast.js";

/** @param {HTMLElement|null} button */
export function syncThemeButton(button) {
  if (!button) return;
  const mode = getThemeMode();
  button.title = "主题：" + themeLabel(mode) + "（点击切换）";
  button.setAttribute("aria-label", "当前主题：" + themeLabel(mode));
}

/** @param {HTMLElement|null} button */
export function initThemeToggle(button) {
  if (!button) return;
  syncThemeButton(button);
  uiStore.subscribe(() => syncThemeButton(button));
  button.addEventListener("click", () => {
    const mode = cycleTheme();
    syncThemeButton(button);
    toast("主题：" + themeLabel(mode));
  });
}
