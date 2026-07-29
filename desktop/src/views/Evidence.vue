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
  }
}
const currentIndex = ref(0); // 用下标而非 path 作选中标识——evidence.csv 同路径可重复出现（重跑追加行，decisions #23），path 不唯一会导致方向键卡住
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
    ensureSelection();
    pickFirst();
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

function pickFirst() {
  currentIndex.value = 0;
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

watch([selDevice, selCase], () => pickFirst());
// 筛选变化可能让当前选中的设备+用例没证据了，回落一下再重置游标
watch([onlyFail, typeFilter], () => {
  ensureSelection();
  pickFirst();
});
watch(() => store.selectedRunId, () => loadEvidence());
// 换 App：批次锚点已在 store.setActive 里重置；这里保证清空旧 App 证据
watch(() => store.activeSlug, () => {
  rows.value = [];
  loadEvidence();
});
watch(current, async (c) => {
  if (c && !c.is_image && textCache.value[c.path] === undefined) {
    try { textCache.value[c.path] = await api.readTextFile(c.path); }
    catch (e: any) { textCache.value[c.path] = "（读不到内容：" + e + "）"; }
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

    <div v-if="err" class="err">{{ err }}</div>
    <div v-else-if="loading" class="muted pad">加载证据…</div>
    <div v-else-if="!rows.length" class="muted pad">这个批次还没有证据（evidence.csv 为空或未找到归档）。</div>

    <div v-else class="body">
      <!-- 左：设备分组 → 用例 → attempt → 证据列表 -->
      <div class="side card">
        <div class="side-scroll">
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
            <img v-if="current.is_image" :src="fileSrc(current.abs_path)" class="shot" />
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
            <img v-if="r.is_image" :src="fileSrc(r.abs_path)" />
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
.thumbs { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
.thumb { width: 72px; height: 46px; flex-shrink: 0; border: 0.5px solid var(--border); border-radius: 6px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center; background: var(--surface-1); }
.thumb.on { border: 2px solid var(--border-accent); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.txt-thumb { font-size: 11px; color: var(--text-muted); }
</style>
