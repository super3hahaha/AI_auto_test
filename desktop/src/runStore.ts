// 运行状态（跨组件、跨 tab 共享，且独立于组件生命周期——切 tab/切子 tab 都不丢）。
// 场景库点「执行选中」→ 填充这里并跳到执行台 tab；执行台监控页只读这里渲染。
// 编排本身（串行 for 设备 × for 用例）也放这里，不再挂在组件上。
import { reactive } from "vue";
import { api } from "./api";
import { store } from "./store";

export type CellStatus =
  | "waiting"      // 等待中
  | "running"      // 运行中
  | "pass"         // 通过（exit 0）
  | "healed"       // 自愈通过（脚本自愈模式下改脚本后通过）
  | "fail"         // 失败（脚本异常退出，非自愈模式）
  | "app_defect"   // 疑似 App 缺陷（自愈判定，exit 2）
  | "needs_human"  // 需人工介入（自愈耗尽/无法判定，exit 3/4/5）
  | "aborted";     // 已中止

export interface RunCell {
  serial: string;
  caseId: string;
  module: string;
  status: CellStatus;
  exitCode: number | null;
  elapsed: number; // 秒
  lines: string[]; // 该格自己的流式日志
}

export interface RunEvent {
  level: "info" | "error";
  text: string;
}

// 每格一条待跑任务：{case_id, script, module}
export interface RunCaseSpec {
  case_id: string;
  script: string;
  module: string;
}

function classify(code: number, brain: boolean, aborted: boolean): CellStatus {
  if (aborted) return "aborted";
  if (code === 0) return "pass"; // healed 由日志另判（见下）
  if (brain) {
    if (code === 2) return "app_defect";
    if (code === 3 || code === 4 || code === 5) return "needs_human";
  }
  return "fail";
}

