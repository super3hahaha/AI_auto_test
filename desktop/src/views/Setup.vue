<script setup lang="ts">
import { ref, onMounted } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { api, type ClaudeCliStatus, type UpdateInfo } from "../api";
import { store } from "../store";

const emit = defineEmits<{ configured: [] }>();
const root = ref("");
const python = ref("python3");
const err = ref("");
const saving = ref(false);

// ── headless 调 claude 用的模型（「脚本自愈」+ 收尾「问题登记」共用；存 app_config.json 的
// claude_model；""=跟随 claude CLI 自身默认）──
const MODEL_OPTIONS = [
  { value: "claude-sonnet-5", label: "Sonnet 5（推荐 · 默认）" },
  { value: "claude-opus-5", label: "Opus 5（更强，更慢更贵）" },
  { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5（更快更省）" },
  { value: "", label: "跟随 CLI 默认设置" },
];
const model = ref("claude-sonnet-5");
const modelSaving = ref(false);
const modelSaved = ref(false);
let modelSavedTimer: ReturnType<typeof setTimeout> | undefined;

async function saveModel() {
  if (!root.value) return; // 项目根还没配好时先不落盘，避免 set_app_config 校验失败
  modelSaving.value = true;
  try {
    const c = await api.setAppConfig(root.value.trim(), python.value.trim(), model.value);
    store.cfg = c;
    modelSaved.value = true;
    clearTimeout(modelSavedTimer);
    modelSavedTimer = setTimeout(() => (modelSaved.value = false), 2000);
  } catch (e: any) {
    err.value = String(e);
  } finally {
    modelSaving.value = false;
  }
}

// ── app 版本号 + 检测更新（读本仓库 GitHub Releases 最新 tag，见 src-tauri/src/updater.rs）──
const appVersion = ref("");
getVersion().then((v) => (appVersion.value = v)).catch(() => {});

type UpdateState = "idle" | "checking" | "latest" | "available" | "downloading" | "installing" | "error";
const updateState = ref<UpdateState>("idle");
const updateInfo = ref<UpdateInfo | null>(null);
const updateErr = ref("");
const downloadProgress = ref({ downloaded: 0, total: 0 });
const showUpdateModal = ref(false);

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}
const downloadPercent = () => {
  const { downloaded, total } = downloadProgress.value;
  return total ? Math.round((downloaded / total) * 100) : 0;
};

async function checkUpdate() {
  updateState.value = "checking";
  updateErr.value = "";
  updateInfo.value = null;
  try {
    const info = await api.checkUpdate();
    if (info) {
      updateInfo.value = info;
      updateState.value = "available";
      showUpdateModal.value = true;
    } else {
      updateState.value = "latest";
    }
  } catch (e: any) {
    updateErr.value = String(e);
    updateState.value = "error";
  }
}

async function startDownload() {
  if (!updateInfo.value) return;
  updateState.value = "downloading";
  downloadProgress.value = { downloaded: 0, total: updateInfo.value.asset_size };
  try {
    const savePath = await api.downloadUpdate(
      updateInfo.value.asset_url,
      updateInfo.value.asset_name,
      (p) => (downloadProgress.value = p)
    );
    updateState.value = "installing";
    await api.applyUpdate(savePath); // 成功后当前进程会退出重启，这行往后不会执行到
  } catch (e: any) {
    updateErr.value = String(e);
    updateState.value = "error";
  }
}

// ── Claude CLI 状态（「脚本自愈」功能依赖它已装 + 已登录）──
const cli = ref<ClaudeCliStatus | null>(null);
const cliLoading = ref(false);

async function refreshCli() {
  cliLoading.value = true;
  try {
    cli.value = await api.checkClaudeCli();
  } catch {
    cli.value = null;
  } finally {
    cliLoading.value = false;
  }
}

onMounted(() => {
  if (store.cfg) {
    root.value = store.cfg.project_root;
    python.value = store.cfg.python || "python3";
    model.value = store.cfg.claude_model || "claude-sonnet-5";
  }
  refreshCli();
});

