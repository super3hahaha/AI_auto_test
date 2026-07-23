<script setup lang="ts">
import { ref, computed } from "vue";
import { confirm } from "@tauri-apps/plugin-dialog";
import { api } from "../api";
import { store } from "../store";
import { makeRecordSource, type MonitorSource, type RunRecordMeta } from "../runStore";
import RunMonitor from "./RunMonitor.vue";

// 执行记录页：持久化保存的「完整执行完毕」的执行台快照。布局与执行台一致（内嵌 RunMonitor），
// 区别是数据源是一份保存下来的记录（有内容），可按 run 记录 id 切换。中止的轮次不落记录。
defineOptions({ name: "RunHistory" });

const records = ref<RunRecordMeta[]>([]);
const selectedId = ref("");
const source = ref<MonitorSource | null>(null);
const loading = ref(false);
const err = ref("");

const selectedMeta = computed(() => records.value.find((r) => r.id === selectedId.value));

async function reload() {
  if (!store.activeSlug) {
    records.value = [];
    selectedId.value = "";
    source.value = null;
    return;
  }
  loading.value = true;
  err.value = "";
  try {
    records.value = await api.listRunRecords(store.activeSlug);
    if (records.value.length) {
      // 保留当前选中；失效/首次则落到最新一条（列表已按时间倒序）
      if (!selectedId.value || !records.value.some((r) => r.id === selectedId.value)) {
        selectedId.value = records.value[0].id;
      }
      await openRecord(selectedId.value);
    } else {
      selectedId.value = "";
      source.value = null;
    }
  } catch (e: any) {
    err.value = String(e);
  } finally {
    loading.value = false;
  }
}

async function openRecord(id: string) {
  selectedId.value = id;
  err.value = "";
  try {
    const rec = await api.readRunRecord(store.activeSlug, id);
    source.value = makeRecordSource(rec);
  } catch (e: any) {
    err.value = String(e);
    source.value = null;
  }
}

async function removeRecord() {
  const id = selectedId.value;
  if (!id) return;
  const ok = await confirm("删除这条执行记录？只删本机保存的这份快照，不影响证据/账本/线上表格。", {
    title: "确认删除执行记录",
    kind: "warning",
  });
  if (!ok) return;
  try {
    await api.deleteRunRecord(store.activeSlug, id);
    selectedId.value = "";
    await reload();
  } catch (e: any) {
    err.value = String(e);
  }
}

function fmtTime(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function optionLabel(r: RunRecordMeta): string {
  const bad = r.bad + r.needs;
  return `${fmtTime(r.startedAt)} · ${r.title} · 通过 ${r.ok}${bad ? ` · 失败/需人工 ${bad}` : ""}`;
}

// 父组件（Runner）切到本子 tab 时会调 reload()
defineExpose({ reload });
</script>

<template>
  <div class="history">
    <!-- 顶部：记录选择条（按 run 记录 id 切换）+ 摘要 + 删除 -->
    <div class="hist-bar card">
      <div class="bar-l">
        <span class="bar-title">执行记录</span>
        <select
          v-if="records.length"
          class="rec-select"
          :value="selectedId"
          @change="openRecord(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="r in records" :key="r.id" :value="r.id">{{ optionLabel(r) }}</option>
        </select>
        <span class="muted count">共 {{ records.length }} 条</span>
      </div>
      <div class="bar-r" v-if="selectedMeta">
        <span class="mono rec-id">{{ selectedMeta.id }}</span>
        <span v-if="selectedMeta.brain" class="tag">自愈</span>
        <span class="muted meta-sub">{{ selectedMeta.deviceCount }} 设备 × {{ selectedMeta.caseCount }} 用例</span>
        <button class="del-btn" @click="removeRecord">删除本条</button>
      </div>
    </div>

    <div v-if="err" class="err">{{ err }}</div>

    <!-- 空态 -->
    <div v-if="!loading && !records.length" class="empty card">
      <div class="empty-t">还没有执行记录</div>
      <div class="muted">
        执行台完整跑完一轮（未中止）后，会自动把该轮的执行台快照存到这里，可按 run 记录切换回看。
      </div>
    </div>

    <!-- 记录内容：复用执行台（RunMonitor）布局，数据源为保存的快照。切 id 用 key 重挂，本地选中态归零 -->
    <div v-else-if="source" class="monitor-wrap">
      <RunMonitor :key="selectedId" :source="source" />
    </div>
  </div>
</template>

<style scoped>
/* 与执行台一致：在 Runner 的 .monitor-wrap（flex 行）里铺满整行，空状态卡才会贴边而非被压成内容宽度 */
.history { flex: 1; min-width: 0; display: flex; flex-direction: column; height: 100%; min-height: 0; }
.hist-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; margin-bottom: 12px; flex-shrink: 0; }
.bar-l { display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1; }
.bar-title { font-size: 14px; font-weight: 500; flex-shrink: 0; }
.rec-select { flex: 1; min-width: 0; max-width: 560px; font-size: 12px; padding: 5px 8px; }
.count { font-size: 12px; flex-shrink: 0; }
.bar-r { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.rec-id { font-size: 12px; color: var(--text-secondary); }
.tag { font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--bg-accent); color: var(--text-accent); }
.meta-sub { font-size: 12px; }
.del-btn { border: 0.5px solid var(--text-danger); color: var(--text-danger); background: transparent; padding: 5px 12px; border-radius: var(--radius); font-size: 12px; }
.del-btn:hover { background: var(--bg-danger); }

.err { color: var(--text-danger); background: var(--bg-danger); padding: 8px 12px; border-radius: var(--radius); margin-bottom: 8px; font-size: 13px; }
.empty { padding: 40px 24px; text-align: center; }
.empty-t { font-size: 15px; font-weight: 500; margin-bottom: 8px; }

.monitor-wrap { flex: 1; min-height: 0; display: flex; }
.monitor-wrap :deep(.monitor) { flex: 1; min-width: 0; }
</style>
