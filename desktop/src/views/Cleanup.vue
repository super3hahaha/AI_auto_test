<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { api, type CleanupReport, type CleanupItem } from "../api";

// 历史文件清理：扫描五类堆积产物 → 结构化列出（名称/大小/时间/受保护）→ 勾选后移进系统废纸篓。
// 删除不是硬删除，走系统废纸篓（macOS 可设 30 天自动清除，误删可捞回）。
const report = ref<CleanupReport | null>(null);
const loading = ref(false);
const err = ref("");
const busy = ref(false);
const msg = ref("");

// 选中集合：以 rel_path 为键
const selected = ref<Set<string>>(new Set());
// 折叠状态：以 category.key 为键，true=折叠
const collapsed = ref<Record<string, boolean>>({});
// 二次确认弹窗
const confirming = ref(false);

async function load() {
  loading.value = true;
  err.value = "";
  msg.value = "";
  try {
    report.value = await api.scanCleanup();
    // 默认折叠没有内容或体积很小的类；「开发构建缓存」默认折叠（默认不勾）
    const c: Record<string, boolean> = {};
    for (const cat of report.value.categories) {
      c[cat.key] = cat.items.length === 0 || cat.key === "build";
    }
    collapsed.value = c;
    selected.value = new Set();
  } catch (e: any) {
    err.value = String(e);
  } finally {
    loading.value = false;
  }
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtTime(secs: number): string {
  if (!secs) return "";
  const d = new Date(secs * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function toggleItem(rel: string) {
  const s = new Set(selected.value);
  if (s.has(rel)) s.delete(rel);
  else s.add(rel);
  selected.value = s;
}

// 组的三态：0=全未选 1=部分 2=全选
function groupState(items: CleanupItem[]): number {
  if (!items.length) return 0;
  const n = items.filter((i) => selected.value.has(i.rel_path)).length;
  if (n === 0) return 0;
  return n === items.length ? 2 : 1;
}

function toggleGroup(items: CleanupItem[]) {
  const s = new Set(selected.value);
  const allOn = groupState(items) === 2;
  for (const i of items) {
    if (allOn) s.delete(i.rel_path);
    else s.add(i.rel_path);
  }
  selected.value = s;
}

// 汇总
const totalSelectable = computed(
  () => report.value?.categories.reduce((sum, c) => sum + c.total_size, 0) ?? 0
);
const selectedItems = computed<CleanupItem[]>(() => {
  const out: CleanupItem[] = [];
  for (const c of report.value?.categories ?? [])
    for (const i of c.items) if (selected.value.has(i.rel_path)) out.push(i);
  return out;
});
const selectedSize = computed(() => selectedItems.value.reduce((s, i) => s + i.size, 0));
const selectedProtected = computed(() => selectedItems.value.filter((i) => i.protected).length);

async function doDelete() {
  confirming.value = false;
  busy.value = true;
  err.value = "";
  msg.value = "";
  try {
    const res = await api.moveToTrash(selectedItems.value.map((i) => i.rel_path));
    msg.value = `已移入废纸篓 ${res.removed} 项，释放 ${fmtSize(res.freed)}`;
    if (res.errors.length) err.value = res.errors.join("；");
    await load();
  } catch (e: any) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="cleanup">
    <p class="muted sub">
      随使用堆积的历史文件按类别列出。删除不是抹掉，而是<b>移进系统废纸篓</b>（可设 30 天自动清除，误删可从废纸篓捞回）。
    </p>

    <!-- 总览条 -->
    <div class="stats" v-if="report">
      <div class="stat">
        <div class="stat-label">项目总占用</div>
        <div class="stat-num">{{ fmtSize(report.project_size) }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">可清理历史文件</div>
        <div class="stat-num warn">{{ fmtSize(totalSelectable) }}</div>
      </div>
      <div class="stat" :class="{ 'stat-danger': selectedItems.length }">
        <div class="stat-label">本次已选</div>
        <div class="stat-num">
          {{ selectedItems.length ? `${fmtSize(selectedSize)} · ${selectedItems.length} 项` : "—" }}
        </div>
      </div>
    </div>

    <div class="toolbar">
      <button class="sm" :disabled="loading" @click="load">
        {{ loading ? "扫描中…" : "重新扫描" }}
      </button>
      <span class="muted hint">当前批次证据默认不勾选，避免误删刚跑完的物料</span>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

    <!-- 分组清单 -->
    <div class="groups" v-if="report">
      <div v-if="loading" class="muted empty-hint">正在扫描历史文件…</div>
      <div v-for="cat in report.categories" :key="cat.key" class="card group">
        <div class="group-hd" @click="collapsed[cat.key] = !collapsed[cat.key]">
          <button
            class="tri"
            :class="'s' + groupState(cat.items)"
            :disabled="!cat.items.length"
            :title="groupState(cat.items) === 2 ? '取消全选' : '全选本类'"
            @click.stop="toggleGroup(cat.items)"
          >
            <span v-if="groupState(cat.items) === 2">✓</span>
            <span v-else-if="groupState(cat.items) === 1">–</span>
          </button>
          <i class="chev">{{ collapsed[cat.key] ? "▸" : "▾" }}</i>
          <span class="g-title">{{ cat.title }}</span>
          <span v-if="cat.key === 'build'" class="pill pill-success">可重装</span>
          <span class="muted g-hint">{{ cat.hint }}</span>
          <span class="g-size">{{ fmtSize(cat.total_size) }}</span>
        </div>
        <div v-if="!collapsed[cat.key]" class="group-body">
          <div v-if="!cat.items.length" class="muted empty-hint">这一类目前没有可清理的文件。</div>
          <div
            v-for="it in cat.items"
            :key="it.rel_path"
            class="row"
            :class="{ on: selected.has(it.rel_path) }"
            @click="toggleItem(it.rel_path)"
          >
            <span class="ck">{{ selected.has(it.rel_path) ? "☑" : "☐" }}</span>
            <span class="mono r-name" :title="it.rel_path">{{ it.name }}</span>
            <span v-if="it.tag === '当前批次'" class="pill pill-accent">当前批次</span>
            <span v-else-if="it.tag" class="pill pill-muted">{{ it.tag }}</span>
            <span class="muted r-time">{{ fmtTime(it.modified) }}</span>
            <span class="r-size">{{ fmtSize(it.size) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 底部操作栏 -->
    <div class="footer">
      <span class="foot-info">
        已选 <b>{{ selectedItems.length }}</b> 项 · 预计释放 <b class="warn">{{ fmtSize(selectedSize) }}</b>
        <span v-if="selectedProtected" class="warn"> · 含 {{ selectedProtected }} 项当前批次</span>
      </span>
      <button class="sm" :disabled="!selectedItems.length" @click="selected = new Set()">清除选择</button>
      <button
        class="danger"
        :disabled="!selectedItems.length || busy"
        @click="confirming = true"
      >
        {{ busy ? "处理中…" : "移入废纸篓" }}
      </button>
    </div>

    <!-- 二次确认（normal-flow 遮罩，避免 position:fixed 塌陷） -->
    <div v-if="confirming" class="modal-mask" @click.self="confirming = false">
      <div class="modal card">
        <div class="m-title">确认清理</div>
        <p class="m-body">
          将把 <b>{{ selectedItems.length }}</b> 项移入系统废纸篓，预计释放
          <b>{{ fmtSize(selectedSize) }}</b>。
          <span v-if="selectedProtected" class="warn">其中 {{ selectedProtected }} 项属于当前批次证据。</span>
          文件进入废纸篓后仍可手动还原。
        </p>
        <div class="m-actions">
          <button class="sm" @click="confirming = false">取消</button>
          <button class="danger" @click="doDelete">确认移入废纸篓</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cleanup { display: flex; flex-direction: column; height: 100%; min-height: 0; position: relative; }
.sub { margin: 4px 0 12px; line-height: 1.6; flex-shrink: 0; }

.stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 12px; flex-shrink: 0; }
.stat { background: var(--surface-1); border-radius: var(--radius); padding: 10px 12px; }
.stat-danger { background: var(--bg-danger); }
.stat-label { font-size: 12px; color: var(--text-muted); }
.stat-num { font-size: 22px; font-weight: 500; margin-top: 2px; }
.stat-num.warn, .warn { color: var(--text-warning); }
.stat-danger .stat-label { color: var(--text-danger); }
.stat-danger .stat-num { color: var(--text-danger); }

.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-shrink: 0; }
.toolbar .hint { font-size: 12px; }

.err { color: var(--text-danger); background: var(--bg-danger); padding: 8px 12px; border-radius: var(--radius); margin: 0 0 8px; font-size: 13px; }
.ok { color: var(--text-success); background: var(--bg-success); padding: 8px 12px; border-radius: var(--radius); margin: 0 0 8px; font-size: 13px; }

.groups { flex: 1 1 auto; overflow-y: auto; min-height: 0; display: flex; flex-direction: column; gap: 8px; }
/* flex-shrink:0 关键：否则纵向 flex 里的卡片会被压扁、被 overflow:hidden 裁掉，
   导致 .groups 永远滚不动（scrollHeight==clientHeight）。不缩才会溢出触发滚动。 */
.group { overflow: hidden; flex-shrink: 0; }
.group-hd { display: flex; align-items: center; gap: 8px; padding: 10px 12px; cursor: pointer; font-size: 13px; }
.group-hd:hover { background: var(--surface-1); }
.g-title { font-weight: 500; }
.g-hint { font-size: 12px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-size { font-weight: 500; flex-shrink: 0; }
.chev { font-style: normal; color: var(--text-muted); font-size: 12px; width: 12px; }

/* 三态复选框 */
.tri { width: 18px; height: 18px; padding: 0; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; line-height: 1; border-radius: 4px; background: var(--surface-2); border: 0.5px solid var(--border-strong); color: var(--text-accent); }
.tri.s2 { background: var(--bg-accent); border-color: var(--border-accent); }
.tri.s1 { background: var(--bg-accent); border-color: var(--border-accent); }
.tri:disabled { opacity: 0.4; }

.group-body { border-top: 0.5px solid var(--border); padding: 4px 6px; }
.empty-hint { padding: 12px 10px; font-size: 12px; }
.row { display: flex; align-items: center; gap: 8px; padding: 6px 8px; font-size: 12px; border-radius: var(--radius); cursor: pointer; }
.row:hover { background: var(--surface-1); }
.row.on { background: var(--bg-accent); }
.ck { width: 16px; flex-shrink: 0; color: var(--text-accent); }
.r-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.r-time { width: 120px; text-align: right; flex-shrink: 0; font-size: 11px; }
.r-size { width: 72px; text-align: right; flex-shrink: 0; font-weight: 500; }

.footer { display: flex; align-items: center; gap: 10px; padding-top: 12px; margin-top: 10px; border-top: 0.5px solid var(--border); flex-shrink: 0; }
.foot-info { font-size: 13px; color: var(--text-secondary); margin-right: auto; }

.modal-mask { position: absolute; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { width: 380px; max-width: 90%; padding: 16px 18px; background: var(--surface-2); }
.m-title { font-size: 15px; font-weight: 500; margin-bottom: 8px; }
.m-body { font-size: 13px; line-height: 1.6; color: var(--text-secondary); margin: 0 0 16px; }
.m-actions { display: flex; justify-content: flex-end; gap: 8px; }

.pill.sm, .sm { font-size: 11px; }
button.sm { padding: 3px 8px; }
</style>
