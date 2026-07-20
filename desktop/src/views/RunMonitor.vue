<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue";
import { runStore, labelOf, type CellStatus } from "../runStore";

defineOptions({ name: "RunMonitor" });

type Filter = "all" | "ok" | "bad" | "needs";
const filter = ref<Filter>("all");
const eventsBox = ref<HTMLElement | null>(null);

const serials = computed(() => runStore.serials());
const caseIds = computed(() => runStore.caseIds());
const hasRun = computed(() => runStore.cells.length > 0);

const overall = computed(() => {
  if (runStore.running) return runStore.aborting ? "中止中…" : "运行中";
  if (!hasRun.value) return "未开始";
  if (runStore.cells.some((c) => c.status === "aborted")) return "已中止";
  return "已完成";
});

function inFilter(s: CellStatus): boolean {
  if (filter.value === "all") return true;
  if (filter.value === "ok") return s === "pass" || s === "healed";
  if (filter.value === "bad") return s === "fail" || s === "app_defect";
  if (filter.value === "needs") return s === "needs_human";
  return true;
}
const counts = computed(() => {
  const c = { ok: 0, bad: 0, needs: 0 };
  for (const cell of runStore.cells) {
    if (cell.status === "pass" || cell.status === "healed") c.ok++;
    else if (cell.status === "fail" || cell.status === "app_defect") c.bad++;
    else if (cell.status === "needs_human") c.needs++;
  }
  return c;
});

// 右栏「实时过程」：选中某格 → 只看该格日志；否则看全部运行事件
const shownLines = computed(() =>
  runStore.selectedKey
    ? (runStore.cells.find((c) => runStore.key(c.serial, c.caseId) === runStore.selectedKey)?.lines || []).map((t) => ({ text: t, level: "info" as const }))
    : runStore.events
);

function pillClass(s: CellStatus) {
  return {
    "st-pass": s === "pass" || s === "healed",
    "st-fail": s === "fail" || s === "app_defect",
    "st-needs": s === "needs_human",
    "st-run": s === "running",
    "st-wait": s === "waiting",
    "st-abort": s === "aborted",
  };
}

function pickCell(serial: string, caseId: string) {
  const k = runStore.key(serial, caseId);
  runStore.selectedKey = runStore.selectedKey === k ? "" : k;
}

// 新日志自动滚到底
watch(
  () => shownLines.value.length,
  () => nextTick(() => { if (eventsBox.value) eventsBox.value.scrollTop = eventsBox.value.scrollHeight; })
);
</script>

<template>
  <div class="monitor">
    <!-- 空态 -->
    <div v-if="!hasRun" class="empty card">
      <div class="empty-t">还没有执行任务</div>
      <div class="muted">去「场景库」选好用例和设备，点「执行选中」，这里会实时显示运行过程。</div>
    </div>

    <template v-else>
      <!-- 头部：标题 + 状态 + 进度 + 中止 -->
      <div class="hd">
        <div class="hd-l">
          <span class="title">{{ runStore.title || "执行" }}</span>
          <span class="badge" :class="{ run: runStore.running, done: overall === '已完成', abort: overall === '已中止' }">{{ overall }}</span>
          <span class="muted prog">{{ runStore.doneCount() }}/{{ runStore.totalCount() }} 格完成</span>
        </div>
        <button class="abort-btn" :disabled="!runStore.running || runStore.aborting" @click="runStore.abort()">
          {{ runStore.aborting ? "中止中…" : "中止任务" }}
        </button>
      </div>

      <div class="body">
        <!-- 左：筛选 + 矩阵 -->
        <div class="left card">
          <div class="filters">
            <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ runStore.totalCount() }}</button>
            <button :class="{ on: filter === 'ok' }" @click="filter = 'ok'">通过 {{ counts.ok }}</button>
            <button :class="{ on: filter === 'bad' }" @click="filter = 'bad'">失败 {{ counts.bad }}</button>
            <button :class="{ on: filter === 'needs' }" @click="filter = 'needs'">需人工 {{ counts.needs }}</button>
          </div>
          <div class="matrix-scroll">
            <table class="matrix">
              <thead>
                <tr>
                  <th class="corner">设备 \ 用例</th>
                  <th v-for="cid in caseIds" :key="cid" class="mono">{{ cid }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in serials" :key="s">
                  <td class="row-hd mono">{{ s }}</td>
                  <td v-for="cid in caseIds" :key="cid" class="cell">
                    <template v-if="runStore.cell(s, cid)">
                      <button
                        class="st"
                        :class="[pillClass(runStore.cell(s, cid)!.status), {
                          dim: !inFilter(runStore.cell(s, cid)!.status),
                          sel: runStore.selectedKey === runStore.key(s, cid),
                        }]"
                        @click="pickCell(s, cid)"
                      >
                        {{ labelOf(runStore.cell(s, cid)!.status) }}
                        <span v-if="runStore.cell(s, cid)!.elapsed" class="et">{{ runStore.cell(s, cid)!.elapsed }}s</span>
                      </button>
                    </template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="foot muted">点用例格看该格实时日志；跑完去「证据」tab 看该用例的截图 / ui / 日志。</div>
        </div>

        <!-- 右：实时过程 -->
        <div class="right card">
          <div class="right-hd">
            <span>实时过程</span>
            <span class="conn" :class="{ live: runStore.running }">{{ runStore.running ? "实时连接正常" : "空闲" }}</span>
          </div>
          <div class="right-sub muted">
            {{ runStore.selectedKey ? `格日志：${runStore.selectedKey.replace("|", " / ")}` : "全部运行事件" }}
            <button v-if="runStore.selectedKey" class="link" @click="runStore.selectedKey = ''">看全部</button>
          </div>
          <div ref="eventsBox" class="events">
            <div v-for="(e, i) in shownLines" :key="i" class="ev" :class="{ err: e.level === 'error' }">
              {{ e.text }}
            </div>
            <div v-if="!shownLines.length" class="muted ev-empty">（暂无事件）</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.monitor { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.empty { padding: 40px 24px; text-align: center; }
.empty-t { font-size: 15px; font-weight: 500; margin-bottom: 8px; }

.hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.hd-l { display: flex; align-items: center; gap: 10px; }
.title { font-size: 16px; font-weight: 500; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--surface-2); color: var(--text-secondary); }
.badge.run { background: var(--bg-accent); color: var(--text-accent); }
.badge.done { background: var(--bg-success, rgba(52,199,89,.12)); color: var(--text-success); }
.badge.abort { background: var(--bg-danger); color: var(--text-danger); }
.prog { font-size: 13px; }
.abort-btn { border: 0.5px solid var(--text-danger); color: var(--text-danger); background: transparent; padding: 6px 14px; border-radius: var(--radius); font-size: 13px; }
.abort-btn:hover:not(:disabled) { background: var(--bg-danger); }
.abort-btn:disabled { opacity: 0.4; border-color: var(--border); color: var(--text-secondary); }

