<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from "vue";
import { api, fileSrc, type EvidenceRow } from "../api";
import { store } from "../store";
import { openUrl } from "@tauri-apps/plugin-opener";

const rows = ref<EvidenceRow[]>([]);
const loading = ref(false);
const err = ref("");
// 选中的「设备+用例」对——同一时间只展开一个用例的证据链；同一用例可能在多台设备上跑，故用 serial 一起做标识
const selDevice = ref("");
const selCase = ref("");
const isExpanded = (serial: string, c: string) => selDevice.value === serial && selCase.value === c;
function toggleCase(serial: string, c: string) {
  if (isExpanded(serial, c)) {
    selDevice.value = "";
    selCase.value = "";
  } else {
    selDevice.value = serial;
    selCase.value = c;
    // 这里显式重置游标，不要挂 watch([selDevice, selCase]) 去做：那样"跳转精确定位到某次 attempt"
    // （consumeJump）刚设好的 currentIndex 会在下一个 flush 被 watcher 的 pickFirst 盖成"最新一次"，
    // 目标不是最新那组时就跳错（真实踩过：RING-SET-01 该去 153510 却停在 180406）。
    pickFirst();
  }
}
const currentIndex = ref(0); // 用下标而非 path 作选中标识——evidence.csv 同路径可重复出现（重跑追加行，decisions #23），path 不唯一会导致方向键卡住
// 跳转（执行台/执行记录卡片「↗」）没落到目标时的提示条文案；空=不显示
const jumpMiss = ref("");
// 已确认打不开的证据文件（图片 onerror / 文本读取失败）。清理页删的是 evidence/<slug>/<ver>/<run_id>
// 整轮物料目录，账本里的 evidence.csv 行不会跟着删——所以清过的旧批次是「条目在、文件没了」，
// 不给提示的话舞台就是一片空白，看着像坏了。按 path 记（同一份文件多行引用也只判一次）。
const missingFiles = ref<Set<string>>(new Set());
function markMissing(path: string) {
  if (missingFiles.value.has(path)) return;
  const s = new Set(missingFiles.value);
  s.add(path);
  missingFiles.value = s;
}
const onlyFail = ref(false);
const typeFilter = ref<"all" | "image" | "text">("all");
const textCache = ref<Record<string, string>>({});

// 按 run_id（YYYYMMDD-HHMM，可直接字符串比较）倒序，最新的批次排最前
const sortedRuns = computed(() => [...store.runs].sort((a, b) => b.run_id.localeCompare(a.run_id)));

async function loadEvidence() {
  if (!store.selectedRunId) return;
  loading.value = true;
  err.value = "";
  try {
    rows.value = await api.readEvidence(store.activeSlug, store.selectedRunId);
    // 有跳转请求就按它定位；没有（或没落到目标）才走默认回落
    if (!consumeJump()) {
      ensureSelection();
      pickFirst();
    }
  } catch (e: any) {
    err.value = String(e);
    rows.value = [];
  } finally {
    loading.value = false;
  }
}

function pass(r: EvidenceRow) {
  if (onlyFail.value && r.result !== "失败") return false;
  if (typeFilter.value === "image" && !r.is_image) return false;
  if (typeFilter.value === "text" && r.is_image) return false;
  return true;
}
// 当前选中「设备+用例」下、通过筛选的证据行（按采集顺序）
const items = computed(() =>
  rows.value.filter((r) => (serialOf(r) || "-") === selDevice.value && r.case_id === selCase.value && pass(r))
);

// 证据路径两种历史布局都存在（decisions：早期无 attempt 段）：
//   新：.../<caseId>/<serial>/<attempt>/{screenshots|logs|ui}/x
//   旧：.../<caseId>/<serial>/{screenshots|logs|ui}/x
// 不能用「从媒体目录往前数第 N 段」——旧布局会整体错位一位、把 caseId 当成 serial。
// 改为锚定已知的 case_id：serial=其后一段，attempt=再后一段（若已是媒体目录则视为无 attempt）。
const MEDIA = new Set(["screenshots", "logs", "ui"]);
function segs(r: EvidenceRow): { serial: string; attempt: string } {
  const parts = r.path.split("/");
  const ci = parts.indexOf(r.case_id);
  if (ci >= 0 && ci + 1 < parts.length && !MEDIA.has(parts[ci + 1])) {
    const serial = parts[ci + 1];
    const next = parts[ci + 2] || "";
    return { serial, attempt: MEDIA.has(next) ? "" : next };
  }
  // 兜底：找不到 caseId 段时退回媒体目录锚点（假定新布局）
  const i = parts.findIndex((p) => MEDIA.has(p));
  return { serial: i >= 2 ? parts[i - 2] : "", attempt: i >= 1 ? parts[i - 1] : "" };
}
const attemptOf = (r: EvidenceRow) => segs(r).attempt;
const serialOf = (r: EvidenceRow) => segs(r).serial;

