<script setup lang="ts">
import { ref, computed, onMounted, onActivated, watch } from "vue";
import { api, type FlowRow, type DeviceRow, type ApkInfo, type ResourceFile } from "../api";
import { store } from "../store";
import { runStore } from "../runStore";
import RunMonitor from "./RunMonitor.vue";

// keep-alive 精确保活本组件（App.vue <keep-alive include="Runner">），切走 tab 不销毁选择状态。
// 运行状态本身放模块级 runStore（跨组件/跨子 tab 共享、独立于组件生命周期），本组件只管选择 UI + 触发编排。
defineOptions({ name: "Runner" });

// 子 tab：场景库（选择）/ 执行台（实时监控）
const subTab = ref<"library" | "monitor">("library");

// ── 中栏：当前 App 的用例/固化脚本 ──
const flows = ref<FlowRow[]>([]);
const pickedCases = ref<string[]>([]); // 勾选的固化用例 case_id
// ── 右栏：设备 + 看板 + 执行 ──
const devices = ref<DeviceRow[]>([]);
const pickedSerials = ref<string[]>([]);
const boardMode = ref<"current" | "new">("current"); // 关联当前 / 新建看板
const brainMode = ref(false); // 脚本自愈：失败自动交 claude 诊断+改脚本重跑
const err = ref("");
const confirmNewBoard = ref(false);

const frozen = computed(() => flows.value.filter((f) => f.has_flow));
const nonFrozen = computed(() => flows.value.filter((f) => !f.has_flow));

async function loadFlows() {
  if (!store.activeSlug) { flows.value = []; return; }
  flows.value = await api.listFlows(store.activeSlug);
  // 剔除已不存在的勾选
  const ids = new Set(frozen.value.map((f) => f.case_id));
  pickedCases.value = pickedCases.value.filter((c) => ids.has(c));
}

async function loadDevices() {
  devices.value = await api.listDevices(store.activeSlug || "");
  // 剔除已掉线/未授权的勾选：设备卡片只渲染当前在线设备，若不同步剪枝，
  // 已勾但掉线的 serial 会残留在 pickedSerials 里（UI 看不到却仍参与执行），导致“选 1 台却跑 N 台”。
  const online = new Set(devices.value.filter((d) => d.state === "device").map((d) => d.serial));
  pickedSerials.value = pickedSerials.value.filter((s) => online.has(s));
  if (!pickedSerials.value.length) {
    const def = devices.value.find((d) => d.is_default) || devices.value.find((d) => d.state === "device");
    if (def) pickedSerials.value = [def.serial];
  }
}

async function loadAll() {
  err.value = "";
  try {
    await Promise.all([loadFlows(), loadDevices()]);
  } catch (e: any) {
    err.value = String(e);
  }
}

// 「▶ 执行选中」→ 校验 → 跳到执行台 tab → 交给 runStore 串行编排（for 设备 × for 用例）
function runSelected() {
  if (runStore.running) return;
  if (!store.activeSlug) { err.value = "请先在左栏选一个 App"; return; }
  const cases = frozen.value.filter((f) => pickedCases.value.includes(f.case_id));
  if (!cases.length) { err.value = "中栏请至少勾选一个固化用例"; return; }
  if (!pickedSerials.value.length) { err.value = "右栏请至少勾选一台设备"; return; }
  err.value = "";
  if (boardMode.value === "new") { confirmNewBoard.value = true; return; }
  launch(false);
}

function launch(newBoard: boolean) {
  confirmNewBoard.value = false;
  const cases = frozen.value
    .filter((f) => pickedCases.value.includes(f.case_id))
    .map((f) => ({ case_id: f.case_id, script: f.script, module: f.module }));
  subTab.value = "monitor"; // 立即跳到执行台看实时过程
  runStore
    .start({
      slug: store.activeSlug,
      cases,
      serials: [...pickedSerials.value],
      brain: brainMode.value,
      newBoard,
      title: `${store.activeSlug} · ${cases.length} 用例 × ${pickedSerials.value.length} 设备`,
    })
    .then(() => loadFlows()); // 跑完刷新用例列表拿最新 last_result
}

// ── 左栏顶部：上传 APK（本地解析 → 装机 → 注册）──
const uploadOpen = ref(false);
const apkPath = ref("");
const apkInfo = ref<ApkInfo | null>(null);
const slugEdit = ref("");
const uploadSerials = ref<string[]>([]);
const uploadLog = ref<string[]>([]);
const uploadStatus = ref("");
const uploadErr = ref("");
const uploading = ref(false);

