<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import type { DeviceRow } from "../api";
import {
  missingRerunSerials,
  resolveRerunPlan,
  type RerunPlan,
} from "../rerunPlan";

const props = defineProps<{
  plan: RerunPlan;
  devices: DeviceRow[];
  refreshing?: boolean;
  initialReplacements?: Record<string, string>;
}>();
const emit = defineEmits<{
  (e: "cancel"): void;
  (e: "refresh"): void;
  (e: "confirm", replacements: Record<string, string>): void;
}>();

const replacements = reactive<Record<string, string>>({ ...(props.initialReplacements ?? {}) });
const onlineDevices = computed(() => props.devices.filter((d) => d.state === "device"));
const onlineSerials = computed(() => onlineDevices.value.map((d) => d.serial));
const missing = computed(() => missingRerunSerials(props.plan, onlineSerials.value));

function labelOfDevice(d: DeviceRow): string {
  const name = d.alias || d.model || d.serial;
  return name === d.serial ? name : `${name}（${d.serial}）`;
}

function sourceLabel(serial: string): string {
  return props.plan.labels[serial] || serial;
}

// 候选变化时只清掉已离线的临时选项。若恰好只有一台在线设备则预选，但仍必须由用户点击确认。
watch(
  onlineSerials,
  (serials) => {
    const online = new Set(serials);
    for (const sourceSerial of missing.value) {
      if (replacements[sourceSerial] && !online.has(replacements[sourceSerial])) {
        delete replacements[sourceSerial];
      }
      const initial = props.initialReplacements?.[sourceSerial];
      if (!replacements[sourceSerial] && initial && online.has(initial)) {
        replacements[sourceSerial] = initial;
      }
      if (!replacements[sourceSerial] && serials.length === 1) {
        replacements[sourceSerial] = serials[0];
      }
    }
  },
  { immediate: true }
);

const canConfirm = computed(() => {
  const online = new Set(onlineSerials.value);
  return missing.value.every((sourceSerial) => online.has(replacements[sourceSerial]));
});

const preview = computed(() => {
  if (!canConfirm.value) return null;
  try {
    return resolveRerunPlan(props.plan, onlineSerials.value, replacements);
  } catch {
    return null;
  }
});

function confirmSelection() {
  if (!canConfirm.value) return;
  emit("confirm", { ...replacements });
}
</script>

<template>
  <div class="rerun-overlay" @keydown.esc="emit('cancel')">
    <div class="rerun-dialog card" role="dialog" aria-modal="true" aria-labelledby="rerun-title">
      <div id="rerun-title" class="title">失败重跑</div>
      <p v-if="missing.length" class="intro">
        原失败设备当前未全部连接。你可以连接原设备后刷新，或为离线设备选择本次使用的在线设备。
      </p>
      <p v-else class="intro ok">
        原失败设备已全部重新连接，将继续使用各自的原设备。
      </p>

      <div v-if="missing.length" class="mapping">
        <div class="mapping-head">
          <span>原失败设备</span>
          <span>本次执行设备</span>
        </div>
        <div v-for="serial in missing" :key="serial" class="mapping-row">
          <div>
            <div class="device-name">{{ sourceLabel(serial) }}</div>
            <div v-if="sourceLabel(serial) !== serial" class="mono serial">{{ serial }}</div>
          </div>
          <select v-model="replacements[serial]" :disabled="!onlineDevices.length">
            <option value="" disabled>请选择在线设备</option>
            <option v-for="d in onlineDevices" :key="d.serial" :value="d.serial">
              {{ labelOfDevice(d) }}
            </option>
          </select>
        </div>
      </div>

      <div v-if="!onlineDevices.length && missing.length" class="warning">
        没有任何在线设备，连接或授权设备后请刷新。
      </div>
      <div v-else class="note">
        仅替换未连接的设备，已在线的原设备保持不变。替代设备通过，不代表原设备已复测通过。
      </div>
      <div v-if="preview?.mergedCount" class="merge-note">
        {{ preview.failureCount }} 个失败格将合并为 {{ preview.executionCount }} 次实际执行；同一用例在同一设备上只执行一次，失败来源会完整保留。
      </div>
      <div class="preflight">
        换机前请核对 App 版本、测试素材、账号、系统权限和语言等前置条件；设备在线不代表环境已满足用例要求。
      </div>

      <div class="actions">
        <button @click="emit('cancel')">取消</button>
        <button :disabled="refreshing" @click="emit('refresh')">
          {{ refreshing ? "刷新中…" : "刷新设备" }}
        </button>
        <button class="primary" :disabled="!canConfirm || refreshing" @click="confirmSelection">
          确认，返回场景库
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rerun-overlay {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.45);
}
.rerun-dialog { width: min(680px, 92vw); max-height: 86vh; overflow: auto; padding: 22px 24px; }
.title { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
.intro { margin: 0 0 16px; color: var(--text-secondary); line-height: 1.6; }
.intro.ok { color: var(--text-accent); }
.mapping { border: 0.5px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.mapping-head,
.mapping-row { display: grid; grid-template-columns: minmax(170px, 0.8fr) minmax(260px, 1.2fr); gap: 18px; align-items: center; }
.mapping-head { padding: 8px 12px; background: var(--surface-2); color: var(--text-secondary); font-size: 12px; }
.mapping-row { padding: 12px; border-top: 0.5px solid var(--border); }
.mapping-row select { width: 100%; min-width: 0; }
.device-name { font-size: 13px; font-weight: 500; }
.serial { margin-top: 3px; color: var(--text-secondary); font-size: 11px; word-break: break-all; }
.note,
.warning,
.merge-note,
.preflight { margin-top: 12px; padding: 9px 11px; border-radius: var(--radius); font-size: 12px; line-height: 1.55; }
.note { color: var(--text-secondary); background: var(--surface-2); }
.warning { color: var(--text-danger); background: var(--bg-danger); }
.merge-note { color: var(--text-accent); background: var(--bg-accent); }
.preflight { color: var(--text-secondary); border: 0.5px dashed var(--border); }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
@media (max-width: 640px) {
  .mapping-head { display: none; }
  .mapping-row { grid-template-columns: 1fr; gap: 8px; }
  .actions { flex-wrap: wrap; }
}
</style>
