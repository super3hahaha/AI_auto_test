// 后端命令的类型化封装。所有与 Rust 的通信都走这里。
// 多 App：读/执行类命令都带 appSlug（tauri 自动把 camelCase 映射到 Rust 的 snake_case）。
import { invoke, Channel, convertFileSrc } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import type { RunRecord, RunRecordMeta } from "./runStore";

export interface AppConfig {
  project_root: string;
  python: string;
  configured: boolean;
}
export interface AppInfo {
  slug: string;
  app_name: string;
  package: string;
  app_version: string;
  sheet_id: string;
  serial: string;
  updated_at: number;
}
export interface ApkInfo {
  package: string;
  version: string;
  label: string;
  suggested_slug: string;
}
export interface ApkVersionInfo {
  version: string;
  path: string;
  size: number;
  imported_at: number;
}
export interface RunRow {
  run_id: string;
  date: string;
  title: string;
  sheet_id: string;
  url: string;
  doc_id: string;
  doc_url: string;
  is_current: boolean;
}
export interface EvidenceRow {
  case_id: string;
  step: string;
  etype: string;
  path: string;
  abs_path: string;
  preview: string;
  assertion: string;
  result: string;
  collected_at: string;
  note: string;
  is_key: boolean;
  is_image: boolean;
}
export interface FlowRow {
  case_id: string;
  module: string;
  purpose: string;
  priority: string;
  script: string;
  has_flow: boolean;
  last_result: string;
  last_status: string;
  start_time: string;
  end_time: string;
  steps: string[];
  expected: string[];
}
export interface DeviceRow {
  serial: string;
  state: string;
  model: string;
  alias: string;
  is_default: boolean;
  os_version: string;
}
export interface KV {
  key: string;
  value: string;
}
export interface StructureRow {
  module: string;
  purpose: string;
  count: string;
  cases: string;
  priority: string;
}
export interface ResourceFile {
  name: string;
  size: number;
}
export interface TextResource {
  key: string;
  value: string;
}
export interface CleanupItem {
  rel_path: string;
  name: string;
  size: number;
  modified: number; // unix 秒，0=未知
  tag: string; // "" | "当前批次" | "可重装"
  protected: boolean;
}
export interface CleanupCategory {
  key: string; // evidence / apks / records / cache / build
  title: string;
  hint: string;
  location: string;
  total_size: number;
  items: CleanupItem[];
}
export interface CleanupReport {
  project_size: number;
  categories: CleanupCategory[];
}
export interface CleanupResult {
  removed: number;
  freed: number;
  errors: string[];
}
export interface ClaudeCliStatus {
  installed: boolean;
  path: string;
  version: string;
  logged_in: boolean;
  detail_parsed: boolean;
  email: string;
  display_name: string;
  org_name: string;
  subscription: string;
}

