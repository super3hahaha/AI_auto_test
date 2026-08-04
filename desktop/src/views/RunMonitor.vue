<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onMounted } from "vue";
import { runStore, labelOf, type CellStatus, type IssueState, type MonitorSource } from "../runStore";
import { api } from "../api";
import { store } from "../store";

defineOptions({ name: "RunMonitor" });

// 数据源：默认实时 runStore；「执行记录」页传入一份只读快照（makeRecordSource）复用同一套渲染。
// running/aborting/syncing/docGenerating 在快照里恒 false → 头部显示「已完成/空闲」、中止按钮禁用。
const props = defineProps<{ source?: MonitorSource | null }>();
const M = props.source ?? runStore;

// 问题清单自动登记状态的中文短标（收尾阶段流转）
function issueLabel(s: IssueState): string {
  return { none: "", registering: "登记中…", registered: "已登记", manual: "人工", skipped: "已跳过" }[s];
}
// 卡片 / 失败摘要上的徽标只显示「还需要处理」或「用户特意选了不一样结果」的状态——已自动登记
// 完成（registered）不再占地方提示；「待人工登记」（manual）继续提醒；「已跳过」（skipped）是
// 用户手动选的，跟默认路径不一样，留个标记方便回头确认当时为什么没登记。
function showIssuePill(s: IssueState): boolean {
  return s === "manual" || s === "skipped";
}
// 收尾流程还没跑到这一格（issue 仍是 "none"）且这一格是失败/需复核 → 可以切换「本条要不要登记」。
// 调试固化脚本时常见：失败是脚本没写好，不是真缺陷，不想每次收尾都自动占用 issues.csv。
function canToggleSkip(status: CellStatus, issue: IssueState): boolean {
  return issue === "none" && (status === "fail" || status === "app_defect" || status === "needs_human");
}

type Filter = "all" | "ok" | "bad";
const filter = ref<Filter>("all");
const eventsBox = ref<HTMLElement | null>(null);

const serials = computed(() => M.serials());
const caseIds = computed(() => M.caseIds());
const hasRun = computed(() => M.cells.length > 0);

// 序列号→别名/型号：矩阵/失败摘要里的 serial 无线连接时是 ip:port，没有这层兜底会直接露出端口号
// （同 Evidence.vue/Runner.vue 的坑，见 docs/gotchas.md）。别名优先，没有就退回型号，再没有才原样显示。
const aliasMap = ref<Record<string, string>>({});
const modelMap = ref<Record<string, string>>({});
function deviceLabel(serial: string): string {
  return aliasMap.value[serial] || modelMap.value[serial] || serial;
}
// 实时过程日志的行文本是 run_flow.py/adbkit 里的固化脚本自己 log() 出来的（形如 "[$S] xxx"），
// $S 就是原始 adb serial（带冒号），不是模板插值拼出来的字段，没法直接换成 deviceLabel(serial)
// ——只能在渲染前对已知 serial 做一次字符串替换。已知 serial 集合=本轮涉及的 serials ∪ 别名/型号
// 表的 key，命中才替换，避免误伤日志正文里凑巧出现的数字串。
const knownSerials = computed(() => {
  const set = new Set<string>(serials.value);
  Object.keys(aliasMap.value).forEach((k) => set.add(k));
  Object.keys(modelMap.value).forEach((k) => set.add(k));
  return [...set];
});
function labelizeText(text: string): string {
  let out = text;
  for (const s of knownSerials.value) {
    if (out.includes(s)) {
      const label = deviceLabel(s);
      if (label !== s) out = out.split(s).join(label);
    }
  }
  return out;
}
// RunMonitor 靠 v-show 常驻挂载（见 Runner.vue「执行台」容器），onMounted 只会跑这一次——
// 场景库设备面板之后点「刷新」查到新型号/别名，只会更新磁盘缓存文件，不会自动触发这里重读。
// 暴露 reload() 给 Runner.vue 在 loadDevices() 里主动调用，两边缓存才能对得上（真实症状：无线设备
// 首次连接时看板还没查到型号显示 ip:port，场景库刷新后型号已知，看板标题栏却仍卡在 ip:port）。
async function loadDeviceLabels() {
  try {
    const kvs = await api.readDeviceAliases();
    aliasMap.value = Object.fromEntries(kvs.map((k) => [k.key, k.value]));
  } catch { /* 别名读不到无妨，退化为显示型号/serial */ }
  try {
    const kvs = await api.readDeviceModelCache();
    modelMap.value = Object.fromEntries(kvs.map((k) => [k.key, k.value]));
  } catch { /* 型号缓存读不到无妨，退化为显示 serial */ }
}
defineExpose({ reload: loadDeviceLabels });
onMounted(loadDeviceLabels);

