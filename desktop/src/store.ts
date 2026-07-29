// 全局共享状态：app 配置、App 注册表 + 当前选中 App、执行批次列表、当前选中的批次（证据查看器锚点）。
import { reactive } from "vue";
import { api, type AppConfig, type AppInfo, type RunRow } from "./api";

export const store = reactive({
  cfg: null as AppConfig | null,
  apps: [] as AppInfo[],
  activeSlug: "" as string, // 当前选中的被测 App，所有读/执行命令都带它
  runs: [] as RunRow[],
  selectedRunId: "" as string, // 证据查看器锚定的批次
  loadingRuns: false,
  err: "" as string,
  // 执行台/执行记录的用例卡片点「↗」→ 跳到证据页并定位到该格第一项证据的一次性请求。
  // 放全局 store 而不是走组件 emit 链：发起方 RunMonitor 嵌在 Runner 里，跟 App.vue 的视图切换
  // 隔着两层（Boards 那种只隔一层的才用 emit）。App.vue 监听它切视图，Evidence 挂载后消费掉置空。
  // attempt = 从该格日志的证据路径里抓到的 attempt 段（HHMMSS），用来精确对准「这一次执行」；
  // startedAt = 这一格开跑的毫秒时间戳，attempt 抓不到时（脚本没产出任何证据）按时间就近配。
  // 两者都为空/都配不上 → Evidence 退回最新一次并提示。
  evidenceJump: null as { serial: string; caseId: string; startedAt: number; attempt: string } | null,

  async loadConfig() {
    this.cfg = await api.getAppConfig();
    return this.cfg;
  },

  // 扫 apps/*/target.json；选定活跃 slug（优先 config/active.json，其次首个）
  async loadApps() {
    this.apps = await api.listApps();
    if (this.activeSlug && !this.apps.some((a) => a.slug === this.activeSlug)) {
      this.activeSlug = ""; // 之前选的 App 已不存在
    }
    if (!this.activeSlug && this.apps.length) {
      let act = "";
      try {
        act = await api.getActiveApp();
      } catch {
        /* active.json 缺失无妨 */
      }
      this.activeSlug = act && this.apps.some((a) => a.slug === act) ? act : this.apps[0].slug;
    }
    return this.apps;
  },

  // 切换当前 App：写回 active.json（命令行工具也跟着切）+ 重置批次锚点 + 重载批次
  async setActive(slug: string) {
    if (slug === this.activeSlug) return;
    this.activeSlug = slug;
    this.selectedRunId = "";
    try {
      await api.setActiveApp(slug);
    } catch (e: any) {
      this.err = String(e);
    }
    await this.loadRuns();
  },

  activeApp(): AppInfo | undefined {
    return this.apps.find((a) => a.slug === this.activeSlug);
  },

  // 删除一个 App 注册：挪进 apps/.trash/（不硬删），返回回收站目标路径；删完重扫列表，loadApps 会顺带清理失效的 activeSlug
  async deleteApp(slug: string): Promise<string> {
    const trashPath = await api.deleteApp(slug);
    await this.loadApps();
    await this.loadRuns();
    return trashPath;
  },

  async loadRuns() {
    if (!this.activeSlug) {
      this.runs = [];
      this.selectedRunId = "";
      return;
    }
    this.loadingRuns = true;
    this.err = "";
    try {
      this.runs = await api.listRuns(this.activeSlug);
      // 默认选中当前批次；没有则最新一条
      if (!this.selectedRunId && this.runs.length) {
        const cur = this.runs.find((r) => r.is_current);
        this.selectedRunId = cur ? cur.run_id : this.runs[this.runs.length - 1].run_id;
      }
    } catch (e: any) {
      this.err = String(e);
    } finally {
      this.loadingRuns = false;
    }
  },

  // 发起一次「去证据页看这一格」的跳转。runId 是这一格所属的轮次（实时轮次或历史执行记录里
  // 记的那一轮）：证据页锚在 selectedRunId 上，先把批次切过去，Evidence 挂载时才读得到对的那份
  // evidence.csv（后端 evidence_file_for 会按 run_id 去 archive/ 里找归档批次）。runId 传空或
  // 不在批次列表里（早于 RunRecordMeta.runId 字段存的旧执行记录就是空）→ 保持当前批次不动，
  // 由 Evidence 侧在找不到目标时给提示，而不是悄悄切到一个不相干的轮次。
  requestEvidence(serial: string, caseId: string, runId?: string, startedAt = 0, attempt = "") {
    if (runId && this.runs.some((r) => r.run_id === runId)) this.selectedRunId = runId;
    this.evidenceJump = { serial, caseId, startedAt, attempt };
  },

  selectedRun(): RunRow | undefined {
    return this.runs.find((r) => r.run_id === this.selectedRunId);
  },
});
