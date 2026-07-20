<script setup lang="ts">
import { computed } from "vue";
import { type RunRow } from "../api";
import { store } from "../store";
import { openUrl } from "@tauri-apps/plugin-opener";

const emit = defineEmits<{ "view-evidence": [] }>();

const flat = computed(() => [...store.runs].reverse());

function viewEvidence(r: RunRow) {
  store.selectedRunId = r.run_id;
  emit("view-evidence");
}
</script>

<template>
  <div>
    <div class="hd">
      <h2>看板 / 执行批次</h2>
    </div>
    <p class="muted">一行一个执行批次（run_id）。</p>

    <!-- 平铺批次 -->
    <table class="card tbl">
      <thead><tr><th>run_id</th><th>日期</th><th>标题</th><th></th></tr></thead>
      <tbody>
        <tr v-for="r in flat" :key="r.run_id">
          <td class="mono">{{ r.run_id }} <span v-if="r.is_current" class="pill pill-accent">当前</span></td>
          <td>{{ r.date }}</td>
          <td class="small">{{ r.title }}</td>
          <td class="right">
            <a v-if="r.url" @click="openUrl(r.url)">Sheet ↗</a>
            <button @click="viewEvidence(r)">查看证据</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="!store.runs.length" class="muted card empty">
      还没有任何执行批次（ledger/runs.csv 为空）。开一轮 new_run 后这里会出现。
    </div>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 12px; }
h2 { margin: 0; font-weight: 500; }
.tbl { width: 100%; border-collapse: collapse; margin-top: 12px; overflow: hidden; }
th, td { text-align: left; padding: 9px 14px; border-bottom: 0.5px solid var(--border); font-size: 13px; }
th { color: var(--text-secondary); font-weight: 500; font-size: 12px; }
.mono { font-size: 12px; }
.small { font-size: 12px; }
.right { text-align: right; display: flex; gap: 10px; justify-content: flex-end; align-items: center; }
.empty { padding: 24px; margin-top: 12px; }
</style>
