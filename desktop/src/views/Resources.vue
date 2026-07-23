<script setup lang="ts">
import { ref, onMounted } from "vue";
import { api, type ResourceFile, type TextResource } from "../api";

// ── 文件资源（assets/，所有 App 共用；固化脚本用相对路径 assets/<文件名> 引用）──
const resourceFiles = ref<ResourceFile[]>([]);
const resErr = ref("");
const uploadingRes = ref(false);

async function loadResources() {
  try {
    resourceFiles.value = await api.listResourceFiles();
  } catch (e: any) {
    resErr.value = String(e);
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

async function uploadResource() {
  resErr.value = "";
  try {
    const p = await api.pickResourceFile();
    if (!p) return;
    uploadingRes.value = true;
    await api.uploadResourceFile(p);
    await loadResources();
  } catch (e: any) {
    resErr.value = String(e);
  } finally {
    uploadingRes.value = false;
  }
}

async function removeResource(name: string) {
  resErr.value = "";
  try {
    await api.deleteResourceFile(name);
    await loadResources();
  } catch (e: any) {
    resErr.value = String(e);
  }
}

// ── 文本资源（config/text_resources.json，key-value，固化脚本按 key 取值）──
const textResources = ref<TextResource[]>([]);
const textErr = ref("");
const newKey = ref("");
const newValue = ref("");
const savingText = ref(false);

async function loadTextResources() {
  try {
    textResources.value = await api.listTextResources();
  } catch (e: any) {
    textErr.value = String(e);
  }
}

async function addTextResource() {
  textErr.value = "";
  const key = newKey.value.trim();
  if (!key) { textErr.value = "key 不能为空"; return; }
  savingText.value = true;
  try {
    await api.upsertTextResource(key, newValue.value);
    newKey.value = "";
    newValue.value = "";
    await loadTextResources();
  } catch (e: any) {
    textErr.value = String(e);
  } finally {
    savingText.value = false;
  }
}

async function editTextResource(item: TextResource, value: string) {
  textErr.value = "";
  try {
    await api.upsertTextResource(item.key, value);
    await loadTextResources();
  } catch (e: any) {
    textErr.value = String(e);
  }
}

async function removeTextResource(key: string) {
  textErr.value = "";
  try {
    await api.deleteTextResource(key);
    await loadTextResources();
  } catch (e: any) {
    textErr.value = String(e);
  }
}

onMounted(() => { loadResources(); loadTextResources(); });
</script>

<template>
  <div class="resources">
    <p class="muted sub">
      文件资源存到 <span class="mono">assets/</span>，固化脚本用文件名引用；文本资源存到
      <span class="mono">config/text_resources.json</span>，固化脚本用 key 取值（<span class="mono">tools/_appctx.py</span> 的
      <span class="mono">get_text_resource(key)</span>）。
    </p>
    <div class="res-cols">
      <!-- ── 左：文件资源 ── -->
      <div class="col card resbox">
        <div class="col-hd">
          <span>文件</span>
          <div class="hd-actions">
            <button class="sm" @click="loadResources">刷新</button>
            <button class="primary sm" :disabled="uploadingRes" @click="uploadResource">+ 上传文件</button>
          </div>
        </div>
        <div class="col-body">
          <div v-if="resErr" class="err">{{ resErr }}</div>
          <div v-if="!resourceFiles.length" class="muted empty-hint">
            还没有素材文件。上传后存到项目 <span class="mono">assets/</span> 目录，固化脚本按文件名引用。
          </div>
          <div v-for="f in resourceFiles" :key="f.name" class="res-item">
            <span class="mono res-name" :title="f.name">{{ f.name }}</span>
            <span class="muted res-size">{{ formatSize(f.size) }}</span>
            <button class="sm danger" title="删除" @click="removeResource(f.name)">✕</button>
          </div>
        </div>
      </div>

      <!-- ── 右：文本资源（key-value）── -->
      <div class="col card textbox">
        <div class="col-hd">
          <span>文本</span>
          <button class="sm" @click="loadTextResources">刷新</button>
        </div>
        <div class="col-body">
          <div v-if="textErr" class="err">{{ textErr }}</div>
          <div class="kv-new">
            <input v-model="newKey" class="kv-key mono" placeholder="key" @keyup.enter="addTextResource" />
            <input v-model="newValue" class="kv-val mono" placeholder="value" @keyup.enter="addTextResource" />
            <button class="primary sm" :disabled="savingText" @click="addTextResource">+ 新建</button>
          </div>
          <div v-if="!textResources.length" class="muted empty-hint">
            还没有文本资源。新建后固化脚本可按 key 取值。
          </div>
          <div v-for="t in textResources" :key="t.key" class="kv-item">
            <span class="mono kv-key-label" :title="t.key">{{ t.key }}</span>
            <input
              class="kv-val-input mono"
              :value="t.value"
              @change="editTextResource(t, ($event.target as HTMLInputElement).value)"
            />
            <button class="sm danger" title="删除" @click="removeTextResource(t.key)">✕</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.resources { flex: 1; min-height: 0; display: flex; flex-direction: column; height: 100%; }
.sub { margin: 4px 0 10px; }
.res-cols { display: flex; gap: 12px; flex: 1; min-height: 0; }
.res-cols .col { flex: 1; min-width: 0; min-height: 0; }
.col { display: flex; flex-direction: column; min-height: 0; }
.resbox { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.textbox { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.col-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 0.5px solid var(--border); font-size: 13px; font-weight: 500; }
.hd-actions { display: flex; align-items: center; gap: 6px; }
.col-body { flex: 1; overflow: auto; padding: 6px; min-height: 0; }
.empty-hint { padding: 14px 10px; font-size: 12px; line-height: 1.5; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 8px 12px; border-radius: var(--radius); margin: 6px 0; font-size: 13px; }

.res-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px; font-size: 12px; border-radius: var(--radius); }
.res-item:hover { background: var(--surface-1); }
.res-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.res-size { flex-shrink: 0; font-size: 11px; }
button.danger { color: var(--text-danger); background: transparent; border: none; cursor: pointer; padding: 2px 4px; }
button.danger:hover { background: var(--bg-danger); border-radius: var(--radius); }

.kv-new { display: flex; gap: 6px; padding: 8px; border-bottom: 0.5px solid var(--border); }
.kv-key { width: 110px; flex-shrink: 0; padding: 5px 8px; font-size: 12px; }
.kv-val { flex: 1; min-width: 0; padding: 5px 8px; font-size: 12px; }
.kv-item { display: flex; align-items: center; gap: 6px; padding: 5px 8px; font-size: 12px; border-radius: var(--radius); }
.kv-item:hover { background: var(--surface-1); }
.kv-key-label { width: 110px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kv-val-input { flex: 1; min-width: 0; padding: 4px 6px; font-size: 12px; background: transparent; border: 0.5px solid transparent; }
.kv-val-input:hover, .kv-val-input:focus { border-color: var(--border); background: var(--surface-2); }

.pill.sm, .sm { font-size: 11px; }
button.sm { padding: 3px 8px; }
</style>
