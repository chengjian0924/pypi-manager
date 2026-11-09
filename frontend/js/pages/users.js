import { navigate } from "../router.js";
import { isAdmin } from "../store/auth.js";
import { setView } from "../store/ui.js";
import { deleteUser, listUsers, saveUser } from "../api/users.js";
import { toast } from "../components/toast.js";
import { esc, delegate, scrollToTop } from "../utils/dom.js";
import { formatUploadedAt } from "../utils/format.js";

const ROLE_OPTIONS = [
  ["admin", "管理员"],
  ["uploader", "上传者"],
  ["viewer", "只读"]
];
const PAGE_SIZES = [10, 20, 50];
const SEARCH_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;

/** @type {Array<{name: string, role: string, created: string, pwd_default: number}>} */
let rows = [];
let me = "";
let defaultPassword = "12345678";
let cleanups = [];

/* 列表状态与包列表保持一致：页码 + 每页条数 + 筛选条件 */
let page = 1;
let pageSize = PAGE_SIZES[0];
let keyword = "";
let roleFilter = "";
let pwdOnly = false;
let searchTimer = null;

/** @type {Record<string, HTMLElement>} */
const el = {};

function roleOptions(current) {
  return ROLE_OPTIONS.map(([value, label]) =>
    `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`).join("");
}

function roleLabel(value) {
  return (ROLE_OPTIONS.find(r => r[0] === value) || [value, value])[1];
}

function hasFilters() {
  return !!(keyword || roleFilter || pwdOnly);
}

function filteredRows() {
  const kw = keyword.trim().toLowerCase();
  return rows.filter(user => {
    if (kw && user.name.toLowerCase().indexOf(kw) < 0) return false;
    if (roleFilter && user.role !== roleFilter) return false;
    if (pwdOnly && !user.pwd_default) return false;
    return true;
  });
}

function renderRows(shown) {
  if (!el.body) return;
  if (!shown.length) {
    const message = rows.length ? "没有符合当前筛选条件的账号" : "暂无账号";
    el.body.innerHTML = `<tr><td colspan="5" class="um-empty">${message}</td></tr>`;
    return;
  }

  el.body.innerHTML = shown.map(user => {
    const isMe = user.name === me;
    const pwdTag = user.pwd_default
      ? '<span class="tag warn">初始密码</span>'
      : '<span class="tag ok">已修改</span>';
    return `<tr data-name="${esc(user.name)}">
      <td data-label="账号">
        <span class="um-name">${esc(user.name)}</span>${isMe ? '<span class="tag um-me">我</span>' : ""}
      </td>
      <td data-label="角色">
        <select class="um-role" data-role-for="${esc(user.name)}" aria-label="角色">
          ${roleOptions(user.role)}
        </select>
      </td>
      <td data-label="密码">${pwdTag}</td>
      <td class="um-time" data-label="创建时间">${esc(formatUploadedAt(user.created))}</td>
      <td data-label="操作">
        <div class="um-ops">
          <button class="btn btn-sm" type="button" data-action="reset-pass"
                  data-name="${esc(user.name)}"${user.pwd_default ? ' disabled title="已经是初始密码"' : ""}>重置密码</button>
          <button class="btn btn-sm btn-danger" type="button" data-action="del-user"
                  data-name="${esc(user.name)}"${isMe ? ' disabled title="不能删除当前登录的账号"' : ""}>删除</button>
        </div>
      </td>
    </tr>`;
  }).join("");
}

function renderPager(list, totalPages) {
  if (!el.pager) return;
  el.pager.hidden = list.length === 0;
  if (!list.length) return;
  el.pageInfo.textContent = "第 " + page + " / " + totalPages + " 页";
  el.prev.disabled = page <= 1;
  el.next.disabled = page >= totalPages;
  el.filterCount.innerHTML = `<b>${list.length}</b> 个账号` +
    (hasFilters() ? ` · 共 <b>${rows.length}</b> 个 · 已应用筛选` : "");
}

