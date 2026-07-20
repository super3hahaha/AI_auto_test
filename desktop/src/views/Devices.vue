<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { api, type DeviceRow } from "../api";
import { store } from "../store";

const devices = ref<DeviceRow[]>([]);
const loading = ref(false);
const err = ref("");
const msg = ref("");

async function load() {
  loading.value = true;
  err.value = "";
  try {
    devices.value = await api.listDevices(store.activeSlug);
  } catch (e: any) {
    err.value = String(e);
  } finally {
    loading.value = false;
  }
}

const STATE_LABELS: Record<string, string> = {
  device: "已连接",
  offline: "离线",
  unauthorized: "未授权",
  absent: "未连接",
};
function stateLabel(state: string) {
  return STATE_LABELS[state] || state;
}
function statePill(state: string) {
  if (state === "device") return "pill-success";
  if (state === "absent") return "pill-muted";
  return "pill-warning";
}

async function setDefault(serial: string) {
  msg.value = "";
  try {
    await api.setTargetSerial(store.activeSlug, serial);
    msg.value = `已设为默认目标设备（写入 ${store.activeSlug} 的 target.serial）：${serial}`;
    await load();
  } catch (e: any) {
    err.value = String(e);
  }
}

// 别名编辑（增/改）
const editingSerial = ref<string | null>(null);
const editAlias = ref("");
function startEdit(d: DeviceRow) {
  err.value = "";
  editingSerial.value = d.serial;
  editAlias.value = d.alias;
}
function cancelEdit() {
  editingSerial.value = null;
}
async function saveEdit(serial: string) {
  try {
    await api.upsertDeviceAlias(serial, editAlias.value.trim());
    editingSerial.value = null;
    msg.value = `已更新别名：${serial}`;
    await load();
  } catch (e: any) {
    err.value = String(e);
  }
}

// 删除别名登记（不影响物理设备连接，只是这台设备从「已知设备」里移除/清空别名）
async function removeDevice(d: DeviceRow) {
  if (!confirm(`确认删除设备登记 ${d.alias || d.serial}？\n仅清除别名登记，不影响设备物理连接。`)) return;
  try {
    await api.deleteDeviceAlias(d.serial);
    msg.value = `已删除设备登记：${d.serial}`;
    await load();
  } catch (e: any) {
    err.value = String(e);
  }
}

// 新增设备登记
const showAdd = ref(false);
const newSerial = ref("");
const newAlias = ref("");
async function addDevice() {
  err.value = "";
  if (!newSerial.value.trim()) {
    err.value = "序列号不能为空";
    return;
  }
  try {
    await api.upsertDeviceAlias(newSerial.value.trim(), newAlias.value.trim());
    msg.value = `已添加设备登记：${newSerial.value.trim()}`;
    newSerial.value = "";
    newAlias.value = "";
    showAdd.value = false;
    await load();
  } catch (e: any) {
    err.value = String(e);
  }
}

// 导出/导入设备别名登记
async function exportDevices() {
  err.value = "";
  const path = await api.pickExportDevicesPath();
  if (!path) return;
  try {
    const n = await api.exportDeviceAliases(path);
    msg.value = `已导出 ${n} 条设备登记到 ${path}`;
  } catch (e: any) {
    err.value = String(e);
  }
}
async function importDevices() {
  err.value = "";
  const path = await api.pickImportDevicesPath();
  if (!path) return;
  try {
    const n = await api.importDeviceAliases(path);
    msg.value = `已导入 ${n} 条设备登记`;
    await load();
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
      <h2>设备</h2>
      <button @click="load">刷新</button>
      <button @click="showAdd = !showAdd">{{ showAdd ? "取消添加" : "添加设备" }}</button>
      <button @click="exportDevices">导出</button>
      <button @click="importDevices">导入</button>
    </div>
    <p class="muted">选一台设为默认目标（写回当前 App 的 <span class="mono">target.serial</span>），执行台与主循环默认用它。别名登记存在 <span class="mono">config/device_aliases.json</span>，跨 App 共享。</p>

    <div v-if="showAdd" class="card add-form">
      <input v-model="newSerial" placeholder="序列号（adb devices 可查）" class="mono" />
      <input v-model="newAlias" placeholder="别名（可空）" />
      <button @click="addDevice">保存</button>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>
    <div v-if="loading" class="muted">读取 adb devices…</div>

    <div v-else-if="!devices.length" class="muted card empty">
      没有登记过的设备。确认设备已连接、已开 USB 调试，<span class="mono">adb devices</span> 能看到，或点「添加设备」手动登记。
    </div>

    <table v-else class="card tbl">
      <thead>
        <tr><th>序列号</th><th>别名</th><th>状态</th><th>型号</th><th>系统</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="d in devices" :key="d.serial">
          <td class="mono">{{ d.serial }}</td>
          <td>
            <template v-if="editingSerial === d.serial">
              <input v-model="editAlias" class="inline-input" @keyup.enter="saveEdit(d.serial)" @keyup.esc="cancelEdit" />
              <button class="mini" @click="saveEdit(d.serial)">保存</button>
              <button class="mini" @click="cancelEdit">取消</button>
            </template>
            <template v-else>
              {{ d.alias || "—" }}
              <button class="mini" @click="startEdit(d)">编辑</button>
            </template>
          </td>
          <td>
            <span class="pill" :class="statePill(d.state)">{{ stateLabel(d.state) }}</span>
          </td>
          <td>{{ d.model || "—" }}</td>
          <td>{{ d.os_version ? `Android ${d.os_version}` : "—" }}</td>
          <td class="right">
            <span v-if="d.is_default" class="pill pill-accent">当前默认</span>
            <button v-else-if="d.state === 'device'" @click="setDefault(d.serial)">设为默认</button>
            <button class="mini danger" @click="removeDevice(d)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 12px; }
h2 { margin: 0; font-weight: 500; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 10px 12px; border-radius: var(--radius); margin: 10px 0; }
.ok { color: var(--text-success); background: var(--bg-success); padding: 10px 12px; border-radius: var(--radius); margin: 10px 0; font-size: 13px; }
.empty { padding: 24px; margin-top: 12px; }
.tbl { width: 100%; border-collapse: collapse; margin-top: 12px; overflow: hidden; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 0.5px solid var(--border); font-size: 13px; }
th { color: var(--text-secondary); font-weight: 500; font-size: 12px; }
tr:last-child td { border-bottom: none; }
.right { text-align: right; white-space: nowrap; }
.add-form { display: flex; gap: 8px; padding: 12px 14px; margin-top: 12px; align-items: center; }
.add-form input { flex: 0 0 auto; }
.inline-input { width: 140px; }
.mini { font-size: 12px; padding: 2px 8px; margin-left: 6px; }
.mini.danger { color: var(--text-danger); }
</style>