// 证据路径里的 serial 段是 tools/adbkit.py `_safe()` 清洗过的文件名片段（冒号等特殊字符换成
// 下划线，如 "192.168.209.239:5555" → "192.168.209.239_5555"），而 aliases/型号缓存的 key
// 是原始 adb serial（带冒号）——两边不清洗成同一种形态就永远查不到，无线设备退化显示成
// "ip_port" 这种半吊子文本。这里镜像同一条清洗规则，把两张表都按"清洗后 key"多建一份索引。
function sanitizeSerial(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]/g, "_");
}
function buildLookup(kvs: { key: string; value: string }[]): Record<string, string> {
  const m: Record<string, string> = {};
  for (const { key, value } of kvs) {
    m[key] = value;
    m[sanitizeSerial(key)] = value;
  }
  return m;
}
// 序列号→别名映射（config/device_aliases.json），把 serial 显示成友好名
const aliasMap = ref<Record<string, string>>({});
// 序列号/ip:port→型号缓存（config/device_info_cache.json），别名没登记时兜底显示型号，
// 避免无线设备（serial 形如 192.168.x.x:5555）直接露出 ip:port
const modelMap = ref<Record<string, string>>({});
const deviceLabel = (serial: string) =>
  serial === "-" ? "(未知设备)" : aliasMap.value[serial] || modelMap.value[serial] || serial;

// 设备分组的收起状态：记住被收起的 serial（默认全展开）
const collapsedDevices = reactive(new Set<string>());
function toggleDevice(serial: string) {
  if (collapsedDevices.has(serial)) collapsedDevices.delete(serial);
  else collapsedDevices.add(serial);
}

// 组内最新采集时间（"YYYY-MM-DD HH:MM"，字典序即时间序）。列错位的脏行/空值直接忽略。
const TS_RE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/;
function latestTs(list: EvidenceRow[]): string {
  let best = "";
  for (const r of list) {
    const t = (r.collected_at || "").trim();
    if (TS_RE.test(t) && t > best) best = t;
  }
  return best;
}

// 按 attempt 拆分子分组——同一设备上同一用例可能重复执行多次
function splitByAttempt(list: EvidenceRow[]): { attempt: string; ts: string; rows: EvidenceRow[] }[] {
  const m = new Map<string, EvidenceRow[]>();
  for (const r of list) {
    const key = attemptOf(r) || "-";
    if (!m.has(key)) m.set(key, []);
    m.get(key)!.push(r);
  }
  const groups = [...m.entries()].map(([attempt, rows]) => ({ attempt, rows, ts: latestTs(rows) }));
  // 最新一次执行排在最前。attempt 目录名只有 HHMMSS 没有日期，按名字排会跨天错序
  // （今天 09:29 那次会被排到昨天 20:01 之后），所以优先按采集时间倒序，
  // 两边都拿不到时间时才退回目录名；"-"（旧布局无 attempt 段）恒排最后。
  groups.sort((a, b) => {
    if (a.attempt === "-") return 1;
    if (b.attempt === "-") return -1;
    if (a.ts && b.ts) return a.ts === b.ts ? b.attempt.localeCompare(a.attempt) : b.ts.localeCompare(a.ts);
    if (a.ts) return -1;
    if (b.ts) return 1;
    return b.attempt.localeCompare(a.attempt);
  });
  return groups;
}

