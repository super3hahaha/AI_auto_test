<script setup lang="ts">
import { ref, computed, onMounted, watch } from "vue";
import { api, type KV } from "../api";
import { store } from "../store";
import { openUrl } from "@tauri-apps/plugin-opener";

const summary = ref<KV[]>([]);
const err = ref("");

// 只把数值型指标做成卡片，其余（阅读规则那些）不展示
const METRIC_KEYS = [
  "总用例数", "已完成", "待执行", "执行中", "通过", "失败", "阻塞", "覆盖缺口", "需复核", "证据条数",
];

const metrics = computed(() =>
  METRIC_KEYS.map((k) => ({ k, v: summary.value.find((s) => s.key === k)?.value ?? "—" }))
);
const meta = computed(() => {
  const get = (k: string) => summary.value.find((s) => s.key === k)?.value ?? "";
  return { created: get("创建日期"), scope: get("本轮范围"), doc: get("Google Doc 图文报告") };
});
const curRun = computed(() => store.runs.find((r) => r.is_current) || store.runs[store.runs.length - 1]);

async function load() {
  err.value = "";
  try {
    summary.value = await api.readSummary(store.activeSlug);
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

    <div class="scope muted" v-if="meta.scope">本轮范围：{{ meta.scope }}</div>

    <div class="metrics">
      <div class="metric card" v-for="m in metrics" :key="m.k">
        <div class="mv">{{ m.v }}</div>
        <div class="mk muted">{{ m.k }}</div>
      </div>
    </div>
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
.scope { font-size: 13px; margin-bottom: 12px; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
.metric { padding: 14px 16px; }
.mv { font-size: 24px; font-weight: 500; }
.mk { font-size: 13px; margin-top: 2px; }
</style>
