<script setup lang="ts">
// 录制器：人在这儿点一遍真机，落成「选择器序列 + 每步前后屏 diff」的录制文件 + flow 草稿。
//
// 为什么值得有这个 tab：固化脚本的**路径**以前靠 AI 一屏一屏 dump + 推理探（贵），而路径本身是人
// 早就知道的。这里把「找路」交给人（点几下），AI 只做它擅长的：把每步 diff 翻成预期、补
// output-check/logscan/踩坑注释。产物是中间物，最终资产仍是 cases/*.yaml + flows/flow_*.sh。
//
// 三条不可妥协的设计（后端 tools/recorder.py 里同样注释）：
//  1. 动作全经 adbkit 下发 —— 录制走的代码路径 == 回放走的代码路径，录得通基本等于脚本跑得通；
//  2. 点击只记选择器不记坐标（坐标 adbkit 每次从实时 UI 树现算，脚本才跨分辨率）。自身
//     id/text/desc 全空的控件退回「父锚 + --child 路径」，仍然不是硬坐标；
//  3. 每步自动 diff 前后两屏的可见文案 —— appeared 直接当 waitfor 目标 / YAML expected 素材。
//
// 状态放在这里（步骤列表、当前屏），后端三个子命令是无状态的：act 时把上一屏 labels 回传当
// diff 基线，省掉一次探屏（见 api.recAct / Rust recorder_cmd）。
//
// 本视图在 App.vue 里被 keep-alive 保活（录到一半切去看设备/证据，回来步骤还在）：切回走
// onActivated，onMounted 只有首次跑。要拿 DOM 一律用模板 ref，不要 document.querySelector(".stage")
// —— 被缓存的 DOM 仍挂在文档里，而 Evidence.vue 也有个 .stage，全局选择器会量到它身上。
import { ref, computed, onMounted, onActivated, watch, nextTick } from "vue";
import { confirm } from "@tauri-apps/plugin-dialog";
import { api, type DeviceRow, type RecScreen, type RecNode, type RecStep, type RecSel } from "../api";
import { store } from "../store";
import { runStore } from "../runStore";
import { RecorderSession } from "../recorder/session";
import { VideoPipe } from "../recorder/videoPipe";
import type { DaemonMsg } from "../recorder/types";

const devices = ref<DeviceRow[]>([]);
const serial = ref("");
// 正在跑回归的设备：录制和回归抢同一份 uiautomator 会话，不能并存（见 docs/gotchas.md）。
const busySerials = computed(() => new Set(runStore.runningSerials()));
const caseId = ref("");
const screen = ref<RecScreen | null>(null);
const steps = ref<RecStep[]>([]);
const mode = ref<"tap" | "longpress" | "swipe" | "longdrag" | "xy">("tap");
const busy = ref("");
const err = ref("");
const msg = ref("");
// 清障这类"过程噪音"不进 msg：msg 是常驻横幅，会把下面整块内容顶下去。走浮层 toast，
// 绝对定位不占高度，1s 自动消失（清障本身不需要人确认，只是让人知道刚发生过）
const toast = ref("");
let toastTimer: number | undefined;
function showToast(t: string) {
  toast.value = t;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { toast.value = ""; }, 1000);
}
const ambig = ref<RecNode | null>(null);
const ambigPick = ref(0);
const ambigKind = ref<"tap" | "longpress">("tap"); // 消歧弹层确认时要用触发它那一刻的 mode，不能事后固定成 tap
const shot = ref<HTMLImageElement | null>(null);
const stage = ref<HTMLElement | null>(null);
const nat = ref<[number, number] | null>(null); // 截图真实像素，框定位的基准
const line = ref<{ x: number; y: number; len: number; deg: number } | null>(null);
// 默认开：录制器遇到已知广告 SDK 全屏页（scope 卡死，见 recorder.py AD_RULE_IDS）自动清一遍再
// 呈现当前屏，不用人眼看到广告手动点「清障」。留开关是防万一——真出现误判，随手关掉退回手动。
const autoSweep = ref(true);
// 录制结束后人要慢慢看/整理步骤，此时视频流+自动刷屏还在跑纯属打扰（且真机上持续占用 scrcpy/u2）。
// 「停止录制」只掐这条：断开 daemon 会话、停视频，画面定格在最后一屏；已录步骤原样保留仍可导出。
// act() 统一在入口挡这一道，覆盖所有触发点（工具栏按钮/点框/滑动/文本），不用逐个按钮加 disabled。
const stopped = ref(false);

// ── 录制器 V2 会话（常驻 daemon + WS）────────────────────────────────────────
// wsMode=true：动作/探屏/导出全走 WS（步骤卡 ~0.2s、新框 ~1s）；daemon 起不来（python 缺
// websockets 等）自动降级 legacy——下面 probe/act/doExport 里各自分流，UI 完全一样只是慢。
const sess = new RecorderSession();
const wsMode = ref(false);
// stale：发出动作后到新控件框到达之间，旧框对应的树已过时——画面压暗+框禁点，防"照着旧框点错"
// （这是旧架构"截图与 dump 不同时刻"坑在异步架构下的等价物，见 docs/gotchas.md 2026-08-03）
const stale = ref(false);
const connState = ref(""); // ""=未连 / "ws"=V2 / "legacy"=降级（界面上要让用户知道现在是哪条链路）
let lastShotSeq = 0;

// ── 视频流（scrcpy → WebCodecs → canvas）────────────────────────────────────
// videoActive 只在**第一帧真的画出来**后才置 true（daemon 声称有视频 ≠ 解码成功），在那之前
// 取屏区继续显示 shot 静态图；解码器连挂 3 次自动退回静态图模式，产品可用性不依赖视频流。
const videoCanvas = ref<HTMLCanvasElement | null>(null);
const videoActive = ref(false);
const deviceWH = ref<[number, number] | null>(null); // 设备逻辑分辨率（画框基准，≠视频尺寸）
let pipe: VideoPipe | null = null;

// ── 画面变化 ↔ 控件框失效的对齐 ─────────────────────────────────────────────
// 视频是连续的、树是离散 dump 的：过渡动画期间框对应的还是上一屏（用户看到"虚线位置不对"）。
// scrcpy 画面不变就不发帧 ⇒ 「有帧到达」==「画面在变」。据此：
//  · 画面在动 → motionStale 压暗禁点（跟 act 后的 stale 同款视觉），但持续动画（广告 banner
//    这类永远在动）超过 3s 就不再压——树对 banner 以外的区域仍然有效，一直压反而没法录；
//  · 画面停稳 400ms → 立刻主动要一份新树（限频 1.2s），不用干等 3s 空闲巡屏。
const motionStale = ref(false);
let lastFrameAt = 0;
let motionStart = 0;
let quietTimer: ReturnType<typeof setTimeout> | undefined;
let lastAutoRefresh = 0;

function onVideoMotion() {
  const t = performance.now();
  if (t - lastFrameAt > 600) motionStart = t; // 静了一阵又来帧 = 新一波画面变化
  lastFrameAt = t;
  if (t - motionStart < 3000) motionStale.value = true;
  else motionStale.value = false; // 持续动画（banner）：3s 后视为"树基本稳定"，恢复可点
  clearTimeout(quietTimer);
  quietTimer = setTimeout(() => {
    motionStale.value = false;
    // 画面刚停稳：树多半已变，主动刷一份（act 触发的刷新有自己的流程，stale 时不重复要）
    if (!wsMode.value || stale.value) return;
    const now = Date.now();
    if (now - lastAutoRefresh < 1200) return;
    lastAutoRefresh = now;
    sess.send({ t: "refresh", auto_sweep: autoSweep.value });
  }, 400);
}