function render() {
  const list = filteredRows();
  const totalPages = Math.max(1, Math.ceil(list.length / pageSize));
  if (page > totalPages) page = totalPages;
  const start = (page - 1) * pageSize;

  if (el.count) {
    const admins = rows.filter(u => u.role === "admin").length;
    const onDefault = rows.filter(u => u.pwd_default).length;
    el.count.textContent = hasFilters()
      ? `筛选出 ${list.length} / ${rows.length} 个账号`
      : `共 ${rows.length} 个账号 · 管理员 ${admins} 个 · 初始密码 ${onDefault} 个`;
  }

  renderRows(list.slice(start, start + pageSize));
  renderPager(list, totalPages);
}

async function load() {
  if (el.body) el.body.innerHTML = '<tr><td colspan="5" class="um-empty">加载中…</td></tr>';
  try {
    const data = await listUsers();
    rows = data.items;
    me = data.me;
    defaultPassword = data.defaultPassword || defaultPassword;
    render();
  } catch (error) {
    if (error.status === 401) return;
    if (el.body) {
      el.body.innerHTML = `<tr><td colspan="5" class="um-empty">${esc(error.message)}</td></tr>`;
    }
    toast(error.message, true);
  }
}

async function removeUser(name) {
  if (!confirm("确认删除账号 " + name + " ？\n该账号会立即无法登录，此操作不可恢复。")) return;
  try {
    await deleteUser(name);
    toast("已删除账号 " + name);
    await load();
  } catch (error) {
    toast(error.message, true);
  }
}

async function resetPassword(name) {
  if (!confirm(`把账号 ${name} 的密码重置为初始密码 ${defaultPassword} ？\n重置后请通知本人登录并自行修改。`)) return;
  try {
    await saveUser({ name, reset_password: true });
    toast(`${name} 的密码已重置为 ${defaultPassword}`);
    await load();
  } catch (error) {
    toast(error.message, true);
  }
}

async function changeRole(select) {
  const name = select.dataset.roleFor;
  const user = rows.find(u => u.name === name);
  if (!user || select.value === user.role) return;
  const previous = user.role;
  select.disabled = true;
  try {
    await saveUser({ name, role: select.value });
    toast("已把 " + name + " 的角色改为" + roleLabel(select.value));
    await load();
  } catch (error) {
    select.value = previous;
    toast(error.message, true);
  } finally {
    select.disabled = false;
  }
}

function resetFilters() {
  keyword = "";
  roleFilter = "";
  pwdOnly = false;
  page = 1;
  if (el.search) el.search.value = "";
  if (el.roleFilter) el.roleFilter.value = "";
  if (el.pwdOnly) el.pwdOnly.checked = false;
  render();
}

/**
 * @param {HTMLElement} container
 */
