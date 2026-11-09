import { navigate } from "../router.js";
import { isAdmin } from "../store/auth.js";
import { setView } from "../store/ui.js";
import { batchCreateUsers } from "../api/users.js";
import { toast } from "../components/toast.js";
import { esc } from "../utils/dom.js";

const DEFAULT_PASSWORD = "12345678";
const ROLE_OPTIONS = [
  ["uploader", "上传者"],
  ["viewer", "只读"],
  ["admin", "管理员"]
];

let cleanups = [];

function roleOptions(current) {
  return ROLE_OPTIONS.map(([value, label]) =>
    `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`).join("");
}

function showBatchResult(result) {
  const box = document.getElementById("batchResult");
  if (!box) return;
  const created = result.created || [];
  const skipped = result.skipped || [];
  const invalid = result.invalid || [];
  const lines = [];

  if (created.length) {
    lines.push(`<p class="um-res-line"><span class="um-res-key">新账号</span>
      <span class="um-res-val">${created.map(esc).join("、")}</span></p>`);
  }
  if (skipped.length) {
    lines.push(`<p class="um-res-line"><span class="um-res-key">已存在跳过</span>
      <span class="um-res-val">${skipped.map(esc).join("、")}</span></p>`);
  }
  if (invalid.length) {
    lines.push(`<p class="um-res-line bad"><span class="um-res-key">账号名不合法</span>
      <span class="um-res-val">${invalid.map(x => `${esc(x.name)}（${esc(x.reason)}）`).join("、")}</span></p>`);
  }

  box.innerHTML = `
    <div class="um-res-head">
      <b>成功创建 ${created.length} 个账号</b>
      ${created.length ? `<span class="um-res-pw">初始密码 <code>${esc(result.password || DEFAULT_PASSWORD)}</code></span>` : ""}
    </div>
    ${lines.join("")}`;
  box.hidden = false;
}

/**
 * @param {HTMLElement} container
 */
export function mount(container) {
  if (!isAdmin()) {
    setTimeout(() => {
      toast("仅管理员可以注册账号", true);
      navigate("/", { replace: true });
    }, 0);
    return;
  }
  setView("users-new");

  container.innerHTML = `
  <section class="view" id="viewUserCreate">
    <div class="page-head">
      <div class="page-title">
        <h2>注册用户</h2>
      </div>
    </div>

    <div class="um-card um-create">
      <form id="userForm" autocomplete="off" novalidate>
        <div class="field">
          <label for="uNames">账号列表</label>
          <textarea id="uNames" rows="12" spellcheck="false"
            placeholder="每行一个账号，也可用逗号、分号或空格分隔&#10;zhangsan&#10;lisi&#10;wangwu"></textarea>
        </div>
        <div class="um-create-foot">
          <div class="field um-role-field">
            <label for="uRole">角色</label>
            <select id="uRole">${roleOptions("uploader")}</select>
          </div>
          <button class="btn btn-primary um-submit" id="createUserBtn" type="submit">创建账号</button>
        </div>
        <div class="um-result" id="batchResult" hidden></div>
      </form>
    </div>
  </section>`;

  const form = container.querySelector("#userForm");
  const namesInput = container.querySelector("#uNames");
  const roleSelect = container.querySelector("#uRole");
  const submitBtn = container.querySelector("#createUserBtn");

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const text = namesInput.value.trim();
    if (!text) { toast("请填写至少一个账号", true); namesInput.focus(); return; }

    submitBtn.disabled = true;
    submitBtn.textContent = "创建中…";
    try {
      const result = await batchCreateUsers({ text, role: roleSelect.value });
      showBatchResult(result);
      if (result.created.length) {
        toast(`已创建 ${result.created.length} 个账号，初始密码 ${result.password || DEFAULT_PASSWORD}`);
        namesInput.value = "";
      } else {
        toast("没有新建账号：" + (result.skipped.length ? "填写的账号都已存在" : "请检查输入"), true);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "创建账号";
    }
  });
  namesInput.focus();
}
export function unmount() {
  cleanups.forEach(fn => {
    try { fn(); } catch (e) { /* ignore */ }
  });
  cleanups = [];
}