// 侧栏三层树：设备(最外) → 用例 → attempt。不同设备跑的用例不一样，故用例挂在各自设备下。
const deviceGroups = computed(() => {
  // serial → (case_id → 行)
  const dev = new Map<string, Map<string, EvidenceRow[]>>();
  for (const r of rows.value) {
    if (!pass(r)) continue;
    const s = serialOf(r) || "-";
    if (!dev.has(s)) dev.set(s, new Map());
    const cm = dev.get(s)!;
    if (!cm.has(r.case_id)) cm.set(r.case_id, []);
    cm.get(r.case_id)!.push(r);
  }
  // 设备名排序（"-" 兜底键排最后），保证展示稳定
  return [...dev.entries()]
    .sort((a, b) => {
      if (a[0] === "-") return 1;
      if (b[0] === "-") return -1;
      return deviceLabel(a[0]).localeCompare(deviceLabel(b[0]));
    })
    .map(([serial, cm]) => ({
      serial,
      cases: [...cm.entries()].map(([caseId, list]) => ({
        caseId,
        count: list.length,
        attempts: splitByAttempt(list),
      })),
    }));
});

// 选中项失效（首次加载 / 筛选后当前设备+用例已无证据）时，回落到第一台设备的第一个用例
function ensureSelection() {
  const g = deviceGroups.value;
  const dev = g.find((d) => d.serial === selDevice.value);
  const stillValid = dev && dev.cases.some((c) => c.caseId === selCase.value);
  if (stillValid) return;
  if (g.length && g[0].cases.length) {
    selDevice.value = g[0].serial;
    selCase.value = g[0].cases[0].caseId;
  } else {
    selDevice.value = "";
    selCase.value = "";
  }
}

// ── 执行台/执行记录用例卡片「↗」发来的跳转请求 ──
// 命中就展开该「设备+用例」、停在第一项证据并把侧栏滚过去；返回 false 表示没落到目标（调用方
// 回落到默认选中）。批次锚点已由 store.requestEvidence 切好，这里只负责在当前批次里找格子。
const sideScroll = ref<HTMLElement | null>(null);

// 把跳转请求对上某一组 attempt。证据路径里的 attempt 段是该格 run_flow 启动时刻的 HHMMSS
// （tools/run_flow.py），**逐格各不相同**——所以整轮的 run_id、执行记录 id 都配不上（后者只等于
// 第一格）。两级配对：
//   1) 精确：发起方从该格日志的证据路径里抓到的 attempt 段（`/<case>/<serial>/<attempt>/ui/…`），
//      直接按名字相等取组。日志随执行记录一起存盘，所以历史旧记录也走这条。
//   2) 就近：脚本刚起来就崩、一条证据都没产出 → 日志里没有路径可抓，退回按该格开跑时刻找最近的组。
// 返回组下标；-1 = 配不上（调用方退回最新一次并提示）。
// 就近为什么要留容差：run_flow 取的是 python 进程起来之后的时刻，前端记的是 invoke 之前，差几百
// 毫秒、跨秒边界差 1~2s 是常态，不能要求相等；同一格两次重跑至少隔几十秒，120s 窗口不会串。
const ATTEMPT_MATCH_TOLERANCE_S = 120;
function matchAttemptIndex(
  cs: { attempts: { attempt: string; ts: string }[] },
  startedAt: number,
  attempt: string
): number {
  if (attempt) {
    const exact = cs.attempts.findIndex((g) => g.attempt === attempt);
    if (exact >= 0) return exact;
    return -1; // 抓到了 attempt 名却在这份 evidence.csv 里找不到 → 那次的证据行没了，别偷偷显示别的
  }
  if (!startedAt) return 0; // 既没 attempt 也没开跑时刻（更早的旧记录）→ 最新一次
  const d = new Date(startedAt);
  const p = (n: number) => String(n).padStart(2, "0");
  const day = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  const want = d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds();
  let best = -1;
  let bestDiff = Infinity;
  cs.attempts.forEach((g, i) => {
    const m = /^(\d{2})(\d{2})(\d{2})$/.exec(g.attempt);
    if (!m) return; // "-"（旧布局无 attempt 段）不参与配对
    if (g.ts && g.ts.slice(0, 10) !== day) return; // attempt 只有 HHMMSS，跨天会撞名——用组内采集日期排掉
    const diff = Math.abs(+m[1] * 3600 + +m[2] * 60 + +m[3] - want);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  });
  return bestDiff <= ATTEMPT_MATCH_TOLERANCE_S ? best : -1;
}

