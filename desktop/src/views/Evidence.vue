<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { api, fileSrc, type EvidenceRow, type RunRow } from "../api";
import { store } from "../store";
import { openUrl } from "@tauri-apps/plugin-opener";

const rows = ref<EvidenceRow[]>([]);
const loading = ref(false);
const err = ref("");
const selectedCase = ref("");
const currentIndex = ref(0); // 用下标而非 path 作选中标识——evidence.csv 同路径可重复出现（重跑追加行，decisions #23），path 不唯一会导致方向键卡住
const onlyKey = ref(false);
const typeFilter = ref<"all" | "image" | "text">("all");
const textCache = ref<Record<string, string>>({});

// 批次按看板(sheet_id)分组
const grouped = computed(() => {
  const m = new Map<string, { title: string; runs: RunRow[] }>();
  for (const r of store.runs) {
    const g = m.get(r.sheet_id) || { title: r.title || r.sheet_id || "(无标题)", runs: [] };
    g.runs.push(r);
    m.set(r.sheet_id, g);
  }
  return [...m.values()];
});

async function loadEvidence() {
  if (!store.selectedRunId) return;
  loading.value = true;
  err.value = "";
  try {
    rows.value = await api.readEvidence(store.activeSlug, store.selectedRunId);
    const cs = cases.value;
    if (cs.length && !cs.includes(selectedCase.value)) selectedCase.value = cs[0];
    pickFirst();
  } catch (e: any) {
    err.value = String(e);
    rows.value = [];
  } finally {
    loading.value = false;
  }
}

const cases = computed(() => [...new Set(rows.value.map((r) => r.case_id))]);

function pass(r: EvidenceRow) {
  if (onlyKey.value && !r.is_key) return false;
  if (typeFilter.value === "image" && !r.is_image) return false;
  if (typeFilter.value === "text" && r.is_image) return false;
  return true;
}
// 当前用例下、通过筛选的证据行（按采集顺序）
const items = computed(() => rows.value.filter((r) => r.case_id === selectedCase.value && pass(r)));

// attempt 段（从路径里抠出来做分组标签）：.../<serial>/<attempt>/screenshots/x
function attemptOf(r: EvidenceRow): string {
  const parts = r.path.split("/");
  const i = parts.findIndex((p) => p === "screenshots" || p === "logs" || p === "ui");
  return i > 0 ? parts[i - 1] : "";
}

// 按 attempt 拆分子分组——同一用例可能重复执行多次（同一 attempt 内步骤才应视为一次完整证据链）
const attemptGroups = computed(() => {
  const m = new Map<string, EvidenceRow[]>();
  for (const r of items.value) {
    const key = attemptOf(r) || "-";
    if (!m.has(key)) m.set(key, []);
    m.get(key)!.push(r);
  }
  // attempt 倒序：最新一次执行排在最前（"-" 兜底键排最后）
  return [...m.entries()].sort((a, b) => {
    if (a[0] === "-") return 1;
    if (b[0] === "-") return -1;
    return b[0].localeCompare(a[0]);
  });
});

const current = computed(() => items.value[Math.min(currentIndex.value, items.value.length - 1)]);

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

watch([selectedCase, onlyKey, typeFilter], () => pickFirst());
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
          <optgroup v-for="g in grouped" :key="g.title" :label="g.title">
            <option v-for="r in g.runs" :key="r.run_id" :value="r.run_id">
              {{ r.run_id }}{{ r.is_current ? "（当前）" : "" }} · {{ r.date }}
            </option>
          </optgroup>
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
      <!-- 左：用例分组 + 证据列表 -->
      <div class="side card">
        <div class="side-scroll">
          <template v-for="c in cases" :key="c">
            <div class="case-hd" :class="{ on: c === selectedCase }" @click="selectedCase = c">
              {{ c }}
            </div>
            <template v-if="c === selectedCase">
              <template v-for="[attempt, group] in attemptGroups" :key="attempt">
                <div class="attempt-hd muted">attempt {{ attempt }} · {{ group.length }} 项</div>
                <div
                  v-for="r in group"
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
        </div>
        <div class="filters">
          <div class="seg">
            <button :class="{ on: typeFilter === 'all' }" @click="typeFilter = 'all'">全部</button>
            <button :class="{ on: typeFilter === 'image' }" @click="typeFilter = 'image'">截图</button>
            <button :class="{ on: typeFilter === 'text' }" @click="typeFilter = 'text'">文本</button>
          </div>
          <label class="chk"><input type="checkbox" v-model="onlyKey" />只看关键</label>
        </div>
      </div>

      <!-- 右：画廊 -->
      <div class="gallery">
        <div class="stage card">
          <template v-if="current">
            <img v-if="current.is_image" :src="fileSrc(current.abs_path)" class="shot" />
            <pre v-else class="text mono">{{ textCache[current.path] ?? "读取中…" }}</pre>
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
            <span class="muted attempt" v-if="attemptOf(current)">attempt {{ attemptOf(current) }}</span>
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
.case-hd { font-size: 12px; font-weight: 500; color: var(--text-secondary); padding: 6px 8px; cursor: pointer; border-radius: var(--radius); margin-top: 4px; }
.case-hd.on { color: var(--text-accent); }
.attempt-hd { font-size: 10.5px; padding: 6px 8px 2px 16px; letter-spacing: 0.02em; }
.evi-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px 5px 16px; font-size: 12px; border-radius: var(--radius); cursor: pointer; color: var(--text-secondary); }
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
.navbtn { position: absolute; top: 50%; transform: translateY(-50%); width: 30px; height: 30px; border-radius: 50%; background: var(--surface-1); font-size: 18px; line-height: 1; padding: 0; display: flex; align-items: center; justify-content: center; }
.navbtn.left { left: 10px; }
.navbtn.right { right: 10px; }
.counter { position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--text-muted); background: var(--surface-1); border: 0.5px solid var(--border); padding: 2px 10px; border-radius: 20px; }
.meta { padding: 10px 14px; }
.meta-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
.step-name { font-weight: 500; font-size: 13px; }
.etype { font-size: 11px; }
.attempt { font-size: 11px; }
.time { font-size: 11px; margin-left: auto; }
.assertion { font-size: 12px; color: var(--text-secondary); line-height: 1.6; }
.thumbs { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; }
.thumb { width: 72px; height: 46px; flex-shrink: 0; border: 0.5px solid var(--border); border-radius: 6px; overflow: hidden; cursor: pointer; display: flex; align-items: center; justify-content: center; background: var(--surface-1); }
.thumb.on { border: 2px solid var(--border-accent); }
.thumb img { width: 100%; height: 100%; object-fit: cover; }
.txt-thumb { font-size: 11px; color: var(--text-muted); }
</style>
