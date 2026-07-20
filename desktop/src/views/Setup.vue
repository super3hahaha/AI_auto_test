<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, type ClaudeCliStatus } from "../api";
import { store } from "../store";

const emit = defineEmits<{ configured: [] }>();
const root = ref("");
const python = ref("python3");
const err = ref("");
const saving = ref(false);

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
        App 通过本机 Claude CLI 调用模型（用例失败时「脚本自愈」由 claude 接管）。这里展示当前 CLI 的安装与登录状态。
      </p>

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
</style>