// 首帧看门狗：daemon 报了 videoMeta（= scrcpy 在推流）就开始计时，到点还没画出第一帧就判定
// "这条链路前端解不出"，主动让 daemon 关流回退 screencap。缺了它就是纯软失败——没有异常、没有
// 报错、画面永远黑、控件框一个都不画（框的定位基准 base 只认真实画面尺寸），用户只看到"探屏失败"。
const VIDEO_FIRSTFRAME_MS = 4000;
let videoWatchdog: number | undefined;
function armVideoWatchdog() {
  clearTimeout(videoWatchdog);
  videoWatchdog = window.setTimeout(() => {
    if (videoActive.value || !wsMode.value) return;
    fallbackToStill(`视频流 ${VIDEO_FIRSTFRAME_MS / 1000}s 没出第一帧`);
  }, VIDEO_FIRSTFRAME_MS);
}
function fallbackToStill(why: string) {
  clearTimeout(videoWatchdog);
  disarmVideoFreezeWatch();
  pipe?.destroy();
  pipe = null;
  videoActive.value = false;
  sess.send({ t: "videoMode", on: false, why });
  msg.value = `${why}，已切静态截图模式（画面刷新变慢，录制/导出不受影响）。想换回实时视频：重进本页。`;
}

// 持续性看门狗：首帧看门狗只管"从没出过画面"，videoActive 一旦变 true 就再没人盯着——流后续
// 静默卡死时（daemon 那边 readexactly 卡死不报错的老问题已经在 recorder_daemon.py 加了超时自愈，
// 但前端解码器自己丢同步、包在收却画不出新帧这类纯前端故障，daemon 那头压根不知道）前端会一直
// 显示最后一帧、框却照常刷新，用户分不清"卡住"和"画面本来没变"。用定期检查代替：超过阈值没收
// 到新帧先请求关键帧（成本低，daemon 流本身没断的话重启很快恢复）；连续两轮还没有才判定彻底
// 断流退回静态图。阈值给宽（6s）——纯静止画面本来就可能好几秒没有新帧，不是异常。
const VIDEO_FREEZE_MS = 6000;
let videoFreezeTimer: ReturnType<typeof setInterval> | undefined;
let freezeStrikes = 0;
function armVideoFreezeWatch() {
  clearInterval(videoFreezeTimer);
  freezeStrikes = 0;
  videoFreezeTimer = window.setInterval(() => {
    if (!videoActive.value || !wsMode.value) return;
    const idleMs = performance.now() - lastFrameAt;
    if (idleMs < VIDEO_FREEZE_MS) { freezeStrikes = 0; return; }
    freezeStrikes++;
    if (freezeStrikes === 1) sess.send({ t: "requestKeyframe" });
    else fallbackToStill(`视频流 ${Math.round(idleMs / 1000)}s 无新帧`);
  }, VIDEO_FREEZE_MS);
}
function disarmVideoFreezeWatch() {
  clearInterval(videoFreezeTimer);
  freezeStrikes = 0;
}

function ensurePipe(): VideoPipe | null {
  if (!VideoPipe.supported() || !videoCanvas.value) return null;
  if (!pipe) {
    pipe = new VideoPipe(videoCanvas.value);
    pipe.onFrame = () => {
      if (!videoActive.value) { videoActive.value = true; clearTimeout(videoWatchdog); armVideoFreezeWatch(); }
      onVideoMotion();
    };
    pipe.onNeedKeyframe = () => sess.send({ t: "requestKeyframe" });
    // 解码连挂：光把前端切静态没用——daemon 那边 scrcpy 还活着就不会 screencap，
    // 结果是"退回静态"却一张图都收不到。必须同时通知 daemon 关流。
    pipe.onFatal = () => fallbackToStill("视频解码连续失败");
  }
  return pipe;
}
sess.onVideoPacket = (flags, pts, payload) => ensurePipe()?.push(flags, pts, payload);

// canvas 元素在首次 hierarchy 到达后才随 .frame 挂载——config packet 若在此之前到达会被丢
// （pipe 还建不出来），后续 delta 全解不出。canvas 一就绪就主动要一次关键帧（daemon 重启取流
// ~1s，重发 config+IDR），把这个启动竞态抹平。
watch(videoCanvas, (el, old) => {
  if (!el || old || !wsMode.value) return;
  // canvas 是被 videoMeta 带来的 deviceWH 挂上的：pipe 这时才建得出来，尺寸得补设一次
  if (deviceWH.value) ensurePipe()?.setDeviceSize(deviceWH.value[0], deviceWH.value[1]);
  sess.send({ t: "requestKeyframe" });
});

function teardownVideo() {
  clearTimeout(videoWatchdog);
  disarmVideoFreezeWatch();
  pipe?.destroy();
  pipe = null;
  videoActive.value = false;
  deviceWH.value = null;
  clearTimeout(quietTimer);
  motionStale.value = false;
}

function onDaemonMsg(m: DaemonMsg) {
  if (m.t === "videoMeta") {
    deviceWH.value = [m.device.w, m.device.h];
    ensurePipe()?.setDeviceSize(m.device.w, m.device.h);
    if (!videoActive.value) armVideoWatchdog();
  } else if (m.t === "hierarchy") {
    // 图先不动（新图由紧随的 shot 消息带来）：bounds 是设备坐标，同一朝向下基准不变，
    // 新框叠旧图的空窗被 stale 压暗明示，不冒充"已同步"
    const prev = screen.value;
    screen.value = { ...m.screen, png: prev?.png ?? "", png_err: prev?.png_err ?? "",
                     shot_w: prev?.shot_w ?? 0, shot_h: prev?.shot_h ?? 0 };
    stale.value = false;
    if (m.screen.auto_swept) showToast(`已自动清障 ${m.screen.auto_swept} 次（广告全屏页）`);
  } else if (m.t === "shot") {
    if (m.seq < lastShotSeq) return; // 乱序旧图丢弃（截图异步，可能晚于下一轮 hierarchy）
    lastShotSeq = m.seq;
    if (screen.value) {
      screen.value = { ...screen.value, png: m.png, png_err: m.png_err,
                       shot_w: m.shot_w, shot_h: m.shot_h };
    }
  } else if (m.t === "step") {
    steps.value.push(m.step);
    busy.value = "";
  } else if (m.t === "stepDiff") {
    const s = steps.value.find((x) => x.n === m.n);
    if (s) { s.diff = m.diff; s.auto_swept = m.auto_swept; }
    // 视频模式：这一刻 = 该步稳定树刚到手，canvas 上的帧与 diff 对应同一时刻——正是
    // shots/NN.png 该定格的画面（still 模式后端自己 screencap，不用前端管）
    if (videoActive.value && videoCanvas.value && caseId.value.trim()) {
      const n = m.n, c = caseId.value.trim();
      videoCanvas.value.toBlob(async (blob) => {
        if (blob) sess.sendShot(n, c, await blob.arrayBuffer());
      }, "image/png");
    }
  } else if (m.t === "swept") {
    showToast(`自动清障命中 ${m.rule_id}（${m.by}=${m.value}）`);
  } else if (m.t === "exported") {
    busy.value = "";
    msg.value = `已导出 ${m.steps} 步 / ${m.shots} 张截图 → ${m.rec} · 脚本草稿 ${m.flow}（草稿没有任何判定，要补 output-check/logscan/FAILED 收尾）` +
      (m.backfilled?.length ? ` ⚠︎ 步骤 ${m.backfilled.join("、")} 的截图是导出时补拍的（时刻≠该步执行时）` : "");
  } else if (m.t === "error") {
    busy.value = "";
    stale.value = false;
    err.value = m.message;
  }
}
sess.onMessage = onDaemonMsg;
sess.onDrop = () => {
  wsMode.value = false;
  connState.value = "";
  teardownVideo();
  err.value = "录制服务连接断开（设备掉线/进程被杀）。点「开始」重连。";
};

