<script setup lang="ts">
import { ref, reactive, computed, onMounted, onActivated, watch } from "vue";
import { confirm, message } from "@tauri-apps/plugin-dialog";
import { api, type FlowRow, type DeviceRow, type ApkInfo, type ApkVersionInfo } from "../api";
import { store } from "../store";
import { runStore } from "../runStore";
import RunMonitor from "./RunMonitor.vue";
import RunHistory from "./RunHistory.vue";

// keep-alive 精确保活本组件（App.vue <keep-alive include="Runner">），切走 tab 不销毁选择状态。
// 运行状态本身放模块级 runStore（跨组件/跨子 tab 共享、独立于组件生命周期），本组件只管选择 UI + 触发编排。
defineOptions({ name: "Runner" });

// 子 tab：场景库（选择）/ 执行台（实时监控）/ 执行记录（保存的历史快照）——资源库已提升为侧栏一级入口（views/Resources.vue）
const subTab = ref<"library" | "monitor" | "history">("library");
// 切到「执行记录」子 tab 时刷新列表（Runner 被 keep-alive 保活，子 tab 用 v-show 不会触发子组件生命周期）
const historyRef = ref<InstanceType<typeof RunHistory> | null>(null);
watch(subTab, (v) => { if (v === "history") historyRef.value?.reload(); });

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

// 优先级配色：P0(danger) > P1(warning) > P2(accent，浅蓝区分于 P3) > P3(muted)
function priorityPill(p: string) {
  if (p === "P0") return "pill-danger";
  if (p === "P1") return "pill-warning";
  if (p === "P2") return "pill-accent";
  return "pill-muted";
}

// 全选/取消全选：只对固化用例生效（非固化用例本来就锁着不可勾）
function selectAllCases() {
  pickedCases.value = frozen.value.map((f) => f.case_id);
}
function clearAllCases() {
  pickedCases.value = [];
}

// 区间选择：先勾选两个（不必相邻的）用例作为起止点，再点这个按钮，
// 把它们在列表中之间的所有用例一并选中（含端点），已勾选的其它用例不受影响。
function selectRange() {
  const idxs = pickedCases.value
    .map((id) => frozen.value.findIndex((f) => f.case_id === id))
    .filter((i) => i >= 0);
  if (idxs.length < 2) {
    err.value = "区间选择需先勾选至少两个用例作为起止点";
    return;
  }
  const lo = Math.min(...idxs);
  const hi = Math.max(...idxs);
  const ids = frozen.value.slice(lo, hi + 1).map((f) => f.case_id);
  const set = new Set(pickedCases.value);
  ids.forEach((id) => set.add(id));
  pickedCases.value = Array.from(set);
}