function consumeJump(): boolean {
  const j = store.evidenceJump;
  if (!j) return false;
  store.evidenceJump = null; // 一次性信号，不管命中与否都消费掉，避免筛选/换批次时又跳回来
  // 证据路径里的 serial 段是 adbkit `_safe()` 清洗过的（冒号→下划线），跳转请求带的是原始 adb
  // serial——两边都过一遍同一条清洗规则才匹配得上，否则无线设备（192.168.x.x:5555）永远落空。
  const want = sanitizeSerial(j.serial);
  const dev = deviceGroups.value.find((d) => sanitizeSerial(d.serial) === want);
  const cs = dev?.cases.find((c) => c.caseId === j.caseId);
  if (!dev || !cs) {
    jumpMiss.value =
      `没找到「${deviceLabel(want)} / ${j.caseId}」的证据条目：证据可能已被清除（「清理」页把整轮物料移进过废纸篓）、` +
      `这一格没产出证据，或它不属于当前选中的批次（${store.selectedRunId || "—"}）——可换上面的批次再看。`;
    return false;
  }
  collapsedDevices.delete(dev.serial); // 目标设备若被收起，先展开
  selDevice.value = dev.serial;
  selCase.value = cs.caseId;
  // 对准发起方那一次执行的 attempt；配不上就退回最新一次，并说清"看的不是你点的那次"
  const attIdx = matchAttemptIndex(cs, j.startedAt, j.attempt);
  currentIndex.value = firstIndex(attIdx >= 0 ? attIdx : 0);
  const which = j.attempt
    ? `attempt ${j.attempt}`
    : j.startedAt
    ? `${new Date(j.startedAt).toLocaleTimeString("zh-CN", { hour12: false })} 开跑那次`
    : "那一次执行";
  jumpMiss.value =
    attIdx >= 0
      ? ""
      : `这一批证据里没有${which}的记录，已改显示最近一次（attempt ${cs.attempts[0]?.attempt || "—"}）` +
        `——那次的证据可能已被清除，或不属于当前选中的批次（${store.selectedRunId || "—"}）。`;
  // 侧栏几十条用例时目标可能在滚动区外，滚过去才看得见「现在选中的是哪条」
  nextTick(() => {
    sideScroll.value
      ?.querySelector(`[data-case="${dev.serial}/${cs.caseId}"]`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
  return true;
}

const current = computed(() => items.value[Math.min(currentIndex.value, items.value.length - 1)]);

// ── 文本证据（主要是 run_flow.py 登记的 99-run-log 整份流程日志）的关键行定位 ──
// 固化脚本 log 出来的失败根因（「严重异常：…」这类）以前只在执行台「实时过程」那一栏、跑完就没了；
// 现在整份日志作为一条 logs 证据落库，这里负责把它渲染成逐行、把关键行标红并自动滚过去——
// 否则几百行日志里那一句根因根本找不到。正则与 tools/run_flow.py 的 KEY_LINE_RE 同一套口径（改一处记得同步）。
const KEY_LINE_RE = /严重异常|校验未通过|不一致|✖|未见|异常退出|命中崩溃|FAILED=[1-9]/;
const textBox = ref<HTMLElement | null>(null);
const hlLine = ref(-1);
const keyCursor = ref(0);
let hlTimer: number | undefined;

const textLines = computed(() => {
  const c = current.value;
  if (!c || c.is_image) return [];
  return (textCache.value[c.path] ?? "").split("\n");
});
const keyLines = computed(() =>
  textLines.value.reduce<number[]>((acc, l, i) => (KEY_LINE_RE.test(l) ? (acc.push(i), acc) : acc), [])
);
const keyLineSet = computed(() => new Set(keyLines.value));

// 定位到第 n 条关键行（越界回绕），高亮 2.2s 后褪掉
function jumpToKey(n: number) {
  const list = keyLines.value;
  if (!list.length) return;
  keyCursor.value = ((n % list.length) + list.length) % list.length;
  const line = list[keyCursor.value];
  hlLine.value = line;
  nextTick(() => {
    textBox.value?.querySelector(`[data-line="${line}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
  if (hlTimer) window.clearTimeout(hlTimer);
  hlTimer = window.setTimeout(() => { hlLine.value = -1; }, 2200);
}
// 换证据/内容读到后自动停在第一条关键行（没有关键行就停在开头，不动）
watch(textLines, () => {
  hlLine.value = -1;
  keyCursor.value = 0;
  if (keyLines.value.length) jumpToKey(0);
});

// 「第一项证据」= 侧栏第 attIdx 组 attempt 的第一条（默认第 0 组，即最新一次执行），不是 items[0]。
// items 跟着 evidence.csv 的采集顺序（最老在前），而侧栏 attempt 分组是按采集时间倒序排的
// （最新那次在最上面，见 splitByAttempt）——两边方向相反，直接取下标 0 会停在「最老那次执行」
// 的第一条：舞台上是几小时前那轮的截图、侧栏高亮也跑到下面那一组去，跟"最新排最前"的直觉相反。
function firstIndex(attIdx = 0): number {
  const cs = caseGroupOf(selDevice.value, selCase.value);
  const first = cs?.attempts[attIdx]?.rows[0];
  const i = first ? items.value.indexOf(first) : -1;
  return i >= 0 ? i : 0; // 兜底：选中项还没落定/该组被筛掉时回到开头
}
function caseGroupOf(serial: string, caseId: string) {
  return deviceGroups.value.find((d) => d.serial === serial)?.cases.find((c) => c.caseId === caseId);
}
function pickFirst() {
  currentIndex.value = firstIndex(); // 侧栏点选用例：没有"哪一次执行"的线索，停在最新一次
}
function step(delta: number) {
  const n = items.value.length;
  if (!n) return;
  currentIndex.value = (currentIndex.value + delta + n) % n;
}

function onKey(e: KeyboardEvent) {
  if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
  else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
}

// 注意：selDevice/selCase 不挂 watch 重置游标（见 toggleCase 里的说明），改由各修改点显式调用。
// 筛选变化可能让当前选中的设备+用例没证据了，回落一下再重置游标
watch([onlyFail, typeFilter], () => {
  ensureSelection();
  pickFirst();
});
watch(() => store.selectedRunId, () => loadEvidence());
// 本组件已挂载时又收到跳转请求就地消费。当前走不到这条路（Evidence 不在 keep-alive 名单里，
// 每次进 tab 都是新挂载 → 由 onMounted 的 loadEvidence 消费），但哪天把它也保活了就要靠这条。
// `!loading` 是为了避开与换批次的竞争：切了批次时上面那条 watch 已经在重载（loading=true），
// 这时手里的 rows 还是旧批次的，就地找必然落空、误报"没找到"——交给重载结束时消费才对。
watch(() => store.evidenceJump, (v) => { if (v && !loading.value) consumeJump(); });
// 换 App：批次锚点已在 store.setActive 里重置；这里保证清空旧 App 证据
watch(() => store.activeSlug, () => {
  rows.value = [];
  loadEvidence();
});
watch(current, async (c) => {
  if (c && !c.is_image && textCache.value[c.path] === undefined) {
    try { textCache.value[c.path] = await api.readTextFile(c.path); }
    catch (e: any) {
      // 读不到基本就是文件没了（清理页把整轮证据物料移进过废纸篓），标记成缺失走统一提示；
      // 原始错误一起留着，遇到权限之类的别的原因也能看出来。
      markMissing(c.path);
      textCache.value[c.path] = String(e);
    }
  }
});

onMounted(async () => {
  if (!store.runs.length) await store.loadRuns();
  try {
    const kvs = await api.readDeviceAliases();
    aliasMap.value = buildLookup(kvs);
  } catch {
    /* 别名读不到无妨，退化为显示型号/serial */
  }
  try {
    const kvs = await api.readDeviceModelCache();
    modelMap.value = buildLookup(kvs);
  } catch {
    /* 型号缓存读不到无妨，退化为显示 serial */
  }
  window.addEventListener("keydown", onKey);
  await loadEvidence();
});
onUnmounted(() => window.removeEventListener("keydown", onKey));

const selRun = computed(() => store.selectedRun());
</script>

<template>
  <div class="evi">
    <!-- 顶部：批次选择 -->
    <div class="topbar">
      <div class="sel">
        <label class="muted">执行批次</label>
        <select v-model="store.selectedRunId">
          <option v-for="r in sortedRuns" :key="r.run_id" :value="r.run_id">
            {{ r.run_id }}{{ r.is_current ? "（当前）" : "" }} · {{ r.date }}
          </option>
        </select>
      </div>
      <div class="links" v-if="selRun">
        <a v-if="selRun.url" @click="openUrl(selRun.url)">Sheet ↗</a>
        <a v-if="selRun.doc_url" @click="openUrl(selRun.doc_url)">Doc ↗</a>
      </div>
    </div>

    <!-- 跳转没落到目标（证据被清过 / 该格没产出 / 不在当前批次）——不能静默不动，否则用户以为按钮坏了 -->
    <div v-if="jumpMiss" class="jump-miss">
      <span>⚠ {{ jumpMiss }}</span>
      <button class="miss-close" @click="jumpMiss = ''">✕</button>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-else-if="loading" class="muted pad">加载证据…</div>
    <div v-else-if="!rows.length" class="muted pad">这个批次还没有证据（evidence.csv 为空或未找到归档）。</div>

    <div v-else class="body">
      <!-- 左：设备分组 → 用例 → attempt → 证据列表 -->
      <div class="side card">
        <div class="side-scroll" ref="sideScroll">
          <template v-for="dev in deviceGroups" :key="dev.serial">
            <div class="device-hd" @click="toggleDevice(dev.serial)">
              <span class="dev-caret">{{ collapsedDevices.has(dev.serial) ? "▸" : "▾" }}</span>
              <span class="dev-name">{{ deviceLabel(dev.serial) }}</span>
              <span class="dev-count muted">{{ dev.cases.length }} 例</span>
            </div>
            <template v-if="!collapsedDevices.has(dev.serial)">
              <template v-for="cs in dev.cases" :key="dev.serial + '/' + cs.caseId">
                <div
                  class="case-hd"
                  :class="{ on: isExpanded(dev.serial, cs.caseId) }"
                  :data-case="dev.serial + '/' + cs.caseId"
                  @click="toggleCase(dev.serial, cs.caseId)"
                >
                  <span class="case-name">{{ cs.caseId }}</span>
                  <button class="expand-btn" @click.stop="toggleCase(dev.serial, cs.caseId)">
                    {{ isExpanded(dev.serial, cs.caseId) ? "∨" : "∧" }}
                  </button>
                </div>
                <template v-if="isExpanded(dev.serial, cs.caseId)">
                  <template v-for="g in cs.attempts" :key="g.attempt">
                    <div class="attempt-hd muted">
                      attempt {{ g.attempt }}
                      <span v-if="g.ts">· {{ g.ts.slice(5) }}</span>
                      · {{ g.rows.length }} 项
                    </div>
                    <div
                      v-for="r in g.rows"
                      :key="items.indexOf(r)"
                      class="evi-item"
                      :class="{ on: items.indexOf(r) === currentIndex }"
                      @click="currentIndex = items.indexOf(r)"
                    >
                      <span class="step">{{ r.step || "(无步骤)" }}</span>
                      <span v-if="r.is_key" class="pill pill-accent">★</span>
                      <span class="etype muted">{{ r.is_image ? "img" : "txt" }}</span>
                    </div>
                  </template>
                </template>
              </template>
            </template>
          </template>
        </div>
        <div class="filters">
          <div class="seg">
            <button :class="{ on: typeFilter === 'all' }" @click="typeFilter = 'all'">全部</button>
            <button :class="{ on: typeFilter === 'image' }" @click="typeFilter = 'image'">截图</button>
            <button :class="{ on: typeFilter === 'text' }" @click="typeFilter = 'text'">文本</button>
          </div>
          <label class="chk"><input type="checkbox" v-model="onlyFail" />只看失败</label>
        </div>
      </div>

      <!-- 右：画廊 -->
      <div class="gallery">
        <div class="stage card">
          <template v-if="current">
            <!-- 文件已不在（清理页把整轮物料移进过废纸篓）：图片会 onerror、文本会读失败，
                 统一显示成一块说明，而不是空白舞台/一行看不懂的报错。 -->
            <div v-if="missingFiles.has(current.path)" class="gone">
              <div class="gone-t">证据文件已不存在</div>
              <div class="gone-d muted">
                账本里还留着这条记录，但文件读不到了——大概是「清理」页把这一轮的证据物料移进过废纸篓
                （删除走系统废纸篓，还没清空的话可以捞回来）。
              </div>
              <div class="gone-p mono muted">{{ current.path }}</div>
            </div>
            <img
              v-else-if="current.is_image"
              :src="fileSrc(current.abs_path)"
              class="shot"
              @error="markMissing(current.path)"
            />
            <div v-else-if="textCache[current.path] === undefined" class="text mono muted">读取中…</div>
            <div v-else ref="textBox" class="text mono">
              <div
                v-for="(ln, i) in textLines"
                :key="i"
                class="tline"
                :class="{ key: keyLineSet.has(i), hl: i === hlLine }"
                :data-line="i"
              >{{ ln }}</div>
            </div>
            <button v-if="items.length > 1" class="navbtn left" @click="step(-1)">‹</button>
            <button v-if="items.length > 1" class="navbtn right" @click="step(1)">›</button>
            <div class="counter">← → 方向键切换 · {{ currentIndex + 1 }} / {{ items.length }}</div>
          </template>
          <div v-else class="muted pad">该用例在当前筛选下没有证据。</div>
        </div>

        <div class="meta card" v-if="current">
          <div class="meta-row">
            <span class="step-name">{{ current.step }}</span>
            <span
              class="pill"
              :class="current.result === '通过' ? 'pill-success' : current.result === '失败' ? 'pill-danger' : 'pill-muted'"
              v-if="current.result"
              >{{ current.result }}</span
            >
            <span class="pill pill-accent" v-if="current.is_key">★ 关键</span>
            <span class="etype muted">{{ current.etype }}</span>
            <span class="muted device" v-if="serialOf(current)">📱 {{ deviceLabel(serialOf(current)) }}</span>
            <span class="muted attempt" v-if="attemptOf(current)">attempt {{ attemptOf(current) }}</span>
            <!-- 文本证据（流程日志）里的关键行：点一次跳下一条，日志几百行时靠它找根因 -->
            <button v-if="keyLines.length" class="keybtn" @click="jumpToKey(keyCursor + 1)">
              ⚠ 关键行 {{ keyCursor + 1 }}/{{ keyLines.length }} · 定位下一条
            </button>
            <span class="muted time">{{ current.collected_at }}</span>
          </div>
          <div class="assertion">断言：{{ current.assertion || "（无）" }}</div>
        </div>

        <!-- 缩略图条 -->
        <div class="thumbs" v-if="current">
          <div
            v-for="(r, i) in items"
            :key="'t' + i"
            class="thumb"
            :class="{ on: i === currentIndex }"
            @click="currentIndex = i"
          >
            <span v-if="missingFiles.has(r.path)" class="txt-thumb mono gone-thumb" title="文件已不存在">✕</span>
            <img v-else-if="r.is_image" :src="fileSrc(r.abs_path)" @error="markMissing(r.path)" />
            <span v-else class="txt-thumb mono">TXT</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evi { display: flex; flex-direction: column; height: 100%; }
.topbar { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.sel { display: flex; align-items: center; gap: 8px; }
.sel select { min-width: 320px; }
.links { display: flex; gap: 14px; font-size: 13px; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 10px 12px; border-radius: var(--radius); }
/* 跳转落空提示条 */
.jump-miss { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; padding: 8px 12px; border-radius: var(--radius); background: rgba(255,179,0,.14); color: #9a6700; font-size: 12px; line-height: 1.6; }
.miss-close { margin-left: auto; flex-shrink: 0; background: transparent; border: none; color: inherit; font-size: 12px; padding: 0 2px; cursor: pointer; }
/* 文件已被清除的舞台兜底 */
.gone { text-align: center; padding: 28px 24px; max-width: 460px; }
.gone-t { font-size: 14px; font-weight: 500; margin-bottom: 8px; }
.gone-d { font-size: 12px; line-height: 1.7; }
.gone-p { font-size: 11px; margin-top: 10px; word-break: break-all; }
.gone-thumb { color: var(--text-muted); }
.pad { padding: 24px 4px; }
.body { display: flex; gap: 12px; flex: 1; min-height: 0; }

.side { width: 210px; flex-shrink: 0; display: flex; flex-direction: column; padding: 8px; }
.side-scroll { flex: 1; overflow: auto; }
/* 最外层：设备 */
.device-hd { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; padding: 6px 8px; cursor: pointer; border-radius: var(--radius); color: var(--text-primary); margin-top: 6px; }
.device-hd:hover { background: var(--surface-1); }
.device-hd .dev-caret { font-size: 10px; flex-shrink: 0; color: var(--text-secondary); width: 12px; text-align: center; }
.device-hd .dev-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.device-hd .dev-count { font-size: 10.5px; flex-shrink: 0; }
/* 二层：用例（缩进挂在设备下） */
.case-hd { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 500; color: var(--text-secondary); padding: 5px 8px 5px 22px; cursor: pointer; border-radius: var(--radius); }
.case-hd.on { color: var(--text-accent); }
.case-hd .case-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.expand-btn { flex-shrink: 0; width: 18px; height: 18px; padding: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; line-height: 1; border-radius: 4px; color: inherit; background: transparent; border: none; }
.expand-btn:hover { background: var(--surface-1); }
.attempt-hd { font-size: 10.5px; padding: 6px 8px 2px 34px; letter-spacing: 0.02em; }
.evi-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px 5px 34px; font-size: 12px; border-radius: var(--radius); cursor: pointer; color: var(--text-secondary); }
.evi-item:hover { background: var(--surface-1); }
.evi-item.on { background: var(--bg-accent); color: var(--text-accent); }
.evi-item .step { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evi-item .etype { font-size: 11px; }
.filters { border-top: 0.5px solid var(--border); padding-top: 8px; margin-top: 6px; display: flex; flex-direction: column; gap: 8px; }
.seg { display: flex; gap: 2px; }
.seg button { flex: 1; padding: 4px 6px; font-size: 12px; border-radius: 6px; }
.seg button.on { background: var(--bg-accent); color: var(--text-accent); border-color: var(--border-accent); }
.chk { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); }

.gallery { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.stage { flex: 1; min-height: 220px; display: flex; align-items: center; justify-content: center; position: relative; overflow: hidden; }
.shot { max-width: 100%; max-height: 100%; object-fit: contain; }
.text { max-width: 100%; max-height: 100%; overflow: auto; padding: 16px; font-size: 12px; white-space: pre-wrap; word-break: break-all; align-self: stretch; margin: 0; }
/* 逐行渲染（而非整块 pre）：流程日志要能按行标红/滚动定位 */
.tline { min-height: 1.5em; line-height: 1.5; padding: 0 3px; border-radius: 3px; }
.tline.key { color: var(--text-danger); background: var(--bg-danger); }
.tline.hl { outline: 1px solid var(--text-danger); outline-offset: -1px; }
.keybtn { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 0.5px solid var(--text-danger); color: var(--text-danger); background: transparent; cursor: pointer; }
.keybtn:hover { background: var(--bg-danger); }
.navbtn { position: absolute; top: 50%; transform: translateY(-50%); width: 30px; height: 30px; border-radius: 50%; background: var(--surface-1); font-size: 18px; line-height: 1; padding: 0; display: flex; align-items: center; justify-content: center; }
.navbtn.left { left: 10px; }
.navbtn.right { right: 10px; }
.counter { position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--text-muted); background: var(--surface-1); border: 0.5px solid var(--border); padding: 2px 10px; border-radius: 20px; }
.meta { padding: 10px 14px; }
.meta-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.step-name { font-weight: 500; font-size: 13px; }
.etype { font-size: 11px; }
.device { font-size: 11px; }
.attempt { font-size: 11px; }
.time { font-size: 11px; margin-left: auto; }
.assertion { font-size: 12px; color: var(--text-secondary); line-height: 1.6; }
/* 缩略图条：高度写死（46 缩略图 + 10 横向滚动条 + 2 余量）+ 禁掉纵向溢出，两条都不能省。
   本来是 height:auto + 只写 overflow-x:auto，在 WebKit（Tauri 用的 WKWebView）下会抖：
   ① overflow-x 一旦非 visible，overflow-y 就从 visible 计算成 auto（CSS 规范）；
   ② 自动高度先按内容算成 48（46+2），而 ::-webkit-scrollbar 是占位滚动条，横向那条要吃 10px，
      剩 38px 装不下 46px 的缩略图 → 连纵向滚动条一起冒出来（就是条尾那根竖条）；
   ③ 等截图 onload 拿到内在尺寸触发下一轮布局，WebKit 才把自动高度回修成 58。
   于是切进证据页后（截图走 asset:// 要几百毫秒~几秒才到齐）这条高度突然 +10px、舞台 -10px、
   大图重新 fit，就是那一下"小抖动/像重绘了一下"。写死高度后整条尺寸与图片加载完全解耦。 */
.thumbs { display: flex; align-items: center; gap: 6px; flex-shrink: 0; height: 58px; overflow-x: auto; overflow-y: hidden; padding-bottom: 2px; }
.thumb { width: 72px; height: 46px; flex-shrink: 0; border: 0.5px solid var(--border); border-radius: 6px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center; background: var(--surface-1); }
.thumb.on { border: 2px solid var(--border-accent); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.txt-thumb { font-size: 11px; color: var(--text-muted); }
</style>