export const api = {
  // app 自身配置（项目根 + python）——与被测 App 无关
  getAppConfig: () => invoke<AppConfig>("get_app_config"),
  setAppConfig: (project_root: string, python: string) =>
    invoke<AppConfig>("set_app_config", { projectRoot: project_root, python }),

  // App 注册表 / 活跃 App
  listApps: () => invoke<AppInfo[]>("list_apps"),
  // 返回值是回收站里的目标路径（apps/.trash/<slug>__<ts>/），手滑误删可以照这个路径挪回来
  deleteApp: (slug: string) => invoke<string>("delete_app", { slug }),
  getActiveApp: () => invoke<string>("get_active_app"),
  setActiveApp: (slug: string) => invoke<void>("set_active_app", { slug }),

  // 以下读类命令按当前选中 App（slug）取数
  readTargetConfig: (slug: string) => invoke<any>("read_target_config", { appSlug: slug }),
  listRuns: (slug: string) => invoke<RunRow[]>("list_runs", { appSlug: slug }),
  readEvidence: (slug: string, runId: string) =>
    invoke<EvidenceRow[]>("read_evidence", { appSlug: slug, runId }),
  readTextFile: (relPath: string) => invoke<string>("read_text_file", { relPath }),
  listFlows: (slug: string) => invoke<FlowRow[]>("list_flows", { appSlug: slug }),
  listDevices: (slug: string) => invoke<DeviceRow[]>("list_devices", { appSlug: slug }),
  // 序列号→别名映射（纯读 config/device_aliases.json，不依赖设备在线）；证据按设备分组显示友好名用
  readDeviceAliases: () => invoke<KV[]>("read_device_aliases"),
  setTargetSerial: (slug: string, serial: string) =>
    invoke<void>("set_target_serial", { appSlug: slug, serial }),
  setTargetScope: (slug: string, scope: string) =>
    invoke<void>("set_target_scope", { appSlug: slug, scope }),
  readSummary: (slug: string) => invoke<KV[]>("read_summary", { appSlug: slug }),
  readStructure: (slug: string) => invoke<StructureRow[]>("read_structure", { appSlug: slug }),

  // 设备别名登记（config/device_aliases.json）的增删改 + 导入导出
  upsertDeviceAlias: (serial: string, alias: string) =>
    invoke<void>("upsert_device_alias", { serial, alias }),
  deleteDeviceAlias: (serial: string) => invoke<void>("delete_device_alias", { serial }),
  pickExportDevicesPath: () =>
    save({ defaultPath: "device_aliases.json", filters: [{ name: "JSON", extensions: ["json"] }] }) as Promise<
      string | null
    >,
  exportDeviceAliases: (path: string) => invoke<number>("export_device_aliases", { path }),
  pickImportDevicesPath: () =>
    open({ multiple: false, filters: [{ name: "JSON", extensions: ["json"] }] }) as Promise<string | null>,
  importDeviceAliases: (path: string) => invoke<number>("import_device_aliases", { path }),

  // 测试资源（assets/，所有 App 共用）：上传/列表/删除，固化脚本用相对路径 assets/<文件名> 引用
  listResourceFiles: () => invoke<ResourceFile[]>("list_resource_files"),
  pickResourceFile: () => open({ multiple: false }) as Promise<string | null>,
  uploadResourceFile: (srcPath: string) => invoke<ResourceFile>("upload_resource_file", { srcPath }),
  deleteResourceFile: (name: string) => invoke<void>("delete_resource_file", { name }),

  // 文本资源（config/text_resources.json，所有 App 共用）：key-value 登记，固化脚本用 key 取值
  listTextResources: () => invoke<TextResource[]>("list_text_resources"),
  upsertTextResource: (key: string, value: string) => invoke<void>("upsert_text_resource", { key, value }),
  deleteTextResource: (key: string) => invoke<void>("delete_text_resource", { key }),

  // Claude CLI 安装/登录状态（「脚本自愈」功能依赖它）
  checkClaudeCli: () => invoke<ClaudeCliStatus>("check_claude_cli"),

  // 流式：返回 promise（resolve 退出码）；onLine 收每行日志
  runFlow(slug: string, caseId: string, script: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("run_flow", { appSlug: slug, caseId, script, serial, onEvent: ch });
  },
  // 「脚本自愈」执行（失败自动交 claude 诊断+改脚本重跑，至多 3 次）
  runFlowRepair(slug: string, caseId: string, script: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("run_flow_repair", { appSlug: slug, caseId, script, serial, onEvent: ch });
  },
  // 中止当前正在跑的 run（kill 其进程组）；返回是否有任务被中止
  abortRun: () => invoke<boolean>("abort_run"),
  newRun(slug: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("new_run", { appSlug: slug, onEvent: ch });
  },
  // 同步到线上表格：推当前 App 本地 ledger → Google Sheets（执行台收尾自动调）
  syncSheets(slug: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("sync_sheets", { appSlug: slug, onEvent: ch });
  },
  // 落账本（judge_result）：跑完一格 run_flow/run_flow_repair 后必须调——不调的话这条用例的
  // queue.csv「当前状态」永远停在「待执行」（run_flow.py 只写 log.csv/时间戳，不写状态列），
  // 账本/Doc/Sheet 会把它当成没跑过。纯确定性映射，不调 claude：pass/healed→通过，
  // fail→失败（固化脚本自己已按 exit 码判过，不豁免已知缺陷），app_defect/needs_human→需复核。
  judgeResult(
    slug: string,
    caseId: string,
    serial: string,
    status: "pass" | "healed" | "fail" | "app_defect" | "needs_human",
    onLine: (line: string) => void
  ) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("judge_result", { appSlug: slug, caseId, serial, status, onEvent: ch });
  },
  // 自动登记问题清单（issue_register）：收尾时对失败/需复核的用例逐条调——headless claude 读证据
  // 把一条结构化问题写进 issues.csv（前缀由终态确定性映射：fail/app_defect→BUG-、needs_human→RISK-，
  // claude 只写描述字段+查重，见 decisions.md #35）。必须排在 syncSheets/docReport 之前（要读 issues.csv）。
  registerIssue(
    slug: string,
    caseId: string,
    serial: string,
    status: "fail" | "app_defect" | "needs_human",
    onLine: (line: string) => void
  ) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("register_issue", { appSlug: slug, caseId, serial, status, onEvent: ch });
  },
  // 刷新 Google Doc 图文报告（doc_report）：执行台收尾时紧跟 syncSheets 之后自动调
  docReport(slug: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("doc_report", { appSlug: slug, onEvent: ch });
  },

  // 上传 APK：本地解析 → 装机 → 注册
  pickApk: () =>
    open({ multiple: false, filters: [{ name: "APK", extensions: ["apk"] }] }) as Promise<string | null>,
  probeApk: (apkPath: string) => invoke<ApkInfo>("probe_apk", { apkPath }),
  installApk(apkPath: string, pkg: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("install_apk", { apkPath, package: pkg, serial, onEvent: ch });
  },
  registerApp(slug: string, pkg: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("register_app", { appSlug: slug, package: pkg, serial, onEvent: ch });
  },

  // 同 slug 下留存的多版本 APK：留存（上传时复制一份）+ 列出（执行前选版本装机用）
  listApkVersions: (slug: string) => invoke<ApkVersionInfo[]>("list_apk_versions", { slug }),
  saveApkVersion: (slug: string, srcPath: string, version: string) =>
    invoke<string>("save_apk_version", { slug, srcPath, version }),

  // 执行记录：完整跑完（未中止）的一轮执行台快照持久化（apps/<slug>/ledger/run_records/）
  saveRunRecord: (slug: string, record: RunRecord) =>
    invoke<void>("save_run_record", { appSlug: slug, record }),
  listRunRecords: (slug: string) => invoke<RunRecordMeta[]>("list_run_records", { appSlug: slug }),
  readRunRecord: (slug: string, id: string) =>
    invoke<RunRecord>("read_run_record", { appSlug: slug, id }),
  deleteRunRecord: (slug: string, id: string) =>
    invoke<void>("delete_run_record", { appSlug: slug, id }),

  // 清理：扫描历史文件（跨 App，不带 slug）+ 把选中项移进系统废纸篓
  scanCleanup: () => invoke<CleanupReport>("scan_cleanup"),
  moveToTrash: (relPaths: string[]) => invoke<CleanupResult>("move_to_trash", { relPaths }),
};

// 本地文件 → webview 可加载的 asset URL（证据图片用）
export const fileSrc = (absPath: string) => convertFileSrc(absPath);