// 用例条目悬停提示：steps/expected 编号列出，供快速预览用例内容而不必打开 yaml。
// 用自绘浮层而非原生 title（原生 tooltip 是系统灰底，跟应用配色不搭）；悬停满 2 秒才弹出，避免扫视列表时框到处闪。
const HOVER_DELAY = 2000;
const HIDE_GRACE = 150; // 离开条目到落到浮层上之间留的缓冲，否则鼠标一移到浮层上就先被 mouseleave 关掉了
const hoverTip = ref<{ x: number; y: number; f: FlowRow } | null>(null);
let hoverTimer: ReturnType<typeof setTimeout> | undefined;
let hideTimer: ReturnType<typeof setTimeout> | undefined;
let pendingPos = { x: 0, y: 0 };
function showTip(e: MouseEvent, f: FlowRow) {
  clearTimeout(hideTimer);
  if (!f.steps.length && !f.expected.length) return;
  clearTimeout(hoverTimer);
  pendingPos = { x: e.clientX, y: e.clientY };
  hoverTimer = setTimeout(() => {
    // 弹出后位置定住不再跟手，方便把鼠标移进浮层里滚动
    hoverTip.value = { x: pendingPos.x, y: pendingPos.y, f };
  }, HOVER_DELAY);
}
function moveTip(e: MouseEvent) {
  pendingPos = { x: e.clientX, y: e.clientY };
}
function hideTip() {
  clearTimeout(hoverTimer);
  hideTimer = setTimeout(() => { hoverTip.value = null; }, HIDE_GRACE);
}
function cancelHide() {
  clearTimeout(hideTimer);
}
const tipStyle = computed(() => {
  if (!hoverTip.value) return {};
  const pad = 18;
  const maxW = 620;
  const maxH = 520;
  let left = hoverTip.value.x + pad;
  let top = hoverTip.value.y + pad;
  if (left + maxW > window.innerWidth) left = Math.max(8, hoverTip.value.x - maxW - pad);
  if (top + maxH > window.innerHeight) top = Math.max(8, window.innerHeight - maxH - 8);
  return { left: `${left}px`, top: `${top}px` };
});

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
  // 收尾阶段（sync_sheets/doc_report）跑完前禁止开新一轮：它们跑完会把当前 doc_id 写回
  // target.json，若这时用户已经手动/自动开了新一轮（尤其「新建看板」会先建一份新 Doc、
  // 也回写 target.json），旧一轮收尾晚完成的那次写入会把新 Doc 的 doc_id 覆盖回旧的——
  // 线上明明刷新成功，desktop 却把指针指回了上一轮的 Doc。见 docs/gotchas.md 对应条目。
  if (runStore.running || runStore.syncing || runStore.docGenerating) return;
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
  // 选了某个留存版本 → 执行前先在每台设备上强制重装这个版本（不管设备当前是不是已经是它）
  const slug = store.activeSlug;
  const ver = selectedVersion[slug];
  const apkPath = ver ? appVersions[slug]?.find((v) => v.version === ver)?.path : undefined;
  const pkg = store.activeApp()?.package;
  runStore
    .start({
      slug,
      cases,
      serials: [...pickedSerials.value],
      brain: brainMode.value,
      newBoard,
      title: `${slug} · ${cases.length} 用例 × ${pickedSerials.value.length} 设备${ver ? ` · ${ver}` : ""}`,
      apkPath: apkPath && pkg ? apkPath : undefined,
      package: apkPath && pkg ? pkg : undefined,
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
    // 同包名之前注册过就沿用那次的 slug（可能有多条，取最近更新的一条），
    // 而不是每次都从 APK label 重新拆一个新 slug 出来
    const prior = store.apps
      .filter((a) => a.package === apkInfo.value!.package)
      .sort((a, b) => b.updated_at - a.updated_at)[0];
    slugEdit.value = prior ? prior.slug : apkInfo.value.suggested_slug;
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
  uploading.value = true;
  uploadStatus.value = "";
  uploadLog.value = [];
  try {
    // 1) 装到勾选的设备（可选；不勾就跳过，直接注册——前提是这个包已经装在某台在线设备上）
    for (const serial of uploadSerials.value) {
      uploadLog.value.push(`$ adb -s ${serial} install -r ${apkPath.value}`);
      const code = await api.installApk(apkPath.value, apkInfo.value.package, serial, (l) => uploadLog.value.push(l));
      if (code !== 0) { uploadStatus.value = `✖ 装机失败（${serial}，exit ${code}）`; uploading.value = false; return; }
    }
    // 2) 注册（init_target.py + 补 app_slug + 建工作区）：勾了设备用第一台探测；没勾就留空，
    // 只有一台在线设备时后端会自动选中它，多台在线又没勾选会报错提示用 --serial 指定。
    const primary = uploadSerials.value[0] || "";
    uploadLog.value.push(`$ AITEST_APP=${slug} python3 tools/init_target.py ${apkInfo.value.package}${primary ? ` --serial ${primary}` : ""} --write`);
    const code = await api.registerApp(slug, apkInfo.value.package, primary, (l) => uploadLog.value.push(l));
    if (code !== 0) { uploadStatus.value = `✖ 注册失败（exit ${code}）`; uploading.value = false; return; }
    // 留存这个版本的 apk 文件，供以后同 slug 下多版本切换执行时直接装机用
    await api.saveApkVersion(slug, apkPath.value, apkInfo.value.version || "unknown");
    delete appVersions[slug]; // 清缓存，下次展开重新拉取（带上刚存的这个版本）
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

function selectApp(slug: string) {
  if (slug === store.activeSlug) return;
  store.setActive(slug).then(loadAll);
}

// ── App 库折叠树：同 slug 下留存的多版本 APK（apps/<slug>/apks/*.apk）──
// 懒加载：第一次展开某个 App 才去查它的版本列表，查过就缓存，不用一次性把所有 App 的版本都拉一遍。
const expandedApps = reactive(new Set<string>());
const appVersions = reactive<Record<string, ApkVersionInfo[]>>({});
// slug -> 用户手动点选的版本。不点选就一直是 undefined——展示用 a.app_version（上次上传注册时探测到的版本）、
// 执行时也不强制重装，都走老逻辑。只有显式点了某个版本行，这两处才会跟着切换到点选的那个版本。
const selectedVersion = reactive<Record<string, string>>({});

async function toggleAppExpand(slug: string) {
  if (expandedApps.has(slug)) { expandedApps.delete(slug); return; }
  expandedApps.add(slug);
  if (!appVersions[slug]) {
    try {
      const versions = await api.listApkVersions(slug);
      appVersions[slug] = versions; // 仅供选择列表用；不自动预选，选不选是用户的事
    } catch (e: any) {
      err.value = String(e);
    }
  }
}

function pickVersion(slug: string, version: string) {
  selectedVersion[slug] = version;
  if (slug !== store.activeSlug) selectApp(slug);
}

function fmtSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

async function removeApp(slug: string) {
  const ok = await confirm(`会移出 App 库（用例/固化脚本/执行账本一起挪走），不是硬删除，挪进 apps/.trash/ 回收站，手滑了可以手动挪回来。`, {
    title: `确认删除 App「${slug}」的注册？`,
    kind: "warning",
  });
  if (!ok) return;
  try {
    const trashPath = await store.deleteApp(slug);
    await message(`已挪进回收站：\n${trashPath}\n\n手滑误删的话，把这个目录挪到 apps/ 下并改名回「${slug}」即可恢复。`, {
      title: "已删除",
    });
    await loadAll();
  } catch (e: any) {
    err.value = String(e);
  }
}

watch(() => store.activeSlug, () => { pickedCases.value = []; loadAll(); });
onMounted(() => { loadAll(); });
// keep-alive 保活后切回本 tab 时刷新设备/用例列表；正在执行则不动，避免打断在跑的任务与日志。
onActivated(() => { if (!runStore.running) loadAll(); });
</script>

<template>
  <div class="runner">
    <div class="subtabs">
      <button class="stab" :class="{ on: subTab === 'library' }" @click="subTab = 'library'">场景库</button>
      <button class="stab" :class="{ on: subTab === 'monitor' }" @click="subTab = 'monitor'">
        执行台<span v-if="runStore.running" class="live-dot" title="正在执行" />
      </button>
      <button class="stab" :class="{ on: subTab === 'history' }" @click="subTab = 'history'">执行记录</button>
    </div>

    <!-- ══════ 场景库：选择 App / 用例 / 设备 / 看板 + 执行 ══════ -->
    <div v-show="subTab === 'library'" class="library">
    <div v-if="err" class="err">{{ err }}</div>

    <div class="cols">
      <!-- ── 左：App 库 ── -->
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
            <div v-for="a in store.apps" :key="a.slug" class="app-group">
              <div
                class="app-item"
                :class="{ on: a.slug === store.activeSlug }"
                @click="selectApp(a.slug)"
              >
                <span class="app-caret" @click.stop="toggleAppExpand(a.slug)">{{ expandedApps.has(a.slug) ? "▾" : "▸" }}</span>
                <div class="app-main">
                  <div class="app-name">
                    {{ a.slug }}
                    <span v-if="a.slug === store.activeSlug" class="dot">●</span>
                    <button class="app-del" title="删除此 App" @click.stop="removeApp(a.slug)">✕</button>
                  </div>
                  <div class="app-sub muted">{{ selectedVersion[a.slug] || a.app_version || "—" }} · {{ a.package }}</div>
                </div>
              </div>
              <div v-if="expandedApps.has(a.slug)" class="app-version-list">
                <div v-if="!appVersions[a.slug]?.length" class="muted app-version-empty">还没有留存的 APK 版本</div>
                <div
                  v-for="v in appVersions[a.slug]"
                  :key="v.version"
                  class="app-version-item"
                  :class="{ on: selectedVersion[a.slug] === v.version }"
                  @click="pickVersion(a.slug, v.version)"
                  :title="`执行时先在设备上强制重装此版本`"
                >
                  <span class="mono">{{ v.version }}</span>
                  <span class="muted app-version-size">{{ fmtSize(v.size) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── 中：用例 / 固化脚本 ── -->
      <div class="col case-col card">
        <div class="col-hd">
          <span>用例（{{ store.activeSlug || "未选 App" }}）</span>
          <div class="case-toolbar">
            <button class="sm" @click="selectAllCases">全选</button>
            <button class="sm" @click="selectRange">区间选择</button>
            <button class="sm" @click="clearAllCases">取消全选</button>
            <button class="sm" @click="loadFlows">刷新</button>
          </div>
        </div>
        <div class="col-body">
          <template v-if="frozen.length">
            <label
              v-for="f in frozen"
              :key="f.case_id"
              class="case-item"
              @mouseenter="showTip($event, f)"
              @mousemove="moveTip"
              @mouseleave="hideTip"
            >
              <input type="checkbox" :value="f.case_id" v-model="pickedCases" />
              <span class="case-id mono">{{ f.case_id }}</span>
              <span class="case-mod muted">{{ f.module }}</span>
              <span v-if="f.purpose" class="case-purpose muted">{{ f.purpose }}</span>
              <span v-if="f.priority" class="pill sm" :class="priorityPill(f.priority)">{{ f.priority }}</span>
            </label>
          </template>
          <div v-else-if="store.activeSlug" class="muted empty-hint">该 App 还没有固化用例（queue.csv 无固化脚本行）。</div>

          <template v-if="nonFrozen.length">
            <div class="divider muted">非固化用例（锁，走主循环）</div>
            <div
              v-for="f in nonFrozen"
              :key="f.case_id"
              class="case-item locked"
              @mouseenter="showTip($event, f)"
              @mousemove="moveTip"
              @mouseleave="hideTip"
            >
              <input type="checkbox" disabled />
              <span class="case-id mono">{{ f.case_id }}</span>
              <span class="case-mod muted">{{ f.module }}</span>
              <span v-if="f.purpose" class="case-purpose muted">{{ f.purpose }}</span>
              <span v-if="f.priority" class="pill sm" :class="priorityPill(f.priority)">{{ f.priority }}</span>
              <span class="pill pill-muted sm">Claude Code</span>
            </div>
          </template>
        </div>
      </div>

      <!-- 用例悬停浮层：steps/expected 编号列表，定位在弹出那一刻的鼠标位置附近 -->
      <div v-if="hoverTip" class="case-tip" :style="tipStyle" @mouseenter="cancelHide" @mouseleave="hideTip">
        <div v-if="hoverTip.f.steps.length" class="tip-sec">
          <div class="tip-hd">步骤</div>
          <ol><li v-for="(s, i) in hoverTip.f.steps" :key="i">{{ s }}</li></ol>
        </div>
        <div v-if="hoverTip.f.expected.length" class="tip-sec">
          <div class="tip-hd">预期</div>
          <ol><li v-for="(s, i) in hoverTip.f.expected" :key="i">{{ s }}</li></ol>
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
          <button
            class="primary run-btn"
            :disabled="runStore.running || runStore.syncing || runStore.docGenerating"
            @click="runSelected"
          >
            {{ runStore.running ? "执行中…" : (runStore.syncing || runStore.docGenerating) ? "收尾中…（同步/刷新Doc）" : "▶ 执行选中" }}
          </button>
        </div>
      </div>
    </div>

    </div><!-- /场景库 -->

    <!-- ══════ 执行台：实时监控（矩阵 + 进度 + 过程 + 中止）══════ -->
    <div v-show="subTab === 'monitor'" class="monitor-wrap">
      <RunMonitor />
    </div>

    <!-- ══════ 执行记录：完整跑完（未中止）的历史执行台快照，按 run 记录切换回看 ══════ -->
    <div v-show="subTab === 'history'" class="monitor-wrap">
      <RunHistory ref="historyRef" />
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
        <div class="dtitle">上传 APK = 注册被测 App（装机可选）</div>
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
            <div class="muted small">装到哪些设备（可不选；不选则跳过装机直接注册，仅当该包已装在某台在线设备上时可行——只有一台在线会自动用它探测，多台在线且不选会报错）</div>
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
            {{ uploading ? "处理中…" : (uploadSerials.length ? "装机并注册" : "仅注册") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.runner { display: flex; flex-direction: column; height: 100%; }
h2 { margin: 0; font-weight: 500; }

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
.app-col { width: 260px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; }
.applist { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.case-col { flex: 1; min-width: 0; }
.dev-col { width: 260px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; }
.col-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 0.5px solid var(--border); font-size: 13px; font-weight: 500; }
.hd-actions { display: flex; align-items: center; gap: 6px; }
.col-body { flex: 1; overflow: auto; padding: 6px; min-height: 0; }
.empty-hint { padding: 14px 10px; font-size: 12px; line-height: 1.5; }

.app-group { margin-bottom: 2px; }
.app-item { padding: 8px 10px; border-radius: var(--radius); cursor: pointer; display: flex; align-items: flex-start; gap: 6px; }
.app-item:hover { background: var(--surface-1); }
.app-item.on { background: var(--bg-accent); }
.app-caret { flex-shrink: 0; width: 12px; font-size: 10px; line-height: 20px; cursor: pointer; color: var(--text-muted); }
.app-main { flex: 1; min-width: 0; }
.app-name { font-size: 13px; font-weight: 500; display: flex; align-items: center; gap: 6px; }
.app-item.on .app-name { color: var(--text-accent); }
.dot { color: var(--text-accent); font-size: 9px; }
.app-del {
  margin-left: auto; padding: 0 6px; font-size: 12px; line-height: 18px;
  color: var(--text-muted); background: transparent; border: none; border-radius: 4px; cursor: pointer;
}
.app-del:hover { color: var(--text-danger, #d33); background: var(--surface-2, rgba(0,0,0,0.06)); }
.app-sub { font-size: 11px; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.app-version-list { padding: 2px 4px 4px 28px; }
.app-version-empty { padding: 4px 6px; font-size: 11px; }
.app-version-item {
  display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: var(--radius);
  cursor: pointer; font-size: 12px;
}
.app-version-item:hover { background: var(--surface-1); }
.app-version-item.on { background: var(--bg-accent); color: var(--text-accent); }
.app-version-size { margin-left: auto; font-size: 11px; }

.case-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: var(--radius); font-size: 13px; cursor: pointer; }
.case-item:hover { background: var(--surface-1); }
.case-item.locked { opacity: 0.6; cursor: default; }

.case-toolbar { display: flex; gap: 6px; }
.case-id { flex-shrink: 0; }
.case-mod { flex-shrink: 0; font-size: 12px; max-width: 25%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.case-purpose { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.divider { font-size: 11px; padding: 10px 8px 4px; border-top: 0.5px solid var(--border); margin-top: 6px; }

.case-tip {
  position: fixed; z-index: 50; pointer-events: auto;
  width: 620px; max-width: 90vw; max-height: 520px; overflow: auto;
  background: var(--surface-2); border: 0.5px solid var(--border); border-radius: 10px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.22);
  padding: 12px 14px; font-size: 12px; line-height: 1.6; color: var(--text-primary);
}
.tip-sec + .tip-sec { margin-top: 10px; }
.tip-hd { font-size: 11px; font-weight: 600; color: var(--text-accent); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.case-tip ol { margin: 0; padding-left: 18px; }
.case-tip li { margin-bottom: 4px; }
.case-tip li:last-child { margin-bottom: 0; }

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
