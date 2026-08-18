// WebCodecs 视频管线：scrcpy H.264 access unit → VideoDecoder → canvas。
//
// 路线 A（已在 WKWebView 上用真机码流 spike 验证：avc1.640032 1008x2244 解码成功）：
// config packet 切出 SPS/PPS → 构 avcC 作 description → 帧 payload 从 Annex-B 起始码改写成
// 4 字节大端长度前缀（AVCC）→ EncodedVideoChunk 喂 decode。scrcpy 无限 GOP：解码器出错时
// 唯一正确的恢复是让 daemon 重启取流（onNeedKeyframe → {t:"requestKeyframe"}）。
//
// canvas 尺寸恒等于**设备逻辑分辨率**（videoMeta.device），帧画上去时拉伸对齐——视频尺寸有
// 编码器对齐（≠设备尺寸，差最多 7px），控件框 bounds 是设备坐标系，基准必须统一到设备尺寸
// （"w/h 是包围盒不是屏幕尺寸"老坑的新形态）。拉伸量 ≤7px，肉眼不可见。

export class VideoPipe {
  private decoder: VideoDecoder | null = null;
  private configured = false;
  private errCount = 0;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private deviceW = 0;

  /** 解码器连续出错（或不可恢复）时回调：外层发 requestKeyframe / 连挂 3 次降级 still */
  onNeedKeyframe: () => void = () => {};
  onFatal: () => void = () => {};
  /** 每帧画完回调一次（外层用来标记"视频已在流"） */
  onFrame: () => void = () => {};

  static supported() {
    return typeof VideoDecoder !== "undefined";
  }

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d")!;
  }

  setDeviceSize(w: number, h: number) {
    this.deviceW = w;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
  }

  /** 从 session.ts 的 0x01 二进制帧进来：flags bit0=config, bit1=keyframe */
  push(flags: number, pts: number, payload: Uint8Array) {
    try {
      if (flags & 1) return this.configure(payload);
      if (!this.configured || !this.decoder || this.decoder.state === "closed") return;
      this.decoder.decode(new EncodedVideoChunk({
        type: flags & 2 ? "key" : "delta",
        timestamp: pts,
        data: toAvcc(payload) as BufferSource,
      }));
    } catch (e) {
      this.fail(String(e));
    }
  }

  private configure(configPayload: Uint8Array) {
    const nals = splitAnnexB(configPayload);
    const sps = nals.find((n) => (n[0] & 0x1f) === 7);
    const pps = nals.find((n) => (n[0] & 0x1f) === 8);
    if (!sps || !pps) return this.fail("config packet 里没有 SPS/PPS");
    const codec = "avc1." + [sps[1], sps[2], sps[3]].map((x) => x.toString(16).padStart(2, "0")).join("");
    this.decoder?.close();
    this.decoder = new VideoDecoder({
      output: (frame) => {
        this.errCount = 0;
        // 拉到设备逻辑分辨率（基准统一，见文件头注）；device 尺寸未知时按帧尺寸兜底
        if (!this.deviceW) this.setDeviceSize(frame.displayWidth, frame.displayHeight);
        this.ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height);
        frame.close();
        this.onFrame();
      },
      error: (e) => this.fail(e.message),
    });
    this.decoder.configure({ codec, description: buildAvcC(sps, pps) as BufferSource, optimizeForLatency: true });
    this.configured = true;
  }

  private fail(why: string) {
    console.warn("[videoPipe]", why);
    this.configured = false;
    this.errCount++;
    if (this.errCount >= 3) this.onFatal();
    else this.onNeedKeyframe();
  }

  destroy() {
    try {
      this.decoder?.close();
    } catch { /* 已 closed */ }
    this.decoder = null;
    this.configured = false;
  }
}

/** Annex-B → NALU 列表（支持 3/4 字节起始码） */
function splitAnnexB(u8: Uint8Array): Uint8Array[] {
  const out: Uint8Array[] = [];
  let i = 0, start = -1;
  const sc = (j: number) =>
    u8[j] === 0 && u8[j + 1] === 0 && u8[j + 2] === 1 ? 3
    : u8[j] === 0 && u8[j + 1] === 0 && u8[j + 2] === 0 && u8[j + 3] === 1 ? 4 : 0;
  while (i < u8.length) {
    const n = sc(i);
    if (n) {
      if (start >= 0) out.push(u8.slice(start, i));
      start = i + n;
      i += n;
    } else i++;
  }
  if (start >= 0) out.push(u8.slice(start));
  return out;
}

/** Annex-B 起始码 → 4 字节大端长度前缀（AVCC），description 模式下 chunk 必须是这个格式 */
function toAvcc(u8: Uint8Array): Uint8Array {
  const nals = splitAnnexB(u8);
  let len = 0;
  for (const n of nals) len += 4 + n.length;
  const out = new Uint8Array(len);
  const dv = new DataView(out.buffer);
  let o = 0;
  for (const n of nals) {
    dv.setUint32(o, n.length);
    out.set(n, o + 4);
    o += 4 + n.length;
  }
  return out;
}

/** SPS/PPS → avcC box（version1 + profile/compat/level 取自 SPS[1..3] + lengthSize=4） */
function buildAvcC(sps: Uint8Array, pps: Uint8Array): Uint8Array {
  const out = new Uint8Array(11 + sps.length + pps.length);
  const dv = new DataView(out.buffer);
  out[0] = 1;
  out[1] = sps[1];
  out[2] = sps[2];
  out[3] = sps[3];
  out[4] = 0xff; // lengthSizeMinusOne=3（4 字节长度前缀）
  out[5] = 0xe1; // 1 个 SPS
  dv.setUint16(6, sps.length);
  out.set(sps, 8);
  let o = 8 + sps.length;
  out[o] = 1; // 1 个 PPS
  dv.setUint16(o + 1, pps.length);
  out.set(pps, o + 3);
  return out;
}