async function save() {
  err.value = "";
  saving.value = true;
  try {
    const c = await api.setAppConfig(root.value.trim(), python.value.trim());
    store.cfg = c;
    if (c.configured) emit("configured");
  } catch (e: any) {
    err.value = String(e);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="setup">
    <h2>设置</h2>
    <p class="muted">
      指向你的 AI_auto_test 项目根目录（含 <span class="mono">config/target.json</span> 与
      <span class="mono">tools/adbkit.py</span> 的仓库）。app 只读账本、代跑现有 python 脚本，不改框架。
    </p>

    <div class="field">
      <label>项目根目录</label>
      <input v-model="root" placeholder="/Users/you/Projects/AI_auto_test" spellcheck="false" />
      <span class="hint muted" v-if="store.cfg?.project_root && !root">
        自动探测到：{{ store.cfg.project_root }}
      </span>
    </div>

    <div class="field">
      <label>python 解释器</label>
      <input v-model="python" placeholder="python3" spellcheck="false" style="max-width: 240px" />
      <span class="hint muted">app 用它代跑 tools/*.py（和你手敲命令一致）</span>
    </div>

    <div v-if="err" class="err">{{ err }}</div>

    <div class="actions">
      <button class="primary" :disabled="saving || !root" @click="save">
        {{ saving ? "保存中…" : "保存并进入" }}
      </button>
      <span
        v-if="store.cfg?.configured"
        class="pill pill-success"
        style="align-self: center"
        >当前已配置</span
      >
    </div>

    <!-- ── Claude CLI 状态 ── -->
    <div class="cli-block">
      <h3>Claude CLI</h3>
      <p class="muted cli-sub">
        App 通过本机 Claude CLI 调用模型（用例失败时「脚本自愈」、执行收尾「问题登记」都由 claude 接管）。
        这里展示当前 CLI 的安装与登录状态，以及这两处 headless 调用用哪个模型。
      </p>

      <div class="field model-field">
        <label>headless 调用模型</label>
        <div class="model-row">
          <select v-model="model" @change="saveModel">
            <option v-for="o in MODEL_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
          <span v-if="modelSaving" class="muted sm">保存中…</span>
          <span v-else-if="modelSaved" class="muted sm">已保存</span>
        </div>
        <span class="hint muted">用于「脚本自愈」诊断改脚本 + 收尾「问题登记」写 issues.csv；不影响你在终端/编辑器里手动用的 claude 会话。</span>
      </div>

      <div v-if="cliLoading && !cli" class="cli-banner neutral">
        <span class="cli-icon">⏳</span>
        <div class="cli-txt"><div class="cli-title">检测中…</div></div>
      </div>

      <template v-else-if="cli">
        <!-- 已登录 -->
        <div v-if="cli.logged_in" class="cli-banner ok">
          <span class="cli-icon">✅</span>
          <div class="cli-txt">
            <div class="cli-title">
              已登录
              <span v-if="cli.subscription" class="pill pill-accent sub-badge">{{ cli.subscription }}</span>
            </div>
            <div class="cli-detail muted">
              <template v-if="cli.detail_parsed">
                {{ cli.display_name || cli.email }}<span v-if="cli.display_name && cli.email"> · {{ cli.email }}</span>
                <span v-if="cli.org_name"> · {{ cli.org_name }}</span>
              </template>
              <template v-else>账号信息无法解析（CLI 仍可正常使用）</template>
            </div>
          </div>
        </div>

        <!-- 已装未登录 -->
        <div v-else-if="cli.installed" class="cli-banner warn">
          <span class="cli-icon">⚠️</span>
          <div class="cli-txt">
            <div class="cli-title">未登录</div>
            <div class="cli-detail muted">
              终端执行 <span class="mono">claude</span> 完成登录后，回来点「刷新」。「脚本自愈」需要已登录才能接管。
            </div>
          </div>
        </div>

        <!-- 未安装 -->
        <div v-else class="cli-banner bad">
          <span class="cli-icon">❌</span>
          <div class="cli-txt">
            <div class="cli-title">未检测到 Claude CLI</div>
            <div class="cli-detail muted">
              本机 PATH 与常见安装位置都没找到 <span class="mono">claude</span>。装好后点「刷新」。
            </div>
          </div>
        </div>

        <div class="cli-meta">
          <span class="muted">CLI</span>
          <span class="mono cli-path">{{ cli.path || "（未找到）" }}</span>
          <span v-if="cli.version" class="pill pill-muted sm">v{{ cli.version }}</span>
        </div>
      </template>

      <div class="cli-actions">
        <button class="sm" :disabled="cliLoading" @click="refreshCli">
          {{ cliLoading ? "检测中…" : "刷新" }}
        </button>
        <a class="muted doc-link" href="https://docs.claude.com/en/docs/claude-code/overview" target="_blank" rel="noreferrer">
          Claude Code 文档 ↗
        </a>
      </div>
    </div>

    <!-- ── 关于 / 检测更新 ── -->
    <div class="about-block">
      <h3>版本</h3>
      <div class="version-row">
        <div class="version-left">
          <span class="muted">当前版本</span>
          <span v-if="appVersion" class="pill pill-accent">v{{ appVersion }}</span>
        </div>
        <div class="version-right">
          <button
            v-if="updateState === 'idle' || updateState === 'latest' || updateState === 'error'"
            class="sm"
            @click="checkUpdate"
          >
            {{ updateState === "error" ? "重试" : "检测更新" }}
          </button>
          <span v-if="updateState === 'latest'" class="pill pill-success">已是最新版本</span>
          <span v-if="updateState === 'checking'" class="muted sm">检查中…</span>

          <template v-if="updateState === 'available' && updateInfo">
            <span class="pill pill-warning">v{{ updateInfo.version }} 可更新</span>
            <button class="primary sm" @click="showUpdateModal = true">查看更新</button>
          </template>

          <div v-if="updateState === 'downloading'" class="dl-progress">
            <div class="dl-bar"><div class="dl-fill" :style="{ width: downloadPercent() + '%' }"></div></div>
            <span class="muted sm dl-text">
              {{ downloadPercent() }}%
              <template v-if="downloadProgress.total">
                （{{ formatBytes(downloadProgress.downloaded) }} / {{ formatBytes(downloadProgress.total) }}）
              </template>
            </span>
          </div>

          <span v-if="updateState === 'installing'" class="muted sm">正在安装并重启…</span>
        </div>
      </div>
      <div v-if="updateState === 'error' && updateErr" class="err" style="margin-top: 10px">{{ updateErr }}</div>
    </div>

    <!-- ── 更新弹窗 ── -->
    <div v-if="showUpdateModal && updateInfo" class="modal-mask" @click.self="showUpdateModal = false">
      <div class="modal-box card">
        <div class="modal-header">
          <span class="modal-title">发现新版本 v{{ updateInfo.version }}</span>
          <button class="sm" @click="showUpdateModal = false">✕</button>
        </div>
        <div class="modal-body">
          <pre v-if="updateInfo.body" class="release-notes">{{ updateInfo.body }}</pre>
          <p v-else class="muted">暂无更新说明</p>
        </div>
        <div class="modal-footer">
          <button class="sm" @click="showUpdateModal = false">稍后再说</button>
          <button class="primary sm" @click="showUpdateModal = false; startDownload()">下载并安装</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup {
  max-width: 620px;
}
h2 {
  margin: 0 0 6px;
  font-weight: 500;
}
.field {
  margin: 18px 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.model-field {
  margin: 0 0 20px;
}
.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.model-row select {
  max-width: 260px;
}
label {
  font-size: 13px;
  color: var(--text-secondary);
}
.hint {
  font-size: 12px;
}
.err {
  color: var(--text-danger);
  background: var(--bg-danger);
  padding: 8px 12px;
  border-radius: var(--radius);
  font-size: 13px;
  margin: 12px 0;
}
.actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

/* ── Claude CLI 状态 ── */
.cli-block {
  margin-top: 32px;
  padding-top: 24px;
  border-top: 0.5px solid var(--border);
}
.cli-block h3 {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 500;
}
.cli-sub {
  font-size: 12px;
  margin: 0 0 14px;
  line-height: 1.5;
}
.cli-banner {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border-radius: var(--radius);
  border: 0.5px solid var(--border);
}
.cli-banner.ok {
  background: var(--bg-success, rgba(52, 199, 89, 0.08));
  border-color: var(--border-success, rgba(52, 199, 89, 0.35));
}
.cli-banner.warn {
  background: var(--bg-warning, rgba(255, 179, 0, 0.08));
  border-color: var(--border-warning, rgba(255, 179, 0, 0.35));
}
.cli-banner.bad {
  background: var(--bg-danger);
  border-color: var(--text-danger);
}
.cli-icon {
  font-size: 20px;
  line-height: 1.2;
}
.cli-txt {
  flex: 1;
  min-width: 0;
}
.cli-title {
  font-size: 14px;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
}
.sub-badge {
  font-size: 10px;
  letter-spacing: 0.5px;
}
.cli-detail {
  font-size: 12px;
  margin-top: 3px;
  line-height: 1.5;
  word-break: break-all;
}
.cli-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: 12px;
}
.cli-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  background: var(--surface-2);
  padding: 4px 8px;
  border-radius: var(--radius);
}
.cli-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 14px;
}
.doc-link {
  font-size: 12px;
  text-decoration: none;
}
.doc-link:hover {
  text-decoration: underline;
}

.pill.sm, .sm {
  font-size: 11px;
}
button.sm {
  padding: 3px 8px;
}

/* ── 版本 / 检测更新 ── */
.about-block {
  margin-top: 32px;
  padding-top: 24px;
  border-top: 0.5px solid var(--border);
}
.about-block h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 500;
}
.version-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.version-left {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.version-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.dl-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 180px;
}
.dl-bar {
  flex: 1;
  height: 6px;
  background: var(--surface-1);
  border-radius: 3px;
  overflow: hidden;
}
.dl-fill {
  height: 100%;
  background: var(--border-accent);
  border-radius: 3px;
  transition: width 0.2s;
}
.dl-text {
  white-space: nowrap;
}

/* ── 更新弹窗 ── */
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-box {
  width: 480px;
  max-width: 90vw;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 18px 12px;
  border-bottom: 0.5px solid var(--border);
}
.modal-title {
  font-size: 14px;
  font-weight: 500;
}
.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
}
.release-notes {
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  margin: 0;
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px 16px;
  border-top: 0.5px solid var(--border);
}
</style>
