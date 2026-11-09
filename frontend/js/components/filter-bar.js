const SEARCH_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;

/**
 * @param {HTMLElement} container
 * @param {{
 *   onChange: (filters: import('../types/models.js').Filters) => void,
 *   onReset: () => void
 * }} options
 */
export function createFilterBar(container, options) {
  const { onChange, onReset } = options;

  container.innerHTML = `
  <div class="filters" role="search" aria-label="包筛选">
    <div class="filter-row">
      <div class="search">
        ${SEARCH_ICON}
        <input type="text" id="fName" placeholder="包名 / 文件名搜索" aria-label="按包名搜索">
      </div>
      <select id="fType" aria-label="按包类型筛选">
        <option value="">全部类型</option>
        <option value="wheel">Wheel（.whl）</option>
        <option value="sdist">SDist（.tar.gz / .zip）</option>
      </select>
      <div class="filter-range" role="group" aria-label="上传时间范围" title="上传时间范围">
        <input type="date" id="fFrom" aria-label="上传开始日期">
        <span class="range-sep" aria-hidden="true">至</span>
        <input type="date" id="fTo" aria-label="上传结束日期">
      </div>
      <input type="text" id="fVersion" class="filter-ver" placeholder="版本号，如 1.2" aria-label="按版本号筛选">
      <label class="switch" title="同时显示回收站中的包">
        <input type="checkbox" id="showDel">
        <span class="switch-track"><span class="switch-thumb"></span></span>
        <span>含已删除</span>
      </label>
      <button class="btn" id="resetFilters" type="button">重置筛选</button>
    </div>
  </div>`;

  const nameInput = container.querySelector("#fName");
  const typeInput = container.querySelector("#fType");
  const fromInput = container.querySelector("#fFrom");
  const toInput = container.querySelector("#fTo");
  const versionInput = container.querySelector("#fVersion");
  const showDelInput = container.querySelector("#showDel");
  const resetButton = container.querySelector("#resetFilters");

  let timer = null;

  const readFilters = () => ({
    name: nameInput.value.trim(),
    type: typeInput.value,
    from: fromInput.value,
    to: toInput.value,
    version: versionInput.value.trim(),
    showDel: !!showDelInput.checked
  });

  const apply = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    onChange(readFilters());
  };

  const debounce = () => {
    clearTimeout(timer);
    timer = setTimeout(apply, 180);
  };

  nameInput.addEventListener("input", debounce);
  versionInput.addEventListener("input", debounce);
  typeInput.addEventListener("change", apply);
  fromInput.addEventListener("change", apply);
  toInput.addEventListener("change", apply);
  showDelInput.addEventListener("change", apply);
  resetButton.addEventListener("click", onReset);

  const setFilters = filters => {
    nameInput.value = filters.name == null ? "" : filters.name;
    typeInput.value = filters.type == null ? "" : filters.type;
    fromInput.value = filters.from == null ? "" : filters.from;
    toInput.value = filters.to == null ? "" : filters.to;
    versionInput.value = filters.version == null ? "" : filters.version;
    showDelInput.checked = !!filters.showDel;
  };

  const clearInputs = () => {
    ["fName", "fType", "fFrom", "fTo", "fVersion"].forEach(id => {
      const node = container.querySelector("#" + id);
      if (node) node.value = "";
    });
    showDelInput.checked = false;
  };

  return {
    readFilters,
    setFilters,
    clearInputs,
    destroy() {
      if (timer) clearTimeout(timer);
    }
  };
}