async function startSession(): Promise<boolean> {
  if (!serial.value) return false;
  try {
    const info = await api.recSessionStart(store.activeSlug, serial.value);
    await sess.connect(info);
    wsMode.value = true;
    connState.value = "ws";
    return true;
  } catch (e: any) {
    // daemon 起不来 → 降级 legacy（慢但可用）。不吞原因：让用户知道怎么修回快路径。
    wsMode.value = false;
    connState.value = "legacy";
    msg.value = `录制服务未启动（${e}），已降级为逐次探屏模式（每步 ~5s）。修复后重进本页恢复。`;
    return false;
  }
}

function stopSession() {
  if (sess.connected) sess.close();
  if (connState.value === "ws" && serial.value) api.recSessionStop(serial.value).catch(() => {});
  wsMode.value = false;
  connState.value = "";
  stale.value = false;
  lastShotSeq = 0;
  teardownVideo();
}

function stopRecording() {
  stopSession();
  stopped.value = true;
}

function defaultCase() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `REC-${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

// 无线连接的 serial 形如 192.168.x.x:5555；USB 是纯序列号
const isWireless = (serial: string) => serial.includes(":");

async function loadDevices() {
  try {
    devices.value = await api.listDevices(store.activeSlug);
    const online = devices.value.filter((d) => d.state === "device");
    // 同一台设备常常 USB 和无线都连着（adb devices 会列两条、model 完全相同）。默认挑 USB 那条：
    // 实测同一台 Pixel_4 探一屏 USB 2.6s / 无线 4.7s，差一倍，而录制是每步都要探屏的。
    if (!online.some((d) => d.serial === serial.value)) {
      serial.value = (online.find((d) => !isWireless(d.serial)) || online[0])?.serial || "";
    }
  } catch (e: any) {
    // 不能静默：失败时下拉是空的，用户会以为"没连设备"，而真因可能是 adb 不在 PATH
    err.value = `读设备列表失败：${e}`;
  }
}

function devLabel(d: DeviceRow) {
  // 必须带上通道和 serial 尾段：同一台设备的 USB/无线两条 model 相同，只显示别名或型号的话
  // 下拉里是两个一模一样的选项，用户无从知道自己选的是哪条——而两条速度差一倍（踩过）。
  const base = d.alias || d.model || d.serial;
  const tag = isWireless(d.serial) ? "无线" : "USB";
  const tail = isWireless(d.serial) ? d.serial : d.serial.slice(-6);
  return `${base}（${tag} ${tail}）${d.state === "device" ? "" : ` ${d.state}`}`;
}

// 控件框定位的基准**只能**是截图真实像素。绝不退回 screen.w/h —— 那是节点 bounds 的包围盒，
// 前台是对话框时只有 1013x1373，而截图始终是整屏 1080x2280，误用它会把所有框整体放大 1.66 倍、
// 糊成盖住半屏的一块（真机踩过，桌面壳里复现、浏览器版没事，因为那边直接读的 naturalWidth）。
const base = computed<[number, number] | null>(() => {
  // 视频模式：canvas 的像素尺寸恒等于设备逻辑分辨率（videoPipe.setDeviceSize 保证），
  // bounds 也是设备坐标系——基准天然统一
  if (videoActive.value && deviceWH.value) return deviceWH.value;
  const sc = screen.value;
  if (sc?.shot_w && sc.shot_h) return [sc.shot_w, sc.shot_h]; // 后端从 PNG IHDR 读的权威值
  return nat.value; // 兜底：img 已加载时的真实像素。两者都没有就不画框，宁可不画也别画错
});

// 取屏区当前实际显示的元素（坐标换算/手势的量测对象）：视频模式是 canvas，静态模式是 img
const viewEl = computed<HTMLElement | null>(() => (videoActive.value ? videoCanvas.value : shot.value));

const boxes = computed(() => {
  const sc = screen.value;
  if (!sc || !base.value) return [];
  const [W, H] = base.value;
  // 同一块矩形上常常叠着好几个 bounds 完全相同的容器节点（对话框那屏就有 6 个：action_bar_root /
  // content / parentPanel / customPanel / custom / ViewGroup；普通页面的根 FrameLayout 也一样）。
  // 给它们各画一个框既没信息量、又叠成一坨，点击还不知道命中了哪个——每组只留一个：优先能唯一
  // 定位的，同等条件下取更内层的（节点是文档序，靠后 = 更深）。折叠掉几个在 tooltip 里说明。
  const rank = (n: RecNode) => (n.sels.length ? (n.sels[0].n === 1 ? 2 : 1) : 0);
  const keep = new Map<string, RecNode>();
  const dup = new Map<string, number>();
  for (const n of sc.nodes) {
    if (!n.sels.length && !n.anc) continue;
    const k = n.b.join(",");
    dup.set(k, (dup.get(k) || 0) + 1);
    const prev = keep.get(k);
    if (!prev || rank(n) >= rank(prev)) keep.set(k, n);
  }
  return [...keep.values()]
    // 面积大的排前面 → 小控件在 DOM 后面即在上层，点击自然命中最贴合的那个
    .sort((a, b) => (b.b[2] - b.b[0]) * (b.b[3] - b.b[1]) - (a.b[2] - a.b[0]) * (a.b[3] - a.b[1]))
    .map((n) => {
      const extra = (dup.get(n.b.join(",")) || 1) - 1;
      return {
        n,
        // 状态类必须带 b- 前缀：裸的 "ok" 会撞上本组件消息横幅的 .ok（margin:10px 0），
        // margin-top 会把带显式 top 的绝对定位框整体下移 10px（只偏 y 不偏 x，真机排查过一轮）
        cls: !n.sels.length ? "b-anc" : n.sels[0].n > 1 ? "b-amb" : "b-ok",
        // 百分比相对 .overlay，而 .overlay 用 inset:0 铺满被 img 撑开的 .frame ⇒ 与 img 严格
        // 同尺寸同位置，不经任何 JS 测量、也不依赖 aspect-ratio 的实现细节
        style: {
          left: `${(n.b[0] / W) * 100}%`,
          top: `${(n.b[1] / H) * 100}%`,
          width: `${((n.b[2] - n.b[0]) / W) * 100}%`,
          height: `${((n.b[3] - n.b[1]) / H) * 100}%`,
        },
        tip:
          (n.sels.length
            ? n.sels.map((s) => `${s.by}=${s.v}${s.n > 1 ? `（${s.n}个匹配，idx ${s.idx}）` : ""}`).join("\n")
            : `自身 id/text/desc 全空\n靠父锚 ${n.anc!.by}=${n.anc!.v} --child ${n.anc!.child}`) +
          `\n${n.cls}${n.clk ? " · clickable" : ""}` +
          (extra ? `\n（同位置还叠着 ${extra} 个同尺寸容器节点，已折叠）` : ""),
      };
    });
});

// 对齐自检（确认对齐无误后可整块删掉）：把第一个框实际渲染的位置反算回设备坐标跟 bounds 比，
// 自己判定对不对 —— 免得靠肉眼看图猜"是不是还偏一点"。overlay 与 img 的尺寸也一起报出来。
const align = ref<{ ok: boolean; text: string } | null>(null);
watch([boxes, () => screen.value?.png, videoActive], async () => {
  await nextTick();
  const im = viewEl.value; // 视频模式量 canvas，静态模式量 img——自检逻辑相同

  const ov = stage.value?.querySelector(".overlay") as HTMLElement | null;
  const el0 = ov?.querySelector(".box") as HTMLElement | null;
  const f = boxes.value[0], b = base.value;
  if (!im || !ov || !el0 || !f || !b) return (align.value = null);
  const ri = im.getBoundingClientRect(), ro = ov.getBoundingClientRect(), r0 = el0.getBoundingClientRect();
  if (!ri.width || !ri.height) return (align.value = null);
  const dx = ((r0.left - ri.left) / ri.width) * b[0] - f.n.b[0];
  const dy = ((r0.top - ri.top) / ri.height) * b[1] - f.n.b[1];
  const ok = Math.abs(dx) < 2 && Math.abs(dy) < 2 && Math.abs(ro.height - ri.height) < 1;
  align.value = {
    ok,
    text: (ok ? "✓ 控件框已对齐" : `⚠︎ 控件框未对齐，偏差 (${dx.toFixed(1)}, ${dy.toFixed(1)}) 设备像素`) +
      ` · overlay ${ro.width.toFixed(1)}×${ro.height.toFixed(1)} / img ${ri.width.toFixed(1)}×${ri.height.toFixed(1)}` +
      ` · ${navigator.userAgent.includes("Chrome") ? "Chromium" : "WebKit"}`,
  };
});

// 统计按**实际画出来的框**算（已折叠同位置重复容器），跟屏幕上看到的对得上
const tally = computed(() => {
  const B = boxes.value;
  return {
    all: screen.value?.nodes.length || 0,
    drawn: B.length,
    sel: B.filter((b) => b.n.sels.length).length,
    amb: B.filter((b) => b.cls === "b-amb").length,
    anc: B.filter((b) => b.cls === "b-anc").length,
    dead: (screen.value?.nodes || []).filter((n) => !n.sels.length && !n.anc).length,
  };
});

const HINTS: Record<string, string> = {
  tap: "点屏幕上的框直接下发点击，记的是选择器不是坐标。红框=该选择器在全树不唯一，点了会先让你消歧。",
  longpress: "长按模式：点屏幕上的框原地按住再松手（不移动），用于长按弹出菜单/长按删除确认这类场景。跟点击一样记选择器不记坐标。",
  swipe: "滑动模式：在屏幕上按下拖到终点松开。起止点会锚到所在控件的选择器 + 百分比，导出脚本时现算坐标。",
  longdrag: "长拖模式：同滑动，但先长按再拖（波形起止手柄这类要用它）。",
  xy: "硬坐标模式：点任意位置下发 tap x y。只在控件既无选择器、也没有能唯一定位的祖先时用，该步会被标红提醒必须改。",
};

function onImgLoad() {
  const el = shot.value;
  if (el?.naturalWidth) nat.value = [el.naturalWidth, el.naturalHeight];
}

async function call<T>(what: string, fn: () => Promise<T>): Promise<T | null> {
  busy.value = what;
  err.value = "";
  msg.value = "";
  try {
    return await fn();
  } catch (e: any) {
    err.value = String(e);
    return null;
  } finally {
    busy.value = "";
  }
}

// 点「开始/重新探屏」先核对一下：手机当前前台是不是左栏选中的这个 App。查到是仓库里
// 另一个已注册的 App 就自动切目标目录（复用切 App 的既有 watcher 收尾旧会话/重置步骤），
// 别的情况（查不到、就是当前这个、或前台是个没注册的 App）一律不动——宁可不切也不能猜错。
async function maybeSwitchByForegroundApp() {
  try {
    const r = await api.recDetectApp(store.activeSlug, serial.value);
    if (r?.slug && r.slug !== store.activeSlug) {
      const from = store.activeSlug || "（未选）";
      await store.setActive(r.slug);
      showToast(`检测到手机前台是「${r.slug}」，已自动切换目标目录（原「${from}」）`);
    }
  } catch {
    /* 探前台失败不阻断录制，按原目录继续 */
  }
}

async function probe() {
  if (!serial.value) return;
  if (runStore.runningSerials().includes(serial.value)) {
    err.value = "该设备正在跑回归，暂不能录制——等回归跑完再来";
    return;
  }
  await maybeSwitchByForegroundApp();
  stopped.value = false; // 重新探屏 = 恢复录制
  // 首选 V2：起（或复用）常驻会话，探屏由 daemon 推送（hierarchy + shot 消息）
  if (wsMode.value || (await startSession())) {
    err.value = "";
    stale.value = true;
    sess.send({ t: "refresh", auto_sweep: autoSweep.value });
    return;
  }
  const s = await call("探当前屏…", () => api.recProbe(store.activeSlug, serial.value, autoSweep.value));
  if (s) {
    screen.value = s;
    if (s.auto_swept) showToast(`已自动清障 ${s.auto_swept} 次（广告全屏页）`);
  }
}

async function act(body: Record<string, unknown>) {
  if (!serial.value) return;
  if (stopped.value) { err.value = "录制已停止，如需继续请先点「重新探屏」"; return; }
  if (!caseId.value.trim()) caseId.value = defaultCase();
  // 用当前最大 n + 1，不能用 length + 1：中间删过步骤后两者不等，会撞上残留的旧 n
  // （撞号会让 shots/{n}.png 互相覆盖，且 v-for :key="s.n" 重复）
  const nextN = steps.value.reduce((m, s) => Math.max(m, s.n), 0) + 1;
  if (wsMode.value) {
    const { kind, ...rest } = body as { kind: string } & Record<string, unknown>;
    err.value = "";
    msg.value = "";
    busy.value = `执行 ${kind}…`; // step 消息到达即清（~0.2s），不上全屏 mask
    stale.value = true;
    sess.send({ t: "act", kind, body: rest, case: caseId.value.trim(), n: nextN,
                auto_sweep: autoSweep.value });
    return;
  }
  const r = await call(`执行 ${body.kind}…`, () =>
    api.recAct(store.activeSlug, serial.value, {
      ...body,
      case: caseId.value.trim(),
      n: nextN,
      before_labels: screen.value?.labels,
      auto_sweep: autoSweep.value,
    })
  );
  if (r) {
    steps.value.push(r.step);
    screen.value = r.screen;
    if (r.step.auto_swept) showToast(`已自动清障 ${r.step.auto_swept} 次（广告全屏页）`);
  }
}

// 悬浮面板：position:fixed 用视口坐标，不受 .stage 的 overflow:hidden 裁切。离开框后延迟
// 200ms 才清空，给鼠标留时间移进面板里去框选文字；移进面板本身要取消这个延迟，否则选不完就被收走
const hoverBox = ref<{ tip: string; x: number; y: number } | null>(null);
let hoverHideTimer: number | undefined;
function clearHoverHide() {
  if (hoverHideTimer !== undefined) { clearTimeout(hoverHideTimer); hoverHideTimer = undefined; }
}
function onBoxEnter(b: { tip: string }, e: MouseEvent) {
  clearHoverHide();
  hoverBox.value = {
    tip: b.tip,
    x: Math.min(e.clientX + 14, window.innerWidth - 320),
    y: Math.min(e.clientY + 14, window.innerHeight - 40),
  };
}
function onBoxLeave() {
  clearHoverHide();
  hoverHideTimer = window.setTimeout(() => { hoverBox.value = null; }, 200);
}

function pick(n: RecNode) {
  if (mode.value !== "tap" && mode.value !== "longpress") return;
  const kind = mode.value;
  if (!n.sels.length) return act({ kind, x: n.c[0], y: n.c[1], anc: n.anc });
  if (n.sels[0].n === 1) return act({ kind, sel: n.sels[0] });
  ambigPick.value = 0; // 有歧义：先让人挑一个能唯一定位的，或确认要用 --index
  ambigKind.value = kind;
  ambig.value = n;
}
function confirmAmbig() {
  const n = ambig.value!;
  const sel: RecSel = n.sels[ambigPick.value];
  ambig.value = null;
  act({ kind: ambigKind.value, sel });
}

function toDev(e: MouseEvent): [number, number] | null {
  const el = viewEl.value;
  if (!el || !base.value) return null; // 没有可信基准时不换算坐标，宁可不动作也别点错位置
  const r = el.getBoundingClientRect();
  const [W, H] = base.value;
  return [Math.round(((e.clientX - r.left) / r.width) * W), Math.round(((e.clientY - r.top) / r.height) * H)];
}

function down(e: MouseEvent) {
  if (mode.value === "tap" || mode.value === "longpress" || !viewEl.value) return;
  const p = toDev(e);
  if (!p) return;
  if (mode.value === "xy") return act({ kind: "tap", x: p[0], y: p[1] });
  const r = viewEl.value.getBoundingClientRect();
  const sx = e.clientX - r.left, sy = e.clientY - r.top;
  line.value = { x: sx, y: sy, len: 0, deg: 0 };
  const move = (ev: MouseEvent) => {
    const dx = ev.clientX - r.left - sx, dy = ev.clientY - r.top - sy;
    line.value = { x: sx, y: sy, len: Math.hypot(dx, dy), deg: (Math.atan2(dy, dx) * 180) / Math.PI };
  };
  const up = (ev: MouseEvent) => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    line.value = null;
    const q = toDev(ev);
    if (!q || Math.hypot(q[0] - p[0], q[1] - p[1]) < 20) return; // 太短当误触，不录
    act({ kind: mode.value, x1: p[0], y1: p[1], x2: q[0], y2: q[1] });
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
  e.preventDefault();
}

// window.prompt 在 Tauri 的 webview 里没实现——点了**静默**什么都不弹、直接返回 null（confirm/
// message 有 plugin-dialog 顶上，文本输入没有对应 API）。所以自己做页内输入弹层。
const ask = ref<{ title: string; hint: string; value: string } | null>(null);
const askInput = ref<HTMLInputElement | null>(null);
let askResolve: ((v: string | null) => void) | null = null;
function askText(title: string, hint: string, initial = ""): Promise<string | null> {
  ask.value = { title, hint, value: initial };
  nextTick(() => askInput.value?.focus());
  return new Promise((r) => { askResolve = r; });
}
function askDone(ok: boolean) {
  const v = ok ? (ask.value?.value ?? "").trim() : null;
  ask.value = null;
  askResolve?.(v || null);
  askResolve = null;
}

async function typeText() {
  const v = await askText("输入文本", "打进当前有焦点的输入框（先点一下目标输入框）。会用 --assert-typed 校验真打进去了，防输入法联想乱码。");
  if (v) act({ kind: "text", value: v });
}
async function noteStep(s: any) {
  const v = await askText(
    "给这一步加备注",
    "不操作设备，只挂在这一步上；导出脚本时会在这行代码前插入 # 备注：… 注释（如标记这是个检查点）。",
    s.note || "",
  );
  if (v) s.note = v;
}
function undo() {
  steps.value.pop();
}
function deleteStep(n: number) {
  steps.value = steps.value.filter((s) => s.n !== n);
}
async function clearSteps() {
  const ok = await confirm(`已录 ${steps.value.length} 步，清空后不可恢复。`, {
    title: "确认清空全部步骤？",
    kind: "warning",
  });
  if (!ok) return;
  steps.value = [];
}
async function doExport() {
  const c = caseId.value.trim();
  if (!c || !steps.value.length) {
    err.value = "还没录到步骤，或用例 ID 为空";
    return;
  }
  // 有 diff 还没回来的步骤（刚点完就导出）先等一拍——rec.json 里 pending diff 是残次品
  if (wsMode.value) {
    if (steps.value.some((s: any) => s.diff?.pending)) {
      err.value = "还有步骤的 diff 在计算中（刚执行完动作），等控件框刷新后再导出";
      return;
    }
    err.value = "";
    busy.value = "落盘…";
    sess.send({ t: "export", case: c, steps: steps.value });
    return;
  }
  const r = await call("落盘…", () => api.recExport(store.activeSlug, serial.value, c, steps.value));
  if (r) msg.value = `已导出 ${r.steps} 步 / ${r.shots} 张截图 → ${r.rec} · 脚本草稿 ${r.flow}（草稿没有任何判定，要补 output-check/logscan/FAILED 收尾）`;
}

const warnCount = computed(() => steps.value.filter((s) => s.warn || s.needs_attention).length);

watch(() => store.activeSlug, () => { stopSession(); loadDevices(); screen.value = null; steps.value = []; });
// 切设备 = 换会话：旧 daemon 停掉（每设备一进程，留着白占一条 u2 连接），新会话点「开始」再起
watch(serial, (_n, old) => {
  if (sess.connected) sess.close();
  if (old && connState.value === "ws") api.recSessionStop(old).catch(() => {});
  wsMode.value = false;
  connState.value = "";
  stale.value = false;
  lastShotSeq = 0;
});

// keep-alive 保活本视图（录到一半切去看设备/证据，回来步骤还在）。控件框的对齐靠 CSS
// （.overlay 用 inset:0 贴合被 img 撑开的 .frame）保证，切走切回都不用重量，只剩"切回刷设备列表"。
let firstActivate = true;
onActivated(() => {
  if (firstActivate) firstActivate = false; // 首次挂载后紧跟一次 activated，别重复拉设备
  else loadDevices();
});

onMounted(async () => {
  caseId.value = defaultCase();
  await loadDevices();
});

</script>

<template>
  <div>
    <div class="hd">
      <h2>录制器</h2>
      <select v-model="serial" @change="screen = null">
        <option value="" disabled>选设备</option>
        <option v-for="d in devices" :key="d.serial" :value="d.serial"
                :disabled="d.state !== 'device' || busySerials.has(d.serial)">
          {{ devLabel(d) }}{{ busySerials.has(d.serial) ? "（回归执行中）" : "" }}
        </option>
      </select>
      <input v-model="caseId" class="mono case" placeholder="用例 ID" />
      <button @click="probe" :disabled="!serial || !!busy || busySerials.has(serial)"
              :title="busySerials.has(serial) ? '该设备正在跑回归，暂不能录制' : ''">
        {{ screen ? "重新探屏" : "开始（探当前屏）" }}
      </button>
      <label class="small auto-sweep" title="遇到已知广告 SDK 全屏页自动清掉，不用人眼看到广告再手动点「清障」">
        <input type="checkbox" v-model="autoSweep" /> 自动清障
      </label>
      <span class="muted small">产物落 <span class="mono">apps/{{ store.activeSlug }}/recordings/&lt;用例ID&gt;/</span></span>
      <span class="sp"></span>
      <button class="mini" @click="stopRecording" :disabled="!screen || stopped" title="断开录制会话/停视频，画面定格；已录步骤保留，仍可导出">
        {{ stopped ? "已停止" : "停止录制" }}
      </button>
    </div>

    <transition name="toast">
      <div v-if="toast" class="toast mono">{{ toast }}</div>
    </transition>

    <div v-if="err" class="err">{{ err }}</div>

    <div v-if="!screen" class="card empty muted">
      选一台在线设备 → 点「开始」。录制器会截当前屏并叠出可点的控件框：<b>黄框</b>=有唯一选择器、<b class="c-amb">红框</b>=选择器有歧义（点前强制消歧）、<b class="c-anc">蓝框</b>=自身
      <span class="mono">id/text/desc</span> 全空、靠父锚 <span class="mono">--child</span> 定位。
      每点一步会自动再探一屏并 diff，新出现的文案就是这步的可观察后果。
    </div>

    <div v-else class="wrap">
      <div class="left">
        <div class="tools">
          <button class="mini" @click="act({ kind: 'launch' })">启动应用</button>
          <button class="mini" @click="act({ kind: 'sweep' })">清障</button>
          <button class="mini" @click="act({ kind: 'key', code: 'KEYCODE_BACK' })">返回</button>
          <button class="mini" @click="act({ kind: 'key', code: 'KEYCODE_HOME' })">主页</button>
          <button class="mini" @click="act({ kind: 'key', code: 'KEYCODE_DEL' })">退格</button>
          <button class="mini" @click="typeText">输入文本</button>
          <button class="mini" :class="{ on: mode === 'swipe' }" @click="mode = mode === 'swipe' ? 'tap' : 'swipe'">滑动</button>
          <button class="mini" :class="{ on: mode === 'longpress' }" @click="mode = mode === 'longpress' ? 'tap' : 'longpress'">长按</button>
          <button class="mini" :class="{ on: mode === 'longdrag' }" @click="mode = mode === 'longdrag' ? 'tap' : 'longdrag'">长拖</button>
          <button class="mini" :class="{ on: mode === 'xy' }" @click="mode = mode === 'xy' ? 'tap' : 'xy'">硬坐标</button>
        </div>
        <div v-if="screen.png_err" class="err small">
          截图失败（控件框仍可点，只是看不到画面）：{{ screen.png_err }}
        </div>
        <div ref="stage" class="stage" :class="{ grab: mode !== 'tap' && mode !== 'longpress' }" @mousedown="down"
             :style="screen.png ? {} : { width: '260px', height: '520px' }">
          <!-- .frame 的尺寸完全由 img 撑开（img 是它唯一的 in-flow 子元素），.overlay 用 inset:0
               铺满 .frame ⇒ overlay 与 img 严格同尺寸同位置，框用百分比定位就必然对齐。
               这么绕一层是因为前两种做法都在 WKWebView 上翻过车：JS 测 img 矩形再算像素会量到
               图片解码前的旧尺寸；aspect-ratio 与 img 的 height:auto 算出的高度也未必逐像素一致。
               inset:0 不依赖任何数值计算，跨引擎都成立。 -->
          <!-- deviceWH 也算渲染条件：视频模式下 daemon 不发截图（png 恒空），而 videoActive 要等
               第一帧解出来、第一帧又要 canvas 先在 DOM 里——只写 `png || videoActive` 时这三者互为
               前提，一旦首屏正好赶上 scrcpy 已在流（png 就此不再来），canvas 永远挂不上、视频永远
               解不出，表现成"探屏成功但一片黑、0 个可点框"（黑框只是 .stage 的占位尺寸）。
               daemon 一报 videoMeta（deviceWH 到手）就把 canvas 挂上，死锁解开。 -->
          <div v-if="screen.png || videoActive || deviceWH" class="frame">
            <!-- 视频模式（scrcpy 流）canvas 与静态截图 img 二选一显示；canvas 常驻 DOM
                 （v-show）是因为 VideoPipe 要先绑上它才解得出第一帧，第一帧到了才切显示 -->
            <canvas ref="videoCanvas" v-show="videoActive" class="vshot" />
            <img v-show="!videoActive" ref="shot" :src="'data:image/png;base64,' + screen.png"
                 @load="onImgLoad" alt="" :class="{ 'img-stale': stale }" />
            <!-- stale：动作已下发、新树未到；motionStale：视频画面正在变（有帧在到达）、
                 树还是旧的。两种情况下旧框都不可信——压暗+禁点，防照旧框点错。 -->
            <div class="overlay" :class="{ 'ov-stale': stale || motionStale }">
              <div
                v-for="(b, i) in boxes"
                :key="i"
                class="box"
                :class="b.cls"
                :style="b.style"
                @mouseenter="onBoxEnter(b, $event)"
                @mouseleave="onBoxLeave"
                @click.stop="pick(b.n)"
              />
            </div>
            <div v-if="stale || motionStale" class="stale-tip">控件框刷新中…</div>
          </div>
          <div
            v-if="line"
            class="line"
            :style="{ left: line.x + 'px', top: line.y + 'px', width: line.len + 'px', transform: `rotate(${line.deg}deg)` }"
          />
          <div v-if="busy" class="mask">{{ busy }}</div>
        </div>
      </div>
      <!-- 原生 title 提示鼠标选不中文字、复制不了（OS 渲染，跟 DOM 无关）——换成真实 DOM 悬浮层，
           鼠标移进面板本身也不会消失，可以正常框选/Ctrl+C 复制里面的选择器信息 -->
      <div
        v-if="hoverBox"
        class="hover-tip mono"
        :style="{ left: hoverBox.x + 'px', top: hoverBox.y + 'px' }"
        @mouseenter="clearHoverHide"
        @mouseleave="hoverBox = null"
      >{{ hoverBox.tip }}</div>

      <div class="right">
        <div class="card hint">
          {{ HINTS[mode] }}
          <div class="muted small mt">
            本屏 {{ tally.all }} 个节点，画出 {{ tally.drawn }} 个可点框（同位置重复的容器已折叠）：<b>{{ tally.sel }}</b>
            个用选择器定位<template v-if="tally.amb">，其中 <b class="c-amb">{{ tally.amb }}</b> 个首选不唯一</template><template
              v-if="tally.anc"
            >；<b class="c-anc">{{ tally.anc }}</b> 个靠父锚定位</template><template v-if="tally.dead">；{{ tally.dead }}
            个彻底够不着（不画框，只能走硬坐标）</template>
          </div>
          <div class="muted small mt">
            <template v-if="connState === 'ws'"><b class="c-ok">实时会话</b>（常驻服务，步骤 ~0.2s / 新框 ~1s）· </template>
            <template v-else-if="connState === 'legacy'"><b class="warn">降级模式</b>（每步 ~5s，见上方提示）· </template>
            dump 后端
            <b :class="screen.backend === 'u2' ? 'c-ok' : ''">{{ screen.backend || "?" }}</b>
            <template v-if="screen.backend === 'u2'">（常驻 atx，比 shell 快约 7×）</template>
            <template v-else>
              —— 这台设备没初始化过 atx，探屏慢约 7×。想提速：<span class="mono">python3 tools/init_target.py &lt;包名&gt; --atx-init</span>
            </template>
          </div>
          <div v-if="align" class="mono align" :class="align.ok ? 'a-ok' : 'a-bad'">{{ align.text }}</div>
        </div>

        <div v-if="msg" class="ok">{{ msg }}</div>

        <div class="hd2">
          <b>步骤 {{ steps.length }}</b>
          <span v-if="warnCount" class="pill pill-warning">{{ warnCount }} 步需人工处理</span>
          <span class="sp"></span>
          <button class="mini" @click="clearSteps" :disabled="!steps.length">清空步骤</button>
          <button class="mini" @click="undo" :disabled="!steps.length">撤销末步</button>
          <button class="mini" @click="doExport" :disabled="!steps.length || !!busy">导出</button>
        </div>

        <div v-if="!steps.length" class="card empty muted small">
          还没有步骤。先「启动应用」，再点屏幕上的框。
        </div>
        <div v-for="s in steps" :key="s.n" class="card step">
          <div class="sn">#{{ s.n }}</div>
          <button v-if="!s.note" class="mini snote" title="给这一步加备注" @click="noteStep(s)">备注</button>
          <button class="mini sdel" title="删除这一步" @click="deleteStep(s.n)">✕</button>
          <div class="sbody">
            <div class="slabel">{{ s.label }}</div>
            <div v-if="s.note" class="note-line" title="点击改备注" @click="noteStep(s)">📝 {{ s.note }}</div>
            <div v-if="s.script?.length" class="mono cmd">
              <div v-for="(ln, i) in s.script" :key="i">{{ ln }}</div>
            </div>
            <div v-if="s.cmd && s.script?.length > 1" class="muted small">
              录制当下执行的是 <span class="mono">{{ s.cmd.join(" ") }}</span>（实时坐标）；上面那几行才是脚本里的样子
            </div>
            <div v-if="s.warn" class="warn small">⚠︎ {{ s.warn }}</div>
            <div v-if="s.needs_attention" class="warn small">⚠︎ {{ s.needs_attention }}</div>
            <div v-if="s.child_anchor" class="muted small">
              父锚 {{ s.child_anchor.by }}={{ s.child_anchor.v }} --child {{ s.child_anchor.child }}（导出脚本用 bounds 现算坐标）
            </div>
            <div v-if="s.straightened" class="muted small">⤳ {{ s.straightened }}</div>
            <div v-if="s.anchor" class="muted small">
              锚 {{ s.anchor.sel.by }}={{ s.anchor.sel.v }} @ {{ s.anchor.rx }}‰,{{ s.anchor.ry }}‰<template
                v-if="s.anchor_to"
              > → {{ s.anchor_to.rx }}‰,{{ s.anchor_to.ry }}‰</template>
            </div>
            <div v-if="s.diff.appeared.length" class="mt">
              <span class="muted small">新出现 </span>
              <span v-for="t in s.diff.appeared.slice(0, 6)" :key="t" class="tag">{{ t }}</span>
              <span v-if="s.diff.appeared.length > 6" class="muted small">+{{ s.diff.appeared.length - 6 }}</span>
            </div>
            <div v-else class="muted small mt">界面文案无变化</div>
            <div v-if="s.diff.disappeared.length" class="muted small">
              消失 {{ s.diff.disappeared.slice(0, 4).join("、") }}<template v-if="s.diff.disappeared.length > 4">
                +{{ s.diff.disappeared.length - 4 }}</template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="ask" class="modal" @click.self="askDone(false)">
      <div class="card dlg">
        <b>{{ ask.title }}</b>
        <p class="muted small">{{ ask.hint }}</p>
        <input ref="askInput" v-model="ask.value" class="mono ask-in"
               @keydown.enter.prevent="askDone(true)" @keydown.esc.prevent="askDone(false)" />
        <div class="dlg-actions mt">
          <button class="mini" @click="askDone(false)">取消</button>
          <button class="mini on" @click="askDone(true)">确定</button>
        </div>
      </div>
    </div>

    <div v-if="ambig" class="modal" @click.self="ambig = null">
      <div class="card dlg">
        <b>这个控件的首选选择器不唯一</b>
        <p class="muted small">
          全树有多个节点匹配同一个选择器（对话框标题和确认按钮同文案就是这种情况，真踩过）。选一个能唯一定位的，
          或确认用 <span class="mono">--index</span> 按序号定位——后者 UI 一改就会点错行。
        </p>
        <label v-for="(s, i) in ambig.sels" :key="i" class="opt">
          <input type="radio" :value="i" v-model="ambigPick" />
          <span class="mono">{{ s.by }}={{ s.v }}</span>
          <span v-if="s.n === 1" class="c-ok small">唯一命中 ✓</span>
          <span v-else class="warn small">{{ s.n }} 个匹配 → 用 --index {{ s.idx }} 点第 {{ s.idx + 1 }} 个</span>
        </label>
        <div class="dlg-actions mt">
          <button class="mini" @click="ambig = null">取消</button>
          <button class="mini on" @click="confirmAmbig">用选中的点</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hd { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-weight: 500; }
.case { width: 150px; }
.small { font-size: 12px; }
.mt { margin-top: 5px; }
.sp { flex: 1; }
.err { color: var(--text-danger); background: var(--bg-danger); padding: 10px 12px; border-radius: var(--radius); margin: 10px 0; }
/* 浮层，脱离文档流：清障提示出现/消失都不推动下面的布局 */
.toast {
  position: fixed; top: 56px; left: 50%; transform: translateX(-50%); z-index: 60;
  color: var(--text-success); background: var(--bg-success); border: 1px solid var(--border);
  padding: 7px 14px; border-radius: 999px; font-size: 12px; pointer-events: none;
  box-shadow: 0 6px 20px rgba(0,0,0,.16); max-width: 80vw;
}
.toast-enter-active, .toast-leave-active { transition: opacity .18s ease, transform .18s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(-6px); }
.ok { color: var(--text-success); background: var(--bg-success); padding: 10px 12px; border-radius: var(--radius); margin: 10px 0; font-size: 13px; }
.empty { padding: 20px; margin-top: 12px; line-height: 1.7; }
.c-amb { color: var(--text-danger); }
.c-anc { color: #2563eb; }
.c-ok { color: var(--text-success); }
.warn { color: var(--text-danger); }

.wrap { display: flex; gap: 16px; align-items: flex-start; margin-top: 12px; }
.left { flex: none; width: 400px; text-align: center; }
.tools { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
.stage { position: relative; background: #000; border-radius: var(--radius); overflow: hidden; line-height: 0;
         max-width: 400px; display: inline-block; }
.stage.grab { cursor: crosshair; }
/* .frame 改 inline-block（原先 block）：block 只在"高度撑不下要靠宽度让路"时才会跟 img
   同宽同高，遇到"宽度反而是瓶颈"的场景（下面 img 的 max-height 富余、max-width 先顶到头，
   比如窗口拉得很高很窄不常见但确实可能）block 会比 img 更宽，超出的黑边会让 .overlay 的
   inset:0 百分比错位。inline-block 让 .frame 在两种场景下都精确收缩到 img 的实际渲染框，
   不引入任何数值计算（对齐自检 118-135 行两种场景都测过，无偏差）。 */
.frame { position: relative; display: inline-block; line-height: 0; max-width: 100%; }
/* img 的 max-height 用 100vh 减一个经验值（.content 上下 padding 40 + .hd 44 + margin-top 12
   + .tools 两行 68，實测约 160，留 20px 余量），而不是 aspect-ratio：aspect-ratio 算出来的
   高度和 img 自身 intrinsic ratio 算出来的高度未必逐像素一致（跨引擎踩过），这里 img 只用
   浏览器对"替换元素+max-width+max-height+width/height:auto"的原生等比缩放算法，本身保真。
   之前这里没有高度上限，1080x2280 的截图在 400px 宽下要撑到 843px 高，笔记本视口经常放不下，
   只能整页滚动。 */
.stage img { display: block; max-width: 100%; max-height: calc(100vh - 180px); width: auto; height: auto; }
/* 视频 canvas 与 img 同一套缩放规则（canvas 的 width/height 属性= 设备像素 = intrinsic 尺寸，
   现代引擎会按属性比例等比缩放，与 img 行为一致），.frame/.overlay 的对齐机制原样成立 */
.stage canvas.vshot { display: block; max-width: 100%; max-height: calc(100vh - 180px); width: auto; height: auto; }
/* 用 outline 而不是 border 画框：outline 不进盒模型、走的渲染路径也不同，能绕开 WKWebView 下
   「半透明 dashed border 的盒子被填上底色 + 冒出圆角」那个怪象（Chromium 里同样代码 computed
   background 是全透明的，只有桌面壳复现）。边框色一律用不透明值，少一个变量。 */
/* inset:0 铺满 .frame，而 .frame 的尺寸由 img 撑开 ⇒ 与 img 严格重合，不含任何数值计算。
   pointer-events:none 让滑动/长拖的 mousedown 能穿透到 .stage，只有框本身接点击。 */
.overlay { position: absolute; inset: 0; pointer-events: none; }
/* margin 必须显式归零：绝对定位元素有显式 top 时 margin-top 仍会叠加位移，任何撞车的
   全局/同组件类带上 margin 都会让框整体平移（.ok 那次就是 +10px，只偏 y 不偏 x）。 */
.box { position: absolute; background: none; cursor: pointer; pointer-events: auto; margin: 0;
       outline: 1px dashed #ffbe00; border-radius: 0; }
.box:hover { background: rgba(37, 99, 235, 0.28); outline: 1px solid #fff; }
.box.b-amb { outline-color: #ff5050; }
.box.b-anc { outline-color: #5aaaff; }
.hover-tip { position: fixed; z-index: 70; max-width: 320px; padding: 8px 10px; font-size: 12px;
  line-height: 1.5; white-space: pre-line; background: #f0f0f0; color: #111; border: 1px solid #ccc;
  border-radius: 6px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25); user-select: text; cursor: text; }
.line { position: absolute; height: 2px; background: #0f0; transform-origin: 0 50%; pointer-events: none; }
/* stale（V2 异步刷新的空窗态）：框压淡 + 禁点 + 角标提示。不用全屏 mask——空窗只有 ~1s，
   遮全屏反而闪。视频模式画面本身是实时的（真相），只弱化"已经过时的框"。 */
.img-stale { filter: brightness(0.55); }
.ov-stale { opacity: 0.22; }
.ov-stale .box { pointer-events: none; }
.stale-tip { position: absolute; top: 8px; left: 50%; transform: translateX(-50%); font-size: 12px;
             color: #fff; background: rgba(0, 0, 0, 0.6); padding: 2px 10px; border-radius: 999px;
             line-height: 1.6; pointer-events: none; }
.mask { position: absolute; inset: 0; background: rgba(255, 255, 255, 0.72); color: #111;
        display: flex; align-items: center; justify-content: center; font-size: 13px; line-height: 1.5; }

.right { flex: 1; min-width: 0; }
.hint { padding: 10px 12px; line-height: 1.6; font-size: 13px; }
/* 对齐自检行（确认后可删） */
.align { font-size: 11px; margin-top: 6px; word-break: break-all; }
.a-ok { color: var(--text-success); }
.a-bad { color: var(--text-danger); font-weight: 500; }
.hd2 { display: flex; align-items: center; gap: 8px; margin: 12px 0 8px; }
.step { position: relative; display: grid; grid-template-columns: 30px 1fr; gap: 8px; padding: 10px 12px; margin-bottom: 6px; }
.sn { color: var(--text-secondary); font-size: 12px; }
.sdel { position: absolute; top: 8px; right: 10px; padding: 0 6px; line-height: 20px; height: 20px;
        color: var(--text-secondary); }
.sdel:hover { color: var(--text-danger); }
.snote { position: absolute; top: 8px; right: 38px; padding: 0 6px; line-height: 20px; height: 20px;
         color: var(--text-secondary); font-size: 11px; }
.snote:hover { color: var(--text-accent, #2563eb); }
.sbody { min-width: 0; }
.slabel { font-weight: 500; font-size: 13px; }
.note-line { font-size: 12px; margin: 4px 0; padding: 2px 8px; border-radius: 4px; cursor: pointer;
             background: rgba(217, 119, 6, .12); color: #92400e; display: inline-block; word-break: break-all; }
.note-line:hover { background: rgba(217, 119, 6, .2); }
.cmd { font-size: 12px; background: var(--bg-code, rgba(127,127,127,.12)); padding: 2px 6px;
       border-radius: 4px; margin: 4px 0; display: inline-block; word-break: break-all; }
.tag { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 999px;
       background: rgba(37, 99, 235, 0.12); color: #3730a3; margin: 0 4px 3px 0; }

.modal { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); z-index: 90;
         display: flex; align-items: center; justify-content: center; }
.dlg { width: 460px; max-width: 92vw; padding: 16px 18px; }
.opt { display: flex; align-items: center; gap: 8px; padding: 7px 9px; border: 0.5px solid var(--border);
       border-radius: 8px; margin: 5px 0; cursor: pointer; font-size: 13px; }
.dlg-actions { text-align: right; }
.ask-in { width: 100%; box-sizing: border-box; margin-top: 4px; }
.mini.on { background: var(--text-primary, #111); color: var(--bg, #fff); }
</style>