.body { display: flex; gap: 12px; flex: 1; min-height: 0; }
.left { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.right { width: 360px; flex-shrink: 0; display: flex; flex-direction: column; }

.filters { display: flex; gap: 6px; padding: 10px; border-bottom: 0.5px solid var(--border); flex-wrap: wrap; }
.filters button { font-size: 12px; padding: 4px 10px; border-radius: 999px; border: 0.5px solid var(--border); background: transparent; color: var(--text-secondary); }
.filters button.on { background: var(--text-primary, #111); color: var(--bg, #fff); border-color: transparent; }

.matrix-scroll { flex: 1; overflow: auto; padding: 6px; min-height: 0; }
.matrix { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 13px; }
.matrix th, .matrix td { padding: 6px 8px; border-bottom: 0.5px solid var(--border); text-align: left; }
.matrix thead th { position: sticky; top: 0; background: var(--surface-1); font-weight: 500; color: var(--text-secondary); font-size: 12px; }
.corner { color: var(--text-secondary); }
.row-hd { color: var(--text-secondary); white-space: nowrap; }
.cell { text-align: center; }
.st { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; padding: 3px 10px; border-radius: 999px; border: none; cursor: pointer; }
.st .et { font-size: 10px; opacity: 0.7; }
.st.dim { opacity: 0.25; }
.st.sel { outline: 2px solid var(--text-accent); outline-offset: 1px; }
.st-wait { background: var(--surface-2); color: var(--text-secondary); }
.st-run { background: var(--bg-accent); color: var(--text-accent); }
.st-pass { background: rgba(52,199,89,.15); color: #1a7f37; }
.st-fail { background: var(--bg-danger); color: var(--text-danger); }
.st-needs { background: rgba(255,179,0,.16); color: #9a6700; }
.st-abort { background: var(--surface-2); color: var(--text-secondary); text-decoration: line-through; }
.foot { padding: 8px 10px; font-size: 11px; border-top: 0.5px solid var(--border); }

.right-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px; border-bottom: 0.5px solid var(--border); font-size: 13px; font-weight: 500; }
.conn { font-size: 11px; color: var(--text-secondary); font-weight: 400; }
.conn.live { color: var(--text-success); }
.right-sub { padding: 6px 12px; font-size: 11px; display: flex; align-items: center; gap: 8px; }
.link { font-size: 11px; color: var(--text-accent); background: none; border: none; padding: 0; cursor: pointer; text-decoration: underline; }
.events { flex: 1; overflow: auto; padding: 6px 12px; min-height: 0; font-size: 12px; line-height: 1.55; }
.ev { padding: 2px 0; color: var(--text-secondary); word-break: break-all; white-space: pre-wrap; border-bottom: 0.5px solid var(--border); }
.ev.err { color: var(--text-danger); }
.ev-empty { padding: 10px 0; }
</style>