function openUpload() {
  uploadOpen.value = true;
  apkPath.value = ""; apkInfo.value = null; slugEdit.value = "";
  uploadSerials.value = []; uploadLog.value = []; uploadStatus.value = ""; uploadErr.value = "";
  loadDevices();
}

async function chooseApk() {
  uploadErr.value = "";
  try {
    const p = await api.pickApk();
    if (!p) return;
    apkPath.value = p;
    apkInfo.value = await api.probeApk(p);
    slugEdit.value = apkInfo.value.suggested_slug;
  } catch (e: any) {
    uploadErr.value = String(e);
    apkInfo.value = null;
  }
}

async function doUpload() {
  uploadErr.value = "";
  const slug = slugEdit.value.trim();
  if (!apkInfo.value || !apkPath.value) { uploadErr.value = "先选一个 APK"; return; }
  if (!slug) { uploadErr.value = "slug 不能为空"; return; }
  if (!uploadSerials.value.length) { uploadErr.value = "至少选一台设备装机"; return; }
  uploading.value = true;
  uploadStatus.value = "";
  uploadLog.value = [];
  try {
    // 1) 逐台装机
    for (const serial of uploadSerials.value) {
      uploadLog.value.push(`$ adb -s ${serial} install -r ${apkPath.value}`);
      const code = await api.installApk(apkPath.value, serial, (l) => uploadLog.value.push(l));
      if (code !== 0) { uploadStatus.value = `✖ 装机失败（${serial}，exit ${code}）`; uploading.value = false; return; }
    }
    // 2) 用第一台已装设备注册（init_target.py + 补 app_slug + 建工作区）
    const primary = uploadSerials.value[0];
    uploadLog.value.push(`$ AITEST_APP=${slug} python3 tools/init_target.py ${apkInfo.value.package} --serial ${primary} --write`);
    const code = await api.registerApp(slug, apkInfo.value.package, primary, (l) => uploadLog.value.push(l));
    if (code !== 0) { uploadStatus.value = `✖ 注册失败（exit ${code}）`; uploading.value = false; return; }
    uploadStatus.value = `✔ 已注册并选中 ${slug}`;
    await store.loadApps();
    await store.setActive(slug);
    await loadAll();
  } catch (e: any) {
    uploadStatus.value = "✖ " + String(e);
  } finally {
    uploading.value = false;
  }
}

// ── 左栏底部：测试资源（assets/，所有 App 共用；固化脚本用相对路径 assets/<文件名> 引用）──
const resourceFiles = ref<ResourceFile[]>([]);
const resErr = ref("");
const uploadingRes = ref(false);

