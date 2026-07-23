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
  recording: boolean; // 脚本已跑完（status 已是 pass/fail 等终态），但 judge_result 还没落库——
                     // 整轮"完成"要等这个也变 false，不然进度会显示"N/N 完成"却其实还在判定。
  issue: IssueState; // 收尾阶段这一格问题清单的自动登记状态（仅失败/需复核格才会流转）
}

// 问题清单自动登记状态（issue_register.py）：none=不适用（通过格）/未开始；registering=收尾登记中；
// registered=已登记进 issues.csv；manual=自动登记没完成，需回 Claude Code 手动登记。
export type IssueState = "none" | "registering" | "registered" | "manual";

export interface RunEvent {
  level: "info" | "error";
  text: string;
}

// ── 执行记录（持久化快照）──
// 完整跑完（未中止）的一轮执行台落成一份快照存进 apps/<slug>/ledger/run_records/<id>.json，
// 「执行记录」页按 id 切换、用 RunMonitor 只读渲染。meta 带够列表用的摘要，list 只回 meta。
export interface RunRecordMeta {
  id: string;        // 由 startedAt 派生的 YYYYMMDD-HHmmss
  slug: string;
  title: string;
  brain: boolean;
  startedAt: number; // 毫秒
  finishedAt: number;
  ok: number;        // 通过+自愈
  bad: number;       // 失败+疑似缺陷
  needs: number;     // 需人工
  total: number;
  deviceCount: number;
  caseCount: number;
}
export interface RunRecord {
  meta: RunRecordMeta;
  cells: RunCell[];  // 终态快照（recording 恒 false）
  events: RunEvent[];
}

// RunMonitor 的数据源抽象：既能是实时 runStore，也能是一份只读快照（见 makeRecordSource）。
export interface MonitorSource {
  running: boolean;
  aborting: boolean;
  syncing: boolean;
  docGenerating: boolean;
  title: string;
  issueTotal: number;
  cells: RunCell[];
  events: RunEvent[];
  selectedKey: string;
  key(serial: string, caseId: string): string;
  cell(serial: string, caseId: string): RunCell | undefined;
  serials(): string[];
  caseIds(): string[];
  doneCount(): number;
  totalCount(): number;
  abort(): unknown;
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
  docGenerating: false,
  title: "",
  slug: "",
  brain: false,
  startedAt: 0,
  cells: [] as RunCell[],
  events: [] as RunEvent[],
  selectedKey: "" as string, // 选中的格子 key（serial|caseId）；空=看全部事件
  issueTotal: 0, // 本轮登记问题清单的固定分母（开始登记时定住，串行处理不再跟着涨）
  completed: false, // 本轮是否「完整跑完」（编排循环自然走到底，非中止/非早退失败）——只有它为真才存执行记录

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
    return this.cells.filter((c) => c.status !== "waiting" && c.status !== "running" && !c.recording).length;
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
    apkPath?: string; // 选了某个留存版本时，跑用例前先在每台设备上强制重装这个 apk
    package?: string;
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
    this.issueTotal = 0;
    this.completed = false;
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
          recording: false,
          issue: "none",
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
      // new_run.py 内部会重建 board/summary 看板（本轮范围取 target.json.scope）；
      // 这里先把本次勾选的用例 ID 写回 scope，让看板范围跟桌面壳实际要跑的用例保持一致，
      // 而不是退回 target.json 里旧的/空的 scope 变成"全量"。
      try {
        const scope = opts.cases.map((c) => c.case_id).join(",");
        await api.setTargetScope(opts.slug, scope);
        this.pushEvent(`本轮范围已写回 target.json.scope：${scope}`);
      } catch (e: any) {
        this.pushEvent(`⚠ 写回本轮范围失败：${e}`, "error");
      }
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

