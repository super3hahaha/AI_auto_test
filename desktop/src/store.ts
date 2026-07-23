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

  selectedRun(): RunRow | undefined {
    return this.runs.find((r) => r.run_id === this.selectedRunId);
  },
});