export const runStore = reactive({
  running: false,
  aborting: false,
  syncing: false,
  title: "",
  slug: "",
  brain: false,
  startedAt: 0,
  cells: [] as RunCell[],
  events: [] as RunEvent[],
  selectedKey: "" as string, // 选中的格子 key（serial|caseId）；空=看全部事件

  key(serial: string, caseId: string) {
    return `${serial}|${caseId}`;
  },
  cell(serial: string, caseId: string) {
    return this.cells.find((c) => c.serial === serial && c.caseId === caseId);
  },
  serials(): string[] {
    return [...new Set(this.cells.map((c) => c.serial))];
  },
  caseIds(): string[] {
    return [...new Set(this.cells.map((c) => c.caseId))];
  },
  doneCount(): number {
    return this.cells.filter((c) => c.status !== "waiting" && c.status !== "running").length;
  },
  totalCount(): number {
    return this.cells.length;
  },
  pushEvent(text: string, level: "info" | "error" = "info") {
    this.events.push({ text, level });
  },

  // 场景库触发：newBoard=true 时先跑 new_run.py 开新一轮
  async start(opts: {
    slug: string;
    cases: RunCaseSpec[];
    serials: string[];
    brain: boolean;
    newBoard: boolean;
    title: string;
  }) {
    if (this.running) return;
    this.running = true;
    this.aborting = false;
    this.slug = opts.slug;
    this.brain = opts.brain;
    this.title = opts.title;
    this.selectedKey = "";
    this.startedAt = Date.now();
    this.cells = [];
    for (const s of opts.serials) {
      for (const c of opts.cases) {
        this.cells.push({
          serial: s,
          caseId: c.case_id,
          module: c.module,
          status: "waiting",
          exitCode: null,
          elapsed: 0,
          lines: [],
        });
      }
    }
    this.events = [];
    this.pushEvent(
      opts.brain
        ? "脚本自愈已启用（引擎: on，失败步骤将由 claude 接管诊断+改脚本重跑）"
        : "脚本自愈未启用（引擎: off，失败步骤将诚实判失败）"
    );
    this.pushEvent(
      `共 ${this.cells.length} 格待执行（${opts.serials.length} 设备 × ${opts.cases.length} 用例）`
    );

    if (opts.newBoard) {
      this.pushEvent("新建看板：new_run.py（当前 App 旧账本整份归档，切新一轮）");
      try {
        const code = await api.newRun(opts.slug, (l) => this.pushEvent(`[new_run] ${l}`));
        if (code !== 0) {
          this.pushEvent(`✖ 新建看板失败（exit ${code}），已放弃执行`, "error");
          this.finish();
          return;
        }
        await store.loadRuns();
      } catch (e: any) {
        this.pushEvent(`✖ 新建看板异常：${e}`, "error");
        this.finish();
        return;
      }
    }

    outer: for (const s of opts.serials) {
      for (const c of opts.cases) {
        if (this.aborting) break outer;
        const cell = this.cell(s, c.case_id)!;
        cell.status = "running";
        this.pushEvent(`▶ ${s} / ${c.case_id} 开始（${this.brain ? "auto_repair" : "run_flow"}）`);
        const t0 = Date.now();
        const runner = opts.brain ? api.runFlowRepair : api.runFlow;
        try {
          const code = await runner(opts.slug, c.case_id, c.script, s, (l) => {
            cell.lines.push(l);
            this.pushEvent(`[${s}/${c.case_id}] ${l}`, /失败|异常|✖|error|Error/.test(l) ? "error" : "info");
          });
          cell.elapsed = Math.round((Date.now() - t0) / 1000);
          cell.exitCode = code;
          let st = classify(code, this.brain, this.aborting);
          // 自愈模式下 exit 0 且日志里有自愈成功痕迹 → 标「自愈通过」
          if (st === "pass" && this.brain && cell.lines.some((l) => l.includes("自愈成功"))) {
            st = "healed";
          }
          cell.status = st;
          this.pushEvent(`${st === "pass" || st === "healed" ? "✔" : "✖"} ${s}/${c.case_id} → ${labelOf(st)}（exit ${code} · ${cell.elapsed}s）`, st === "pass" || st === "healed" ? "info" : "error");
        } catch (e: any) {
          cell.elapsed = Math.round((Date.now() - t0) / 1000);
          cell.status = this.aborting ? "aborted" : "fail";
          cell.exitCode = -1;
          this.pushEvent(`✖ ${s}/${c.case_id} 调用异常：${e}`, "error");
        }
      }
    }

    if (this.aborting) {
      for (const c of this.cells) {
        if (c.status === "waiting" || c.status === "running") c.status = "aborted";
      }
      this.pushEvent("🛑 任务已中止", "error");
    } else {
      this.pushEvent("全部执行完成。通过/失败判定与关键证据升级仍回 Claude Code 做。");
    }
    this.finish();
  },

  finish() {
    this.running = false;
    this.aborting = false;
    // 执行台收尾：无论成功/失败/中止，都把本地 ledger 推回线上表格。桌面端跑的结果否则只留本地。
    // fire-and-forget：同步在后台流式跑，日志进事件面板；失败只提示、不阻塞。
    void this.syncSheets();
  },

  // 同步到线上 Google Sheets（sheets_sync.py）。幂等；由 finish() 收尾自动调，也可手动触发。
  async syncSheets() {
    if (!this.slug || this.syncing) return;
    this.syncing = true;
    this.pushEvent("☁ 同步到线上表格…（sheets_sync）");
    try {
      const code = await api.syncSheets(this.slug, (l) =>
        this.pushEvent(`[sync] ${l}`, /失败|异常|✖|error|Error|Traceback/.test(l) ? "error" : "info")
      );
      if (code === 0) {
        this.pushEvent("☁ 已同步到线上表格");
      } else {
        this.pushEvent(`☁ 同步结束但有异常（exit ${code}），线上可能不是最新——可稍后重试`, "error");
      }
    } catch (e: any) {
      this.pushEvent(`☁ 同步调用异常：${e}`, "error");
    } finally {
      this.syncing = false;
    }
  },

  async abort() {
    if (!this.running || this.aborting) return;
    this.aborting = true;
    this.pushEvent("正在中止…（向进程组发 SIGTERM）", "error");
    try {
      const ok = await api.abortRun();
      if (!ok) this.pushEvent("没有正在运行的后端任务可中止（可能刚好在两格之间）", "error");
    } catch (e: any) {
      this.pushEvent(`中止失败：${e}`, "error");
    }
  },
});

export function labelOf(s: CellStatus): string {
  return {
    waiting: "等待中",
    running: "运行中",
    pass: "通过",
    healed: "自愈通过",
    fail: "失败",
    app_defect: "疑似缺陷",
    needs_human: "需人工",
    aborted: "已中止",
  }[s];
}