// 设备面板折叠态（按 serial 记，默认展开；纯 UI 态，不放 runStore）
const collapsedMap = reactive<Record<string, boolean>>({});
function isCollapsed(s: string): boolean {
  return !!collapsedMap[s];
}
function toggleDevice(s: string) {
  collapsedMap[s] = !collapsedMap[s];
}

function cellsOf(s: string) {
  return M.cells.filter((c) => c.serial === s);
}
function doneCountOf(s: string): number {
  return cellsOf(s).filter((c) => c.status !== "waiting" && c.status !== "running" && !c.recording).length;
}
function countsOf(s: string) {
  const c = { ok: 0, bad: 0 };
  for (const cell of cellsOf(s)) {
    if (cell.status === "pass" || cell.status === "healed") c.ok++;
    else if (cell.status === "fail" || cell.status === "app_defect" || cell.status === "needs_human") c.bad++;
  }
  return c;
}

// 设备总耗时：累加该设备所有格子的 elapsed（各格串行执行，求和≈墙钟时间）
function totalElapsedOf(s: string): number {
  return cellsOf(s).reduce((sum, c) => sum + (c.elapsed || 0), 0);
}
// h/min/s 格式化：<60s 只写 s；<1h 写 XminYs；否则写 XhYmZs
function formatDuration(totalSec: number): string {
  const s = Math.round(totalSec);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${h}h${m}m${sec}s` : `${m}min${sec}s`;
}

// 全局失败用例摘要：跨设备汇总，供折叠后也能一眼看到哪些用例炸了
const failedCells = computed(() =>
  M.cells.filter((c) => c.status === "fail" || c.status === "app_defect")
);

const overall = computed(() => {
  if (M.running) return M.aborting ? "中止中…" : "运行中";
  if (!hasRun.value) return "未开始";
  if (M.cells.some((c) => c.status === "aborted")) return "已中止";
  return "已完成";
});

function inFilter(s: CellStatus): boolean {
  if (filter.value === "all") return true;
  if (filter.value === "ok") return s === "pass" || s === "healed";
  if (filter.value === "bad") return s === "fail" || s === "app_defect";
  return true;
}
const counts = computed(() => {
  const c = { ok: 0, bad: 0 };
  for (const cell of M.cells) {
    if (cell.status === "pass" || cell.status === "healed") c.ok++;
    else if (cell.status === "fail" || cell.status === "app_defect" || cell.status === "needs_human") c.bad++;
  }
  return c;
});

// 问题清单自动登记的整体计数（收尾阶段流转），供头部指示用。
// 分母用 M.issueTotal（开始登记时就定住的固定值），不再按"目前处理到第几条"动态数——
// 串行逐条登记时，还没轮到的格子 issue 仍是 "none"，若靠遍历 cells 数分母会出现 1/2→2/3→3/4 的诡异爬升。
const issueStats = computed(() => {
  const s = { total: M.issueTotal, registering: 0, registered: 0, manual: 0, skipped: 0 };
  for (const cell of M.cells) {
    if (cell.issue === "registering") s.registering++;
    else if (cell.issue === "registered") s.registered++;
    else if (cell.issue === "manual") s.manual++;
    else if (cell.issue === "skipped") s.skipped++;
  }
  return s;
});

// 头部收尾阶段指示：登记问题 → 同步表格 → 刷新报告，跑完后停在问题清单结果摘要上
const publishPhase = computed(() => {
  // 串行逐条登记：正在登记的这一条也算进当前位次（1-based），故 +1——否则登记第 1 条时显示 0/N。
  if (issueStats.value.registering > 0) return { cls: "run", text: `登记问题中… ${issueStats.value.registered + issueStats.value.manual + 1}/${issueStats.value.total}` };
  if (M.syncing) return { cls: "run", text: "同步表格中…" };
  if (M.docGenerating) return { cls: "run", text: "刷新报告中…" };
  // 收尾结束：若本轮有失败/需复核用例，把问题清单登记结果留在头部
  if (issueStats.value.total > 0 || issueStats.value.skipped > 0) {
    const parts = [];
    if (issueStats.value.registered) parts.push(`已登记 ${issueStats.value.registered}`);
    if (issueStats.value.manual) parts.push(`待人工 ${issueStats.value.manual}`);
    if (issueStats.value.skipped) parts.push(`已跳过 ${issueStats.value.skipped}`);
    return { cls: issueStats.value.manual ? "warn" : "ok", text: `问题清单：${parts.join(" · ")}` };
  }
  return null;
});

// 右栏「实时过程」：选中某格 → 只看该格日志；否则看全部运行事件（都过一遍 labelizeText 把行里的原始 serial 换成别名/型号）
const shownLines = computed(() => {
  const raw = M.selectedKey
    ? (M.cells.find((c) => M.key(c.serial, c.caseId) === M.selectedKey)?.lines || []).map((t) => ({ text: t, level: "info" as const }))
    : M.events;
  return raw.map((e) => ({ ...e, text: labelizeText(e.text) }));
});
// 「格日志：<serial> / <caseId>」子标题——selectedKey 的 serial 段也要走别名/型号，不能露 ip:port
const selectedKeyLabel = computed(() => {
  if (!M.selectedKey) return "";
  const i = M.selectedKey.indexOf("|");
  if (i < 0) return M.selectedKey;
  return `${deviceLabel(M.selectedKey.slice(0, i))} / ${M.selectedKey.slice(i + 1)}`;
});

function pillClass(s: CellStatus) {
  return {
    "st-pass": s === "pass" || s === "healed",
    "st-fail": s === "fail" || s === "app_defect",
    "st-needs": s === "needs_human",
    "st-run": s === "running",
    "st-wait": s === "waiting",
    "st-abort": s === "aborted",
  };
}

// 点用例卡片后：定位到该格日志里的判定结论，没判定就退化定位关键失败行
const highlightIdx = ref(-1);
let hlTimer: number | undefined;

function findAnchor(lines: string[]): number {
  // judge_result.py 2026-07-22 起改成纯确定性映射，不再调 claude 输出"判定诊断/JUDGE_RESULT"这类
  // 标记；自愈模式(auto_repair.py)的诊断输出走另一套文案，定位失败仍然靠固化脚本自己的 DONE/校验行。
  const idx = lines.findIndex((l) => /校验未通过|DONE（FAILED=[1-9]|✖.*(失败|异常)/.test(l));
  if (idx >= 0) return idx;
  return lines.length ? 0 : -1;
}

function scrollToAnchor() {
  nextTick(() => {
    const idx = findAnchor(shownLines.value.map((e) => e.text));
    highlightIdx.value = idx;
    if (idx < 0 || !eventsBox.value) return;
    eventsBox.value.querySelector(`[data-idx="${idx}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    if (hlTimer) window.clearTimeout(hlTimer);
    hlTimer = window.setTimeout(() => { highlightIdx.value = -1; }, 2200);
  });
}

