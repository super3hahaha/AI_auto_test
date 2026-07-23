<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { api, type KV, type StructureRow } from "../api";
import { store } from "../store";
import { openUrl } from "@tauri-apps/plugin-opener";

const summary = ref<KV[]>([]);
const structure = ref<StructureRow[]>([]);
const err = ref("");

// 优先级配色（与执行台一致）；区间如 "P1-P3" 取更高优先级（第一段）着色
function priorityPill(p: string) {
  const head = (p || "").split("-")[0].trim();
  if (head === "P0") return "pill-danger";
  if (head === "P1") return "pill-warning";
  if (head === "P2") return "pill-accent";
  return "pill-muted";
}
// 覆盖用例串 → 数组（去空白）
function caseList(s: string) {
  return s.split(",").map((c) => c.trim()).filter(Boolean);
}
const structTotal = computed(() =>
  structure.value.reduce((n, r) => n + (parseInt(r.count, 10) || 0), 0)
);

// 只把数值型指标做成卡片，其余（阅读规则那些）不展示
const METRIC_KEYS = [
  "总用例数", "已完成", "待执行", "执行中", "通过", "失败", "需复核", "证据条数",
];

const metrics = computed(() =>
  METRIC_KEYS.map((k) => ({ k, v: summary.value.find((s) => s.key === k)?.value ?? "—" }))
);
const curRun = computed(() => store.runs.find((r) => r.is_current) || store.runs[store.runs.length - 1]);

async function load() {
  err.value = "";
  try {
    [summary.value, structure.value] = await Promise.all([
      api.readSummary(store.activeSlug),
      api.readStructure(store.activeSlug),
    ]);
    if (!store.runs.length) await store.loadRuns();
  } catch (e: any) {
    err.value = String(e);
  }
}
watch(() => store.activeSlug, load);
onMounted(load);
</script>

<template>
  <div>
    <div class="hd">
      <h2>概览</h2>
      <button @click="load">刷新</button>
    </div>

    <div v-if="err" class="err">{{ err }}</div>

    <div class="topcard card" v-if="curRun">
      <div>
        <div class="muted small">当前批次</div>
        <div class="title">{{ curRun.title || curRun.run_id }}</div>
        <div class="muted small mono">run_id {{ curRun.run_id }} · {{ curRun.date }}</div>
      </div>
      <div class="links">
        <a v-if="curRun.url" @click="openUrl(curRun.url)">Sheet ↗</a>
        <a v-if="curRun.doc_url" @click="openUrl(curRun.doc_url)">Doc ↗</a>
      </div>
    </div>

    <div class="metrics">
      <div class="metric card" v-for="m in metrics" :key="m.k">
        <div class="mv">{{ m.v }}</div>
        <div class="mk muted">{{ m.k }}</div>
      </div>
    </div>

    <section class="struct" v-if="structure.length">
      <div class="struct-hd">
        <h3>结构视图</h3>
        <span class="muted small">{{ structure.length }} 个模块 · {{ structTotal }} 条用例</span>
      </div>
      <table class="card tbl">
        <thead>
          <tr>
            <th class="col-mod">模块</th>
            <th>测试目的</th>
            <th class="col-cnt">用例数</th>
            <th class="col-case">覆盖用例</th>
            <th class="col-prio">优先级</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in structure" :key="s.module">
            <td class="col-mod strong">{{ s.module }}</td>
            <td class="purpose">{{ s.purpose || "见各用例目标" }}</td>
            <td class="col-cnt num">{{ s.count }}</td>
            <td class="col-case">
              <span class="case mono" v-for="c in caseList(s.cases)" :key="c">{{ c }}</span>
            </td>
            <td class="col-prio">
              <span class="pill" :class="priorityPill(s.priority)">{{ s.priority }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 12px; }
h2 { margin: 0; font-weight: 500; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 10px 12px; border-radius: var(--radius); margin: 10px 0; }
.topcard { display: flex; justify-content: space-between; align-items: center; padding: 16px 18px; margin: 14px 0 10px; }
.title { font-size: 15px; font-weight: 500; margin: 2px 0; }
.small { font-size: 12px; }
.links { display: flex; gap: 14px; font-size: 13px; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
.metric { padding: 14px 16px; }
.mv { font-size: 24px; font-weight: 500; }
.mk { font-size: 13px; margin-top: 2px; }

.struct { margin-top: 22px; }
.struct-hd { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.struct-hd h3 { margin: 0; font-weight: 500; font-size: 15px; }
.small { font-size: 12px; }
.tbl { width: 100%; border-collapse: collapse; overflow: hidden; }
.tbl th, .tbl td { text-align: left; padding: 10px 14px; border-bottom: 0.5px solid var(--border); font-size: 13px; vertical-align: top; }
.tbl th { color: var(--text-secondary); font-weight: 500; font-size: 12px; }
.tbl tbody tr:last-child td { border-bottom: none; }
.strong { font-weight: 500; white-space: nowrap; }
.purpose { color: var(--text-secondary); line-height: 1.5; }
.num { font-variant-numeric: tabular-nums; }
.col-cnt { text-align: center; width: 56px; }
.col-cnt.num { text-align: center; }
.col-prio { width: 76px; }
.col-case { width: 34%; }
.case { display: inline-block; font-size: 12px; padding: 1px 6px; margin: 1px 4px 1px 0; border-radius: 5px; background: var(--bg-subtle, rgba(127,127,127,.1)); color: var(--text-secondary); }
</style>
