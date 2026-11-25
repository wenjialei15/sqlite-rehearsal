"use strict";

const byId = (id) => document.getElementById(id);
const buttons = [...document.querySelectorAll("button[data-sample]")];
const statusNames = { passed: "通过", failed: "失败", error: "执行出错" };
const changeNames = { added: "新增", removed: "移除", changed: "修改" };
const checkNames = new Map([
  ["SQLite integrity", "SQLite 完整性"],
  ["Foreign keys", "外键完整性"],
  ["Preserve readings", "历史读数逐行一致"],
  ["Devices match legacy identities and timestamps", "设备身份及首次和最后上报时间正确"],
  ["Legacy thresholds copied to both modes", "旧阈值已复制到实机和模拟模式"],
]);
let request = 0;

function element(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}

function showMessage(text, error = false) {
  const message = byId("message");
  message.hidden = !text;
  message.textContent = text;
  message.classList.toggle("error", error);
}

function validReport(report) {
  return report?.format_version === 1 && Object.hasOwn(statusNames, report.status)
    && Array.isArray(report.checks) && report.checks.every((check) => check
      && typeof check.name === "string" && ["passed", "failed"].includes(check.status))
    && (report.schema === null || (Number.isInteger(report.schema?.before_version)
      && Number.isInteger(report.schema.after_version) && Array.isArray(report.schema.changes)
      && report.schema.changes.every((item) => item && typeof item.type === "string"
        && typeof item.name === "string" && Object.hasOwn(changeNames, item.change))));
}

function renderCheck(check, sample) {
  const row = element("li", "");
  const line = element("div", "", "check-line");
  const state = element("span", statusNames[check.status], "check-state");
  state.dataset.kind = check.status;
  line.append(state, element("span", (sample && checkNames.get(check.name)) || check.name));
  row.append(line);

  if (Number.isInteger(check.before_count) && Number.isInteger(check.after_count)) {
    row.append(element("p", `记录数 ${check.before_count} → ${check.after_count}`));
    for (const [key, label] of [["removed", "丢失"], ["added", "新增"], ["changed", "改动"]]) {
      const diff = check[key];
      if (!Number.isInteger(diff?.count) || diff.count <= 0) continue;
      const keys = Array.isArray(diff.keys) ? ` · 主键样本 ${JSON.stringify(diff.keys)}` : "";
      row.append(element("p", `${label} ${diff.count} 条${keys}`, "diff"));
    }
  } else if (Object.hasOwn(check, "expected")) {
    row.append(element("p", `预期 ${JSON.stringify(check.expected)} · 实际 ${JSON.stringify(check.actual)}`));
  } else if (check.status === "failed" && typeof check.detail === "string") {
    row.append(element("p", check.detail, "diff"));
  } else if (Array.isArray(check.violations) && check.violations.length) {
    row.append(element("p", `违规样本 ${JSON.stringify(check.violations)}`, "diff"));
  }
  return row;
}

function renderReport(report, title, sample = false) {
  if (!validReport(report)) throw new Error("请选择 SQLite Rehearsal 生成的 JSON 报告（格式版本 1）。");
  byId("report-title").textContent = title;
  const status = byId("report-status");
  status.textContent = statusNames[report.status];
  status.dataset.kind = report.status;
  const schema = report.schema;
  byId("versions").textContent = schema ? `${schema.before_version} → ${schema.after_version}` : "—";
  byId("schema-count").textContent = schema ? schema.changes.length : "—";
  const passed = report.checks.filter((check) => check.status === "passed").length;
  byId("passed").textContent = report.checks.length ? `${passed} / ${report.checks.length}` : "—";

  const checks = byId("checks");
  checks.replaceChildren();
  for (const check of report.checks) checks.append(renderCheck(check, sample));
  if (!report.checks.length) {
    checks.append(element("li", typeof report.error === "string" ? report.error : "暂无核验结果。"));
  }

  const changes = byId("schema");
  changes.replaceChildren();
  for (const change of schema?.changes ?? []) {
    const entry = element("li", "");
    entry.append(element("span", changeNames[change.change], "schema-type"),
      element("code", `${change.type} ${change.name}`));
    changes.append(entry);
  }
  if (!changes.childElementCount) changes.append(element("li", schema ? "未发现结构变更。" : "暂无结构结果。"));
  byId("report").hidden = false;
  showMessage("");
}

function selectScenario(sample) {
  for (const button of buttons) button.setAttribute("aria-pressed", String(button.dataset.sample === sample));
}

async function loadSample(sample) {
  const current = ++request;
  selectScenario(sample);
  showMessage("正在读取示例报告…");
  byId("report").hidden = true;
  try {
    const response = await fetch(`sample-${sample}.json`);
    if (!response.ok) throw new Error();
    const report = await response.json();
    if (current === request) renderReport(report, "AgriPulse v0.1.0 升级演练", true);
  } catch {
    if (current === request) showMessage("无法加载示例。请通过 GitHub Pages 或本地 HTTP 服务打开页面。", true);
  }
}

for (const button of buttons) button.addEventListener("click", () => loadSample(button.dataset.sample));
byId("report-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const current = ++request;
  selectScenario(null);
  byId("report").hidden = true;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error("报告超过 2 MB，请使用本地文本编辑器打开。");
    const report = JSON.parse(await file.text());
    if (current === request) renderReport(report, file.name);
  } catch (error) {
    if (current === request) showMessage(error instanceof SyntaxError ? "文件内容不是有效的 JSON。" : error.message, true);
  }
});

loadSample("pass");