// ── 卡片「↗」：跳到证据页并定位到这一格的第一项证据 ──
// 轮次：历史快照用它自己记的 runId（可能是已归档的旧轮次），实时源没有这个字段 → 回退到当前批次。
// 「执行记录」页与「执行台」共用本组件，所以这颗按钮两处都有，只是 runId 取法不同。
const evidenceRunId = computed(
  () => props.source?.runId || store.runs.find((r) => r.is_current)?.run_id || ""
);
// 从该格日志里抓出「这一次执行」的证据坐标。固化脚本每次采证都会打出证据文件全路径
// （`[ui] 已保存 …/evidence/<slug>/<ver>/<run_id>/<case>/<serial>/<attempt>/ui/xx.xml`），
// run_id 和 attempt 两段都在里面 → 加上用例、serial 就是四段全齐，能跟证据页**精确**对上，
// 不用靠时间戳猜。`lines` 本来就存进执行记录快照，所以历史旧记录也配得准——实测本机 15 条记录
// 98 格全部精确命中，含 34 格 `meta.runId` 字段加入前存的、光看 meta 根本不知道属于哪一轮的格。
// 用 caseId + 媒体目录名双锚定，避免误抓日志正文里凑巧出现的六位数字；取最后一条匹配。
function evidenceRefOf(serial: string, caseId: string): { runId: string; attempt: string } {
  const lines = M.cell(serial, caseId)?.lines || [];
  const re = new RegExp(`evidence/[^/]+/[^/]+/([^/]+)/${caseId}/[^/]+/(\\d{6})/(?:screenshots|logs|ui)/`);
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = re.exec(lines[i]);
    if (m) return { runId: m[1], attempt: m[2] };
  }
  return { runId: "", attempt: "" };
}
function jumpToEvidence(serial: string, caseId: string) {
  const ref = evidenceRefOf(serial, caseId);
  // 批次优先用日志里抓到的（比 meta.runId 可靠，且覆盖没有那个字段的旧记录）；
  // attempt 精确定位这一次执行；开跑时刻兜底——脚本刚起来就崩、一条证据都没产出时日志里没有
  // 路径可抓，那时只能按时间就近配（见 Evidence.matchAttemptIndex）。
  store.requestEvidence(
    serial,
    caseId,
    ref.runId || evidenceRunId.value,
    M.cell(serial, caseId)?.startedAt || 0,
    ref.attempt
  );
}