async function loadResources() {
  try {
    resourceFiles.value = await api.listResourceFiles();
  } catch (e: any) {
    resErr.value = String(e);
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function uploadResource() {
  resErr.value = "";
  try {
    const p = await api.pickResourceFile();
    if (!p) return;
    uploadingRes.value = true;
    await api.uploadResourceFile(p);
    await loadResources();
  } catch (e: any) {
    resErr.value = String(e);
  } finally {
    uploadingRes.value = false;
  }
}

async function removeResource(name: string) {
  resErr.value = "";
  try {
    await api.deleteResourceFile(name);
    await loadResources();
  } catch (e: any) {
    resErr.value = String(e);
  }
}

function selectApp(slug: string) {
  if (slug === store.activeSlug) return;
  store.setActive(slug).then(loadAll);
}

watch(() => store.activeSlug, () => { pickedCases.value = []; loadAll(); });
onMounted(() => { loadAll(); loadResources(); });
// keep-alive 保活后切回本 tab 时刷新设备/用例列表；正在执行则不动，避免打断在跑的任务与日志。
onActivated(() => { if (!runStore.running) { loadAll(); loadResources(); } });
</script>

<template>
  <div class="runner">
    <div class="subtabs">
      <button class="stab" :class="{ on: subTab === 'library' }" @click="subTab = 'library'">场景库</button>
      <button class="stab" :class="{ on: subTab === 'monitor' }" @click="subTab = 'monitor'">
        执行台<span v-if="runStore.running" class="live-dot" title="正在执行" />
      </button>
    </div>

    <!-- ══════ 场景库：选择 App / 用例 / 设备 / 看板 + 执行 ══════ -->
    <div v-show="subTab === 'library'" class="library">
    <p class="muted sub">
      只跑固化脚本，一律经 <span class="mono">run_flow.py</span>（自动登记账本）。判定与关键证据升级仍在 Claude Code 做。
    </p>
    <div v-if="err" class="err">{{ err }}</div>

    <div class="cols">
      <!-- ── 左：App 库 + 测试资源 ── -->
      <div class="col app-col">
        <div class="card applist">
          <div class="col-hd">
            <span>App 库</span>
            <button class="primary sm" @click="openUpload">+ 上传 APK</button>
          </div>
          <div class="col-body">
            <div v-if="!store.apps.length" class="muted empty-hint">
              还没有被测 App。点「上传 APK」注册第一个。
            </div>
            <div
              v-for="a in store.apps"
              :key="a.slug"
              class="app-item"
              :class="{ on: a.slug === store.activeSlug }"
              @click="selectApp(a.slug)"
            >
              <div class="app-name">
                {{ a.slug }}
                <span v-if="a.slug === store.activeSlug" class="dot">●</span>
              </div>
              <div class="app-sub muted">{{ a.app_version || "—" }} · {{ a.package }}</div>
            </div>
          </div>
        </div>

        <div class="card resbox">
          <div class="col-hd">
            <span>测试资源</span>
            <button class="primary sm" :disabled="uploadingRes" @click="uploadResource">+ 上传文件</button>
          </div>
          <div class="col-body">
            <div v-if="resErr" class="err">{{ resErr }}</div>
            <div v-if="!resourceFiles.length" class="muted empty-hint">
              还没有素材文件。上传后存到项目 <span class="mono">assets/</span> 目录，固化脚本按文件名引用。
            </div>
            <div v-for="f in resourceFiles" :key="f.name" class="res-item">
              <span class="mono res-name" :title="f.name">{{ f.name }}</span>
              <span class="muted res-size">{{ formatSize(f.size) }}</span>
              <button class="sm danger" title="删除" @click="removeResource(f.name)">✕</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ── 中：用例 / 固化脚本 ── -->
      <div class="col case-col card">
        <div class="col-hd"><span>用例（{{ store.activeSlug || "未选 App" }}）</span></div>
        <div class="col-body">
          <template v-if="frozen.length">
            <label v-for="f in frozen" :key="f.case_id" class="case-item">
              <input type="checkbox" :value="f.case_id" v-model="pickedCases" />
              <span class="case-id mono">{{ f.case_id }}</span>
              <span class="case-mod muted">{{ f.module }}</span>
              <span
                v-if="f.last_result"
                class="pill sm"
                :class="f.last_result === '通过' ? 'pill-success' : f.last_result === '失败' ? 'pill-danger' : 'pill-muted'"
                >{{ f.last_result }}</span
              >
            </label>
          </template>
          <div v-else-if="store.activeSlug" class="muted empty-hint">该 App 还没有固化用例（queue.csv 无固化脚本行）。</div>

          <template v-if="nonFrozen.length">
            <div class="divider muted">非固化用例（锁，走主循环）</div>
            <div v-for="f in nonFrozen" :key="f.case_id" class="case-item locked">
              <input type="checkbox" disabled />
              <span class="case-id mono">{{ f.case_id }}</span>
              <span class="case-mod muted">{{ f.module }}</span>
              <span class="pill pill-muted sm">Claude Code</span>
            </div>
          </template>
        </div>
      </div>

      <!-- ── 右：设备 + 看板 + 执行 ── -->
      <div class="col dev-col">
        <div class="card devbox">
          <div class="col-hd">
            <span>设备</span>
            <button class="sm" @click="loadDevices">刷新</button>
          </div>
          <div class="col-body">
            <div v-if="!devices.length" class="muted empty-hint">无在线设备（adb devices 为空）。</div>
            <label v-for="d in devices" :key="d.serial" class="dev-item">
              <input type="checkbox" :value="d.serial" v-model="pickedSerials" :disabled="d.state !== 'device'" />
              <span class="mono">{{ d.serial }}</span>
              <span class="muted">{{ d.model || d.state }}</span>
            </label>
          </div>
        </div>

        <div class="card boardbox">
          <div class="col-hd"><span>看板</span></div>
          <div class="col-body board-opts">
            <label class="radio"><input type="radio" value="current" v-model="boardMode" />关联当前批次（续用）</label>
            <label class="radio"><input type="radio" value="new" v-model="boardMode" />
              新建看板 <span class="muted">（开新一轮 · 旧账本自动归档）</span>
            </label>
          </div>
          <label class="brain-opt" :class="{ on: brainMode }">
            <input type="checkbox" v-model="brainMode" />
            <span class="brain-txt">
              脚本自愈
              <span class="muted brain-sub">失败时 Claude 接管：诊断→只改导航/健壮性→重跑（至多 3 次）。判为 App 缺陷则停。</span>
            </span>
          </label>
          <button class="primary run-btn" :disabled="runStore.running" @click="runSelected">
            {{ runStore.running ? "执行中…" : "▶ 执行选中" }}
          </button>
        </div>
      </div>
    </div>

    </div><!-- /场景库 -->

    <!-- ══════ 执行台：实时监控（矩阵 + 进度 + 过程 + 中止）══════ -->
    <div v-show="subTab === 'monitor'" class="monitor-wrap">
      <RunMonitor />
    </div>

    <!-- 新建看板二次确认 -->
    <div v-if="confirmNewBoard" class="overlay">
      <div class="dialog card">
        <div class="dtitle">执行前先开新一轮？</div>
        <p class="muted">
          你选了「新建看板」。将先执行 <span class="mono">new_run.py</span>：新建 Sheet + 新 run_id，把
          {{ store.activeSlug }} 当前账本<b>整份归档到 archive/&lt;run_id&gt;</b>后切到新一轮，随后再跑选中的用例。
          <br />历史不丢：证据文件原地保留，旧账本可在 archive 与上一轮云端 Sheet 查看。
        </p>
        <div class="dactions">
          <button @click="confirmNewBoard = false">取消</button>
          <button class="primary" @click="launch(true)">确认开新一轮并执行</button>
        </div>
      </div>
    </div>

    <!-- 上传 APK 模态 -->
    <div v-if="uploadOpen" class="overlay">
      <div class="dialog card upload">
        <div class="dtitle">上传 APK = 注册被测 App + 装机</div>
        <div class="up-row">
          <button @click="chooseApk">选择 APK…</button>
          <span class="mono small path">{{ apkPath || "（未选）" }}</span>
        </div>
        <div v-if="uploadErr" class="err">{{ uploadErr }}</div>

        <div v-if="apkInfo" class="apk-info">
          <div><span class="muted">包名</span> <span class="mono">{{ apkInfo.package }}</span></div>
          <div><span class="muted">版本</span> {{ apkInfo.version || "—" }}</div>
          <div><span class="muted">名称</span> {{ apkInfo.label || "—" }}</div>
          <div class="slug-row">
            <span class="muted">slug（证据目录名，可改）</span>
            <input v-model="slugEdit" class="slug-input mono" />
          </div>
          <div class="up-dev">
            <div class="muted small">装到哪些设备（第一台用于注册探测）</div>
            <div v-if="!devices.length" class="muted small">无在线设备。</div>
            <label v-for="d in devices" :key="d.serial" class="dev-item">
              <input type="checkbox" :value="d.serial" v-model="uploadSerials" :disabled="d.state !== 'device'" />
              <span class="mono">{{ d.serial }}</span><span class="muted">{{ d.model || d.state }}</span>
            </label>
          </div>
        </div>

        <div v-if="uploadLog.length" class="log-scroll up-log">
          <pre class="log mono">{{ uploadLog.join("\n") }}</pre>
        </div>
        <div v-if="uploadStatus" class="up-status" :class="{ bad: uploadStatus.startsWith('✖') }">{{ uploadStatus }}</div>

        <div class="dactions">
          <button @click="uploadOpen = false">关闭</button>
          <button class="primary" :disabled="uploading || !apkInfo" @click="doUpload">
            {{ uploading ? "处理中…" : "装机并注册" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.runner { display: flex; flex-direction: column; height: 100%; }
h2 { margin: 0; font-weight: 500; }
.sub { margin: 4px 0 10px; }

/* 子 tab */
.subtabs { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 0.5px solid var(--border); }
.stab { position: relative; background: transparent; border: none; padding: 8px 16px; font-size: 14px; color: var(--text-secondary); border-bottom: 2px solid transparent; margin-bottom: -0.5px; cursor: pointer; }
.stab:hover { color: var(--text-primary, #111); }
.stab.on { color: var(--text-primary, #111); border-bottom-color: var(--text-accent); font-weight: 500; }
.live-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--text-accent); margin-left: 6px; vertical-align: middle; animation: livepulse 1.2s infinite; }
@keyframes livepulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.library { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.monitor-wrap { flex: 1; min-height: 0; display: flex; }
.monitor-wrap :deep(.monitor) { flex: 1; min-width: 0; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 8px 12px; border-radius: var(--radius); margin: 6px 0; font-size: 13px; }

.cols { display: flex; gap: 12px; flex: 1; min-height: 0; }
.col { display: flex; flex-direction: column; min-height: 0; }
.app-col { width: 210px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; }
.applist { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.resbox { flex-shrink: 0; max-height: 40%; display: flex; flex-direction: column; }
.res-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px; font-size: 12px; border-radius: var(--radius); }
.res-item:hover { background: var(--surface-1); }
.res-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.res-size { flex-shrink: 0; font-size: 11px; }
button.danger { color: var(--text-danger); background: transparent; border: none; cursor: pointer; padding: 2px 4px; }
button.danger:hover { background: var(--bg-danger); border-radius: var(--radius); }
.case-col { flex: 1; min-width: 0; }
.dev-col { width: 260px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; }
.col-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 0.5px solid var(--border); font-size: 13px; font-weight: 500; }
.col-body { flex: 1; overflow: auto; padding: 6px; min-height: 0; }
.empty-hint { padding: 14px 10px; font-size: 12px; line-height: 1.5; }

.app-item { padding: 8px 10px; border-radius: var(--radius); cursor: pointer; margin-bottom: 2px; }
.app-item:hover { background: var(--surface-1); }
.app-item.on { background: var(--bg-accent); }
.app-name { font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 6px; }
.app-item.on .app-name { color: var(--text-accent); }
.dot { color: var(--text-accent); font-size: 9px; }
.app-sub { font-size: 11px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.case-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: var(--radius); font-size: 13px; cursor: pointer; }
.case-item:hover { background: var(--surface-1); }
.case-item.locked { opacity: 0.6; cursor: default; }
.case-id { flex-shrink: 0; }
.case-mod { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.divider { font-size: 11px; padding: 10px 8px 4px; border-top: 0.5px solid var(--border); margin-top: 6px; }

.devbox { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.dev-item { display: flex; align-items: center; gap: 8px; padding: 5px 8px; font-size: 12px; cursor: pointer; }
.boardbox { flex-shrink: 0; }
.board-opts { display: flex; flex-direction: column; gap: 8px; padding: 12px; }
.radio { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }
.warn { color: var(--text-danger); font-size: 11px; }
.brain-opt { display: flex; align-items: flex-start; gap: 8px; margin: 2px 12px 10px; padding: 8px 10px; border: 0.5px solid var(--border); border-radius: var(--radius); cursor: pointer; }
.brain-opt.on { background: var(--bg-accent); border-color: var(--text-accent); }
.brain-opt input { margin-top: 2px; }
.brain-txt { font-size: 13px; line-height: 1.4; }
.brain-sub { display: block; font-size: 11px; margin-top: 3px; }
.run-btn { margin: 0 12px 12px; }

.pill.sm, .sm { font-size: 11px; }
button.sm { padding: 3px 8px; }

.logwrap { margin-top: 12px; flex-shrink: 0; }
.log-hd { font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }
.log-scroll { max-height: 240px; overflow: auto; background: var(--surface-2); border: 0.5px solid var(--border); border-radius: var(--radius); padding: 8px; }
.grp { margin-bottom: 10px; }
.grp-hd { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 12px; margin-bottom: 3px; }
.grp-status { color: var(--text-secondary); }
.grp-status.good { color: var(--text-success); }
.grp-status.bad { color: var(--text-danger); }
.log { font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; color: var(--text-secondary); }

.overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: flex; align-items: center; justify-content: center; z-index: 10; }
.dialog { padding: 20px 22px; max-width: 460px; }
.dialog.upload { width: 480px; max-width: 90vw; max-height: 86vh; overflow: auto; }
.dtitle { font-size: 15px; font-weight: 500; margin-bottom: 12px; }
.dactions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.up-row { display: flex; align-items: center; gap: 10px; }
.path { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.small { font-size: 12px; }
.apk-info { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.slug-row { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.slug-input { padding: 5px 8px; font-size: 13px; }
.up-dev { margin-top: 8px; border-top: 0.5px solid var(--border); padding-top: 8px; }
.up-log { max-height: 160px; margin-top: 10px; }
.up-status { margin-top: 8px; font-size: 13px; color: var(--text-success); }
.up-status.bad { color: var(--text-danger); }
</style>
