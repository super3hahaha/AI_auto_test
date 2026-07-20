// 后端命令的类型化封装。所有与 Rust 的通信都走这里。
// 多 App：读/执行类命令都带 appSlug（tauri 自动把 camelCase 映射到 Rust 的 snake_case）。
import { invoke, Channel, convertFileSrc } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";

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
}
export interface ApkInfo {
  package: string;
  version: string;
  label: string;
  suggested_slug: string;
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
  script: string;
  has_flow: boolean;
  last_result: string;
  last_status: string;
  start_time: string;
  end_time: string;
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
  setTargetSerial: (slug: string, serial: string) =>
    invoke<void>("set_target_serial", { appSlug: slug, serial }),
  readSummary: (slug: string) => invoke<KV[]>("read_summary", { appSlug: slug }),

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

  // 上传 APK：本地解析 → 装机 → 注册
  pickApk: () =>
    open({ multiple: false, filters: [{ name: "APK", extensions: ["apk"] }] }) as Promise<string | null>,
  probeApk: (apkPath: string) => invoke<ApkInfo>("probe_apk", { apkPath }),
  installApk(apkPath: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("install_apk", { apkPath, serial, onEvent: ch });
  },
  registerApp(slug: string, pkg: string, serial: string, onLine: (line: string) => void) {
    const ch = new Channel<string>();
    ch.onmessage = onLine;
    return invoke<number>("register_app", { appSlug: slug, package: pkg, serial, onEvent: ch });
  },
};

// 本地文件 → webview 可加载的 asset URL（证据图片用）
export const fileSrc = (absPath: string) => convertFileSrc(absPath);