    // 选了留存版本 → 逐台强制重装（adb install -r 本身幂等，不用先查设备当前版本，
    // 装错版本跑测试比多花几秒重装的代价大得多）。任一台装机失败就整轮放弃，不带着错版本瞎跑。
    if (opts.apkPath && opts.package) {
      this.pushEvent(`📦 强制重装选中版本到 ${opts.serials.length} 台设备…`);
      for (const s of opts.serials) {
        try {
          const code = await api.installApk(opts.apkPath, opts.package, s, (l) => this.pushEvent(`[install ${s}] ${l}`));
          if (code !== 0) {
            this.pushEvent(`✖ ${s} 装机失败（exit ${code}），已放弃执行`, "error");
            this.finish();
            return;
          }
        } catch (e: any) {
          this.pushEvent(`✖ ${s} 装机调用异常：${e}，已放弃执行`, "error");
          this.finish();
          return;
        }
      }
      this.pushEvent("📦 装机完成");
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

          // 所有终态（pass/healed/fail/app_defect/needs_human）都必须落账本——纯确定性映射，
          // 不调 claude：run_flow.py/auto_repair.py 只写 log.csv 和时间戳，从不碰 queue.csv 的
          // "当前状态"列，这条不调，这条用例会一直停在"待执行"，看起来像完全没跑过（真实踩过：
          // CUT-EDGE-02 明明跑了且失败，因为当时这步被跳过，账本显示"已完成 2"漏了它）。
          if (!this.aborting && (st === "pass" || st === "healed" || st === "fail" || st === "app_defect" || st === "needs_human")) {
            // recording=true 期间这格的 pass/fail 只是"脚本跑没跑崩"的初步状态，落账本还没完成——
            // doneCount()/进度条据此排除它，避免"N/N 完成"却其实还没写进账本的误导。落库是纯本地
            // 文件写入，几乎瞬时，这个态停留时间很短，不会像以前 claude 判定那样卡 1-2 分钟。
            cell.recording = true;
            try {
              const jcode = await api.judgeResult(opts.slug, c.case_id, s, st, (l) => {
                cell.lines.push(l); // 落账本输出也并入该格日志，否则选中卡片时"该格日志"里看不到
                this.pushEvent(`[落账本 ${s}/${c.case_id}] ${l}`, /失败|异常|✖|error|Error/.test(l) ? "error" : "info");
              });
              if (jcode !== 0) {
                this.pushEvent(`⚠ ${s}/${c.case_id} 落账本异常（exit ${jcode}）`, "error");
              }
            } catch (e: any) {
              this.pushEvent(`✖ ${s}/${c.case_id} 落账本调用异常：${e}`, "error");
            } finally {
              cell.recording = false;
            }
          }
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
      this.completed = true; // 编排循环自然走到底 → 本轮完整跑完（哪怕有失败格），收尾后存执行记录
      this.pushEvent("全部执行完成，结果已自动落库，收尾同步表格 + 刷新 Doc 报告。");
    }
    this.finish();
  },

  finish() {
    this.running = false;
    this.aborting = false;
    // 先把本轮快照抓成局部引用（新一轮 start() 会另建新数组，这些引用仍指向本轮，不被后续 mutate）。
    // completed 决定要不要存执行记录：中止（this.aborting 曾为真）/早退失败的轮次 completed 一直是 false。
    const completed = this.completed;
    const snap = { slug: this.slug, title: this.title, brain: this.brain, startedAt: this.startedAt };
    const cellsRef = this.cells;
    const eventsRef = this.events;
    this.completed = false;
    // 执行台收尾：无论成功/失败/中止，都把本地 ledger 推回线上表格 + 刷新 Doc 图文报告。
    // 桌面端跑的结果否则只留本地、报告也不会带上最新判定。fire-and-forget：在后台流式跑，
    // 日志进事件面板；失败只提示、不阻塞（不重跑，避免收尾阶段无限重试）。
    // 存执行记录排在 publish 之后 —— 让快照带上收尾阶段落定的问题清单登记状态（issue 字段）。
    void this.publish().then(() => {
      if (completed) void this.saveRecord(snap, cellsRef, eventsRef);
    });
  },

  // 把「完整跑完」的这一轮执行台落成一份持久化快照（apps/<slug>/ledger/run_records/<id>.json）。
  // 只在 finish() 里、且 completed 为真时调；中止/早退失败的轮次不会走到这里。
  async saveRecord(
    snap: { slug: string; title: string; brain: boolean; startedAt: number },
    cells: RunCell[],
    events: RunEvent[]
  ) {
    if (!snap.slug || !cells.length) return;
    const counts = { ok: 0, bad: 0, needs: 0 };
    for (const c of cells) {
      if (c.status === "pass" || c.status === "healed") counts.ok++;
      else if (c.status === "fail" || c.status === "app_defect") counts.bad++;
      else if (c.status === "needs_human") counts.needs++;
    }
    const record: RunRecord = {
      meta: {
        id: fmtRunRecordId(snap.startedAt),
        slug: snap.slug,
        title: snap.title,
        brain: snap.brain,
        startedAt: snap.startedAt,
        finishedAt: Date.now(),
        ok: counts.ok,
        bad: counts.bad,
        needs: counts.needs,
        total: cells.length,
        deviceCount: new Set(cells.map((c) => c.serial)).size,
        caseCount: new Set(cells.map((c) => c.caseId)).size,
      },
      cells: cells.map((c) => ({ ...c, recording: false })), // 终态快照，recording 归零
      events: events.map((e) => ({ ...e })),
    };
    try {
      await api.saveRunRecord(snap.slug, record);
      this.pushEvent(`🗄 本轮已存入执行记录（id ${record.meta.id}）——去「执行记录」子 tab 可回看`);
    } catch (e: any) {
      this.pushEvent(`⚠ 执行记录保存失败：${e}`, "error");
    }
  },

  // 收尾发布：先同步表格，再刷新 Doc 报告——doc_report 内部会重新按 queue.csv 当前状态投影，
  // 所以必须放在本轮所有 judge_result 落库之后，且顺序在 syncSheets 之后（各自独立、互不依赖，
  // 但都读同一份本地 ledger，串行跑避免并发写同一份 CSV）。
  async publish() {
    await this.registerIssues();
    await this.syncSheets();
    await this.genDocReport();
  },

  // 收尾第一步：把本轮所有失败/需复核的格子自动登记进问题清单（issue_register.py）。
  // 必须排在 syncSheets/genDocReport 之前——那两个要读 issues.csv 才能把问题带进「问题清单」tab
  // 和 Doc 的失败详情。串行逐格调：headless claude 会写同一份 issues.csv，并发会撞车。
  // 中止的这一轮不登记（aborted 不是判定结果，且证据可能不完整）。fire-and-forget 风格：
  // 单格失败只提示、不中断整个收尾。
  async registerIssues() {
    if (!this.slug || this.aborting) return;
    const targets = this.cells.filter(
      (c) => c.status === "fail" || c.status === "app_defect" || c.status === "needs_human"
    );
    if (!targets.length) return;
    this.issueTotal = targets.length; // 开始登记时就定住分母，串行逐条处理不再让分母跟着涨
    this.pushEvent(`自动登记问题清单：${targets.length} 条失败/需复核用例（issue_register）…`);
    for (const cell of targets) {
      // fail/app_defect→BUG-、needs_human→RISK-（前缀由 issue_register 按 status 确定性映射，这里只透传）
      const status = cell.status as "fail" | "app_defect" | "needs_human";
      cell.issue = "registering";
      try {
        const code = await api.registerIssue(this.slug, cell.caseId, cell.serial, status, (l) => {
          cell.lines.push(l);
          this.pushEvent(`[问题登记 ${cell.serial}/${cell.caseId}] ${l}`,
            /失败|异常|✖|error|Error|Traceback|未完成|需人工/.test(l) ? "error" : "info");
        });
        // issue_register 退出码：0=已登记/去重跳过；2/3/4=未完成，需人工登记
        cell.issue = code === 0 ? "registered" : "manual";
        this.pushEvent(
          code === 0
            ? `✔ ${cell.serial}/${cell.caseId} 已登记问题清单`
            : `⚠ ${cell.serial}/${cell.caseId} 自动登记未完成（exit ${code}），需回 Claude Code 手动登记`,
          code === 0 ? "info" : "error"
        );
      } catch (e: any) {
        cell.issue = "manual";
        this.pushEvent(`✖ ${cell.serial}/${cell.caseId} 问题登记调用异常：${e}`, "error");
      }
    }
  },

  // 同步到线上 Google Sheets（sheets_sync.py）。幂等；由 publish() 收尾自动调，也可手动触发。
  async syncSheets() {
    if (!this.slug || this.syncing) return;
    this.syncing = true;
    this.pushEvent("☁ 同步到线上表格…（sheets_sync）");
    try {
      const code = await api.syncSheets(this.slug, (l) =>
        // sheets_sync.py 自己每行都带 [sync] 前缀，这里不重复加，不然日志里会出现"[sync] [sync] xxx"
        this.pushEvent(l, /失败|异常|✖|error|Error|Traceback/.test(l) ? "error" : "info")
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

  // 刷新 Google Doc 图文报告（doc_report.py）。幂等（覆盖式刷新）；由 publish() 收尾自动调。
  async genDocReport() {
    if (!this.slug || this.docGenerating) return;
    this.docGenerating = true;
    this.pushEvent("📄 刷新 Doc 图文报告…（doc_report）");
    try {
      const code = await api.docReport(this.slug, (l) =>
        // doc_report.py 自己每行都带 [doc] 前缀，这里不重复加，不然日志里会出现"[doc] [doc] xxx"
        this.pushEvent(l, /失败|异常|✖|error|Error|Traceback/.test(l) ? "error" : "info")
      );
      if (code === 0) {
        this.pushEvent("📄 Doc 报告已刷新");
      } else {
        this.pushEvent(`📄 Doc 报告刷新有异常（exit ${code}）——可能是 OAuth 未授权/网络问题，可稍后重试`, "error");
      }
    } catch (e: any) {
      this.pushEvent(`📄 Doc 报告调用异常：${e}`, "error");
    } finally {
      this.docGenerating = false;
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

// 执行记录 id：由 startedAt（毫秒）派生的本地时间 YYYYMMDD-HHmmss（同一秒内极难重复，够用作文件名）
export function fmtRunRecordId(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// 把一份持久化执行记录包成 RunMonitor 能吃的只读数据源（形状与 runStore 的相关子集一致）。
// running/aborting/syncing/docGenerating 恒 false → RunMonitor 渲染成「已完成、空闲」的静态视图；
// selectedKey 本地可写（点卡片看该格日志）；abort 空实现（按钮本就因 running=false 被禁用）。
export function makeRecordSource(record: RunRecord): MonitorSource {
  const src = reactive({
    running: false,
    aborting: false,
    syncing: false,
    docGenerating: false,
    title: record.meta.title,
    // 问题清单摘要分母：终态记录里 issue 非 none 的格数（= 已登记 + 待人工），驱动头部 publishPhase 摘要
    issueTotal: record.cells.filter((c) => c.issue !== "none").length,
    cells: record.cells,
    events: record.events,
    selectedKey: "",
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
      return this.cells.length; // 记录都是终态，全部算完成
    },
    totalCount(): number {
      return this.cells.length;
    },
    abort() {
      /* 静态记录无可中止 */
    },
  });
  return src as unknown as MonitorSource;
}