function pickCell(serial: string, caseId: string) {
  const k = M.key(serial, caseId);
  const wasSame = M.selectedKey === k;
  M.selectedKey = wasSame ? "" : k;
  if (!wasSame) scrollToAnchor();
}

// 新日志自动滚到底（仅在看「全部运行事件」时；选中单个用例由 scrollToAnchor 控场，不抢着滚底）
watch(
  () => shownLines.value.length,
  () => {
    if (M.selectedKey) return;
    nextTick(() => { if (eventsBox.value) eventsBox.value.scrollTop = eventsBox.value.scrollHeight; });
  }
);
</script>

<template>
  <div class="monitor">
    <!-- 空态 -->
    <div v-if="!hasRun" class="empty card">
      <div class="empty-t">还没有执行任务</div>
      <div class="muted">去「场景库」选好用例和设备，点「执行选中」，这里会实时显示运行过程。</div>
    </div>

    <template v-else>
      <!-- 头部：标题 + 状态 + 进度 + 中止 -->
      <div class="hd">
        <div class="hd-l">
          <span class="title">{{ M.title || "执行" }}</span>
          <span class="badge" :class="{ run: M.running, done: overall === '已完成', abort: overall === '已中止' }">{{ overall }}</span>
          <span class="muted prog">{{ M.doneCount() }}/{{ M.totalCount() }} 格完成</span>
          <span v-if="publishPhase" class="publish-chip" :class="publishPhase.cls">{{ publishPhase.text }}</span>
        </div>
        <button class="abort-btn" :disabled="!M.running || M.aborting" @click="M.abort()">
          {{ M.aborting ? "中止中…" : "中止任务" }}
        </button>
      </div>

      <div class="body">
        <!-- 左：筛选 + 矩阵 -->
        <div class="left card">
          <div class="filters">
            <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ M.totalCount() }}</button>
            <button :class="{ on: filter === 'ok' }" @click="filter = 'ok'">通过 {{ counts.ok }}</button>
            <button :class="{ on: filter === 'bad' }" @click="filter = 'bad'">失败 {{ counts.bad }}</button>
          </div>
          <div v-if="failedCells.length" class="fail-summary">
            <span class="fail-summary-t">⚠ 失败用例摘要（{{ failedCells.length }}）</span>
            <span v-for="fc in failedCells" :key="M.key(fc.serial, fc.caseId)" class="fail-chip-wrap">
              <button class="fail-chip mono" @click="pickCell(fc.serial, fc.caseId)">
                {{ deviceLabel(fc.serial) }} · {{ fc.caseId }}
                <span v-if="showIssuePill(fc.issue)" class="issue-pill" :class="fc.issue">{{ issueLabel(fc.issue) }}</span>
              </button>
              <button
                v-if="canToggleSkip(fc.status, fc.issue)"
                class="skip-toggle"
                :class="{ off: fc.issueSkip }"
                :title="fc.issueSkip ? '点击恢复：收尾时正常登记问题清单' : '点击跳过：收尾时不登记问题清单（调试固化脚本时常用）'"
                @click="M.toggleIssueSkip(fc.serial, fc.caseId)"
              >{{ fc.issueSkip ? "不登记" : "登记" }}</button>
            </span>
          </div>
          <div class="devices-scroll">
            <div v-for="s in serials" :key="s" class="device-panel">
              <div class="device-hd" @click="toggleDevice(s)">
                <span class="chevron">{{ isCollapsed(s) ? "▸" : "▾" }}</span>
                <span class="mono dev-serial" :title="s">{{ deviceLabel(s) }}</span>
                <!-- 分母 = 该设备在 plan 里实际分到的格数（显式分派下各台不同，不能用全局用例数） -->
                <span class="muted dev-prog">{{ doneCountOf(s) }}/{{ cellsOf(s).length }} 完成</span>
                <span v-if="countsOf(s).ok" class="dev-ok">通过 {{ countsOf(s).ok }}</span>
                <span v-if="countsOf(s).bad" class="dev-bad">失败 {{ countsOf(s).bad }}</span>
                <span v-if="totalElapsedOf(s)" class="dev-total-t">总耗时 {{ formatDuration(totalElapsedOf(s)) }}</span>
              </div>
              <div v-show="!isCollapsed(s)" class="device-grid">
                <template v-for="cid in caseIds" :key="cid">
                  <div
                    v-if="M.cell(s, cid)"
                    class="case-card"
                    role="button"
                    tabindex="0"
                    :class="{
                      dim: !inFilter(M.cell(s, cid)!.status),
                      sel: M.selectedKey === M.key(s, cid),
                    }"
                    @click="pickCell(s, cid)"
                    @keydown.enter="pickCell(s, cid)"
                  >
                    <div class="case-top">
                      <span class="case-id mono">{{ cid }}</span>
                      <!-- 还没跑过（等待中）必然没有证据，那时不显示这颗按钮 -->
                      <button
                        v-if="M.cell(s, cid)!.status !== 'waiting'"
                        class="evi-jump"
                        title="去「证据」看这条用例的证据（定位到第一项）"
                        @click.stop="jumpToEvidence(s, cid)"
                      >↗</button>
                    </div>
                    <div class="case-meta">
                      <span class="st-pill" :class="pillClass(M.cell(s, cid)!.status)">{{ labelOf(M.cell(s, cid)!.status) }}</span>
                      <span v-if="M.cell(s, cid)!.issue !== 'none'" class="issue-pill" :class="M.cell(s, cid)!.issue">{{ issueLabel(M.cell(s, cid)!.issue) }}</span>
                      <button
                        v-if="canToggleSkip(M.cell(s, cid)!.status, M.cell(s, cid)!.issue)"
                        class="skip-toggle"
                        :class="{ off: M.cell(s, cid)!.issueSkip }"
                        :title="M.cell(s, cid)!.issueSkip ? '点击恢复：收尾时正常登记问题清单' : '点击跳过：收尾时不登记问题清单（调试固化脚本时常用）'"
                        @click.stop="M.toggleIssueSkip(s, cid)"
                      >{{ M.cell(s, cid)!.issueSkip ? "不登记" : "登记" }}</button>
                      <span v-if="M.cell(s, cid)!.recording" class="et recording">落库中…</span>
                      <span v-else-if="M.cell(s, cid)!.elapsed" class="et">{{ M.cell(s, cid)!.elapsed }}s</span>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
          <div class="foot muted">点用例卡片看该格实时日志；面板标题栏点击折叠/展开整台设备；跑完去「证据」tab 看该用例的截图 / ui / 日志。</div>
        </div>

        <!-- 右：实时过程 -->
        <div class="right card">
          <div class="right-hd">
            <span>实时过程</span>
            <span class="conn" :class="{ live: M.running }">{{ M.running ? "实时连接正常" : "空闲" }}</span>
          </div>
          <div class="right-sub muted">
            {{ M.selectedKey ? `格日志：${selectedKeyLabel}` : "全部运行事件" }}
            <button v-if="M.selectedKey" class="link" @click="M.selectedKey = ''">看全部</button>
          </div>
          <div ref="eventsBox" class="events">
            <div v-for="(e, i) in shownLines" :key="i" class="ev" :class="{ err: e.level === 'error', hl: i === highlightIdx }" :data-idx="i">
              {{ e.text }}
            </div>
            <div v-if="!shownLines.length" class="muted ev-empty">（暂无事件）</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.monitor { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.empty { padding: 40px 24px; text-align: center; }
.empty-t { font-size: 15px; font-weight: 500; margin-bottom: 8px; }

.hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.hd-l { display: flex; align-items: center; gap: 10px; }
.title { font-size: 16px; font-weight: 500; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--surface-2); color: var(--text-secondary); }
.badge.run { background: var(--bg-accent); color: var(--text-accent); }
.badge.done { background: var(--bg-success, rgba(52,199,89,.12)); color: var(--text-success); }
.badge.abort { background: var(--bg-danger); color: var(--text-danger); }
.prog { font-size: 13px; }
/* 头部收尾阶段指示（登记问题→同步→报告，跑完停在问题清单结果摘要） */
.publish-chip { font-size: 12px; padding: 2px 9px; border-radius: 999px; }
.publish-chip.run { background: var(--bg-accent); color: var(--text-accent); }
.publish-chip.ok { background: var(--bg-success, rgba(52,199,89,.12)); color: var(--text-success); }
.publish-chip.warn { background: rgba(255,179,0,.16); color: #9a6700; }
.abort-btn { border: 0.5px solid var(--text-danger); color: var(--text-danger); background: transparent; padding: 6px 14px; border-radius: var(--radius); font-size: 13px; }
.abort-btn:hover:not(:disabled) { background: var(--bg-danger); }
.abort-btn:disabled { opacity: 0.4; border-color: var(--border); color: var(--text-secondary); }

.body { display: flex; gap: 12px; flex: 1; min-height: 0; }
.left { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.right { width: 360px; flex-shrink: 0; display: flex; flex-direction: column; }

.filters { display: flex; gap: 6px; padding: 10px; border-bottom: 0.5px solid var(--border); flex-wrap: wrap; }
.filters button { font-size: 12px; padding: 4px 10px; border-radius: 999px; border: 0.5px solid var(--border); background: transparent; color: var(--text-secondary); }
.filters button.on { background: var(--text-primary, #111); color: var(--bg, #fff); border-color: transparent; }

.fail-summary { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 8px 10px 0; padding: 8px 10px; border-radius: var(--radius); background: var(--bg-danger); }
.fail-summary-t { font-size: 12px; font-weight: 500; color: var(--text-danger); flex-shrink: 0; }
.fail-chip-wrap { display: inline-flex; align-items: center; gap: 3px; }
.fail-chip { font-size: 11px; padding: 2px 8px; border-radius: var(--radius); background: var(--surface-2); color: var(--text-danger); border: 0.5px solid var(--border-danger, var(--text-danger)); cursor: pointer; }
.fail-chip.sm { margin-left: 4px; padding: 1px 7px; }

.devices-scroll { flex: 1; overflow: auto; padding: 8px 10px; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
.device-panel { border: 0.5px solid var(--border); border-radius: 12px; background: var(--surface-2); }
.device-hd { display: flex; align-items: center; gap: 10px; padding: 8px 12px; cursor: pointer; font-size: 13px; }
.chevron { font-size: 11px; color: var(--text-muted); flex-shrink: 0; width: 10px; }
.dev-serial { font-weight: 500; }
.dev-prog { font-size: 12px; margin-left: auto; }
.dev-ok { font-size: 12px; color: var(--text-success); }
.dev-bad { font-size: 12px; color: var(--text-danger); }
.dev-total-t { font-size: 12px; color: var(--text-muted); }

.device-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; padding: 4px 12px 12px; border-top: 0.5px solid var(--border); }
.case-card { text-align: left; border: 0.5px solid var(--border); border-radius: var(--radius); padding: 8px 10px; background: var(--surface-1); cursor: pointer; }
.case-card:hover { border-color: var(--border-strong); }
.case-card.dim { opacity: 0.25; }
.case-card.sel { outline: 2px solid var(--text-accent); outline-offset: 1px; }
.case-top { display: flex; align-items: center; gap: 6px; }
.case-id { font-size: 12px; }
/* 「↗」去证据：常态低对比，悬到卡片上才显眼，别跟状态徽标抢注意力 */
.evi-jump { margin-left: auto; flex-shrink: 0; width: 20px; height: 20px; padding: 0; display: flex; align-items: center; justify-content: center; font-size: 13px; line-height: 1; border: none; border-radius: 5px; background: transparent; color: var(--text-muted); cursor: pointer; }
.case-card:hover .evi-jump { color: var(--text-accent); background: var(--bg-accent); }
.evi-jump:hover { color: var(--text-accent); background: var(--bg-accent); filter: brightness(0.96); }
.case-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.case-meta .et { font-size: 11px; color: var(--text-muted); margin-left: auto; }
.case-meta .et.recording { color: var(--text-accent); }
.st-pill { display: inline-flex; align-items: center; font-size: 11px; padding: 1px 7px; border-radius: 999px; }
.st-wait { background: var(--surface-2); color: var(--text-secondary); }
.st-run { background: var(--bg-accent); color: var(--text-accent); }
.st-pass { background: rgba(52,199,89,.15); color: #1a7f37; }
.st-fail { background: var(--bg-danger); color: var(--text-danger); }
.st-needs { background: rgba(255,179,0,.16); color: #9a6700; }
.st-abort { background: var(--surface-2); color: var(--text-secondary); text-decoration: line-through; }
/* 问题清单自动登记状态徽标（失败摘要栏 chip + 用例卡片「失败」状态右侧都显示） */
.issue-pill { display: inline-flex; align-items: center; font-size: 10px; padding: 1px 6px; border-radius: 999px; }
.issue-pill.registering { background: var(--bg-accent); color: var(--text-accent); }
.issue-pill.registered { background: var(--bg-accent); color: var(--text-accent); }
.issue-pill.manual { background: rgba(255,179,0,.16); color: #9a6700; }
.issue-pill.skipped { background: var(--surface-2); color: var(--text-secondary); }
/* 「本条要不要登记问题清单」切换按钮——收尾流程跑到这一格前都可以点，调试固化脚本时用来
   取消不值得登记的失败。默认（登记）用中性描边；已切到「不登记」用醒目一点的灰底提醒别忘了改回来。 */
.skip-toggle { font-size: 10px; padding: 1px 6px; border-radius: 999px; border: 0.5px solid var(--border); background: transparent; color: var(--text-secondary); cursor: pointer; }
.skip-toggle:hover { background: var(--surface-2); }
.skip-toggle.off { background: var(--surface-2); color: var(--text-primary); border-color: var(--border-strong); text-decoration: line-through; }
.foot { padding: 8px 10px; font-size: 11px; border-top: 0.5px solid var(--border); flex-shrink: 0; }

.right-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 0.5px solid var(--border); font-size: 13px; font-weight: 500; }
.conn { font-size: 11px; color: var(--text-secondary); font-weight: 400; }
.conn.live { color: var(--text-success); }
.right-sub { padding: 6px 12px; font-size: 11px; display: flex; align-items: center; gap: 8px; }
.link { font-size: 11px; color: var(--text-accent); background: none; border: none; padding: 0; cursor: pointer; text-decoration: underline; }
.events { flex: 1; overflow: auto; padding: 6px 12px; min-height: 0; font-size: 12px; line-height: 1.55; }
.ev { padding: 2px 0; color: var(--text-secondary); word-break: break-all; white-space: pre-wrap; border-bottom: 0.5px solid var(--border); }
.ev.err { color: var(--text-danger); }
.ev.hl { background: var(--bg-danger); border-radius: 4px; outline: 1px solid var(--text-danger); outline-offset: -1px; }
.ev-empty { padding: 10px 0; }
</style>
