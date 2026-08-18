// 录制器 V2 的 WS 客户端：连 recorder_daemon、分发消息。有意保持"薄"——重连/降级策略在
// Recorder.vue（它才知道 UI 该进哪个状态），这里只管一条连接的生命周期与编解码。
import type { ClientMsg, DaemonMsg, RecSessionInfo } from "./types";

export class RecorderSession {
  private ws: WebSocket | null = null;
  private closedByUs = false;

  onMessage: (m: DaemonMsg) => void = () => {};
  /** 0x01 视频帧：flags bit0=config, bit1=keyframe；payload 是一个完整 H.264 access unit */
  onVideoPacket: (flags: number, pts: number, payload: Uint8Array) => void = () => {};
  /** 连接断开（非本端主动关）时回调；Recorder.vue 决定重连还是降级 legacy */
  onDrop: () => void = () => {};

  connect(info: RecSessionInfo): Promise<void> {
    this.closedByUs = false;
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`ws://127.0.0.1:${info.port}/ws?token=${info.token}`);
      ws.binaryType = "arraybuffer"; // 默认 blob，每帧都要 await 转换，白丢一拍
      this.ws = ws;
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("录制服务 WS 连接失败"));
      ws.onclose = () => {
        if (!this.closedByUs) this.onDrop();
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          const u8 = new Uint8Array(ev.data as ArrayBuffer);
          if (u8.length < 10 || u8[0] !== 0x01) return;
          // [type u8][flags u8][pts u64be][payload]；pts 高位标志已在 daemon 侧剥掉
          const dv = new DataView(u8.buffer, u8.byteOffset);
          const pts = Number(dv.getBigUint64(2));
          this.onVideoPacket(u8[1], pts, u8.subarray(10));
          return;
        }
        try {
          this.onMessage(JSON.parse(ev.data) as DaemonMsg);
        } catch {
          /* 非法消息忽略，别让一条坏包毁掉会话 */
        }
      };
    });
  }

  send(m: ClientMsg) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(m));
  }

  /** 0x11 shot 上传：[u8 type][u32be n][u16be case_len][case utf8][PNG]。
      视频模式下每步截图从 canvas capture 回传（时刻与该步 diff 对齐、零设备开销）。 */
  sendShot(n: number, caseId: string, png: ArrayBuffer) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const c = new TextEncoder().encode(caseId);
    const out = new Uint8Array(7 + c.length + png.byteLength);
    const dv = new DataView(out.buffer);
    out[0] = 0x11;
    dv.setUint32(1, n);
    dv.setUint16(5, c.length);
    out.set(c, 7);
    out.set(new Uint8Array(png), 7 + c.length);
    this.ws.send(out);
  }

  get connected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  close() {
    this.closedByUs = true;
    this.ws?.close();
    this.ws = null;
  }
}
