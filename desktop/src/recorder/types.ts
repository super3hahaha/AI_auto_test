// 录制器 V2 WS 协议类型（与 tools/recorder_daemon.py 的 JSON 契约一一对应，改一边要改两边）。
// RecScreen/RecStep/RecNode 等基础形状沿用 api.ts（与 legacy CLI 共用），这里只定义信封。
import type { RecScreen, RecStep } from "../api";

/** daemon → 前端 */
export type DaemonMsg =
  | { t: "hello"; serial: string; app: string; pkg: string; backend: string | null; video: boolean }
  // w/h 是编码器输出的视频尺寸（有对齐，仅供参考）；device 才是画框基准（设备逻辑分辨率）
  | { t: "videoMeta"; codec: string; w: number; h: number; device: { w: number; h: number } | null }
  | { t: "hierarchy"; seq: number; hash: string; cause: "act" | "refresh" | "poll"; screen: RecScreen }
  | { t: "step"; step: RecStep }
  | { t: "stepDiff"; n: number; diff: { appeared: string[]; disappeared: string[] }; auto_swept: number }
  | { t: "shot"; seq: number; n: number; png: string; png_err: string; shot_w: number; shot_h: number }
  | { t: "swept"; rule_id: string; by: string; value: string }
  // backfilled：export 时缺图被 screencap 补拍的步骤号（图的时刻是导出当下，不是那一步执行时）
  | { t: "exported"; dir: string; rec: string; flow: string; steps: number; shots: number; backfilled?: number[] }
  | { t: "status"; [k: string]: unknown }
  | { t: "error"; scope: "act" | "dump" | "video" | "export"; message: string };

/** 前端 → daemon */
export type ClientMsg =
  | { t: "act"; kind: string; body: Record<string, unknown>; case: string; n: number; auto_sweep: boolean }
  | { t: "refresh"; auto_sweep: boolean }
  | { t: "export"; case: string; steps: RecStep[] }
  | { t: "requestKeyframe" }
  // 前端解不出视频（WebCodecs 不支持 / config 丢 / 解码器连挂 / 首帧超时）→ 让 daemon 关流、
  // 恢复 screencap 供图。不发这条的话 daemon 会一直以为"画面有人管"而不拍图（黑屏 0 框）。
  | { t: "videoMode"; on: boolean; why?: string };

export interface RecSessionInfo {
  port: number;
  token: string;
  video: boolean;
}