export function mount(container) {
  if (!isAdmin()) {
    // 路由层已经拦过非管理员，这里只是兜底；延后执行避免在视图切换动画里再次跳转
    setTimeout(() => {
      toast("仅管理员可以管理账号", true);
      navigate("/", { replace: true });
    }, 0);
    return;
  }
  setView("users");

  container.innerHTML = `
  <section class="view" id="viewUsers">
    <div class="page-head">
      <div class="page-title">
        <h2>用户管理</h2>
      </div>
    </div>

    <div class="filters um-filters" role="search" aria-label="账号筛选">
      <div class="filter-row um-filter-row">
        <div class="search">
          ${SEARCH_ICON}
          <input type="text" id="umSearch" placeholder="账号名搜索" aria-label="按账号名搜索">
        </div>
        <select id="umRole" aria-label="按角色筛选">
          <option value="">全部角色</option>
          ${ROLE_OPTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
        </select>
        <label class="switch" title="只显示仍在使用初始密码的账号">
          <input type="checkbox" id="umPwdOnly">
          <span class="switch-track"><span class="switch-thumb"></span></span>
          <span>仅初始密码</span>
        </label>
        <button class="btn" id="umReset" type="button">重置筛选</button>
        <button class="btn" id="reloadUsers" type="button">刷新</button>
      </div>
    </div>

    <div class="um-card">
      <div class="um-head">
        <h3>账号列表</h3>
        <span class="um-count" id="umCount">加载中…</span>
      </div>
      <div class="um-scroll">
        <table class="um-table">
          <thead>
            <tr>
              <th>账号</th>
              <th>角色</th>
              <th>密码</th>
              <th class="um-time">创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody id="umBody"><tr><td colspan="5" class="um-empty">加载中…</td></tr></tbody>
        </table>
      </div>
    </div>

    <nav class="pager" id="umPager" aria-label="分页" hidden>
      <div class="pager-ctl">
        <button class="btn btn-sm" id="umPrev" type="button">上一页</button>
        <span class="pager-info" id="umPageInfo">第 1 / 1 页</span>
        <button class="btn btn-sm" id="umNext" type="button">下一页</button>
        <label class="pager-size">每页
          <select id="umPageSize">
            ${PAGE_SIZES.map(size => `<option value="${size}">${size}</option>`).join("")}
          </select> 条
        </label>
      </div>
      <div class="pager-count" id="umFilterCount">–</div>
    </nav>
  </section>`;

  el.body = container.querySelector("#umBody");
  el.count = container.querySelector("#umCount");
  el.search = container.querySelector("#umSearch");
  el.roleFilter = container.querySelector("#umRole");
  el.pwdOnly = container.querySelector("#umPwdOnly");
  el.pager = container.querySelector("#umPager");
  el.pageInfo = container.querySelector("#umPageInfo");
  el.filterCount = container.querySelector("#umFilterCount");
  el.prev = container.querySelector("#umPrev");
  el.next = container.querySelector("#umNext");
  el.pageSize = container.querySelector("#umPageSize");
  el.pageSize.value = String(pageSize);

  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      keyword = el.search.value.trim();
      page = 1;
      render();
    }, 160);
  });
  el.roleFilter.addEventListener("change", () => {
    roleFilter = el.roleFilter.value;
    page = 1;
    render();
  });
  el.pwdOnly.addEventListener("change", () => {
    pwdOnly = el.pwdOnly.checked;
    page = 1;
    render();
  });
  container.querySelector("#umReset").addEventListener("click", () => {
    resetFilters();
    toast("已重置筛选条件");
  });
  container.querySelector("#reloadUsers").addEventListener("click", () => {
    load().then(() => toast("已刷新"));
  });

  el.prev.addEventListener("click", () => {
    if (page <= 1) return;
    page -= 1;
    render();
    scrollToTop("smooth");
  });
  el.next.addEventListener("click", () => {
    page += 1;
    render();
    scrollToTop("smooth");
  });
  el.pageSize.addEventListener("change", () => {
    pageSize = Number(el.pageSize.value) || PAGE_SIZES[0];
    page = 1;
    render();
    scrollToTop("smooth");
  });

  cleanups.push(delegate(el.body, '[data-action]', "click", (event, match) => {
    const action = match.dataset.action;
    const name = match.dataset.name || "";
    if (action === "reset-pass") resetPassword(name);
    else if (action === "del-user") removeUser(name);
  }));
  cleanups.push(delegate(el.body, "select[data-role-for]", "change", (event, match) => {
    changeRole(match);
  }));

  load();
}

export function unmount() {
  cleanups.forEach(fn => {
    try { fn(); } catch (e) { /* ignore */ }
  });
  cleanups = [];
  rows = [];
  me = "";
  keyword = "";
  roleFilter = "";
  pwdOnly = false;
  page = 1;
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = null;
  Object.keys(el).forEach(key => { delete el[key]; });
}
