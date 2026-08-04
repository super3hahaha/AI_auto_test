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
import { api, type DeviceRow, type RecScreen, type RecNode, type RecStep, type RecSel } from "../api";
import { store } from "../store";

const devices = ref<DeviceRow[]>([]);
const serial = ref("");
const caseId = ref("");
const screen = ref<RecScreen | null>(null);
const steps = ref<RecStep[]>([]);
const mode = ref<"tap" | "swipe" | "longdrag" | "xy">("tap");
const busy = ref("");
const err = ref("");
const msg = ref("");
const ambig = ref<RecNode | null>(null);
const ambigPick = ref(0);
const shot = ref<HTMLImageElement | null>(null);
const stage = ref<HTMLElement | null>(null);
const nat = ref<[number, number] | null>(null); // 截图真实像素，框定位的基准
const line = ref<{ x: number; y: number; len: number; deg: number } | null>(null);

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
  const sc = screen.value;
  if (sc?.shot_w && sc.shot_h) return [sc.shot_w, sc.shot_h]; // 后端从 PNG IHDR 读的权威值
  return nat.value; // 兜底：img 已加载时的真实像素。两者都没有就不画框，宁可不画也别画错
});

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
watch([boxes, () => screen.value?.png], async () => {
  await nextTick();
  const im = shot.value;
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

async function probe() {
  if (!serial.value) return;
  const s = await call("探当前屏…", () => api.recProbe(store.activeSlug, serial.value));
  if (s) screen.value = s;
}

async function act(body: Record<string, unknown>) {
  if (!serial.value) return;
  if (!caseId.value.trim()) caseId.value = defaultCase();
  const r = await call(`执行 ${body.kind}…`, () =>
    api.recAct(store.activeSlug, serial.value, {
      ...body,
      case: caseId.value.trim(),
      n: steps.value.length + 1,
      before_labels: screen.value?.labels,
    })
  );
  if (r) {
    steps.value.push(r.step);
    screen.value = r.screen;
  }
}

function pick(n: RecNode) {
  if (mode.value !== "tap") return;
  if (!n.sels.length) return act({ kind: "tap", x: n.c[0], y: n.c[1], anc: n.anc });
  if (n.sels[0].n === 1) return act({ kind: "tap", sel: n.sels[0] });
  ambigPick.value = 0; // 有歧义：先让人挑一个能唯一定位的，或确认要用 --index
  ambig.value = n;
}
function confirmAmbig() {
  const n = ambig.value!;
  const sel: RecSel = n.sels[ambigPick.value];
  ambig.value = null;
  act({ kind: "tap", sel });
}

function toDev(e: MouseEvent): [number, number] | null {
  const el = shot.value;
  if (!el || !base.value) return null; // 没有可信基准时不换算坐标，宁可不动作也别点错位置
  const r = el.getBoundingClientRect();
  const [W, H] = base.value;
  return [Math.round(((e.clientX - r.left) / r.width) * W), Math.round(((e.clientY - r.top) / r.height) * H)];
}

function down(e: MouseEvent) {
  if (mode.value === "tap" || !shot.value) return;
  const p = toDev(e);
  if (!p) return;
  if (mode.value === "xy") return act({ kind: "tap", x: p[0], y: p[1] });
  const r = shot.value.getBoundingClientRect();
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

function typeText() {
  const v = prompt("输入文本（会用 --assert-typed 校验真打进去了，防输入法联想乱码）");
  if (v) act({ kind: "text", value: v });
}
function note() {
  const v = prompt("给这一步记一句备注（不操作设备，只写进录制文件）");
  if (v) act({ kind: "note", value: v });
}
function undo() {
  steps.value.pop();
}
async function doExport() {
  const c = caseId.value.trim();
  if (!c || !steps.value.length) {
    err.value = "还没录到步骤，或用例 ID 为空";
    return;
  }
  const r = await call("落盘…", () => api.recExport(store.activeSlug, serial.value, c, steps.value));
  if (r) msg.value = `已导出 ${r.steps} 步 / ${r.shots} 张截图 → ${r.rec} · 脚本草稿 ${r.flow}（草稿没有任何判定，要补 output-check/logscan/FAILED 收尾）`;
}

const warnCount = computed(() => steps.value.filter((s) => s.warn || s.needs_attention).length);

watch(() => store.activeSlug, () => { loadDevices(); screen.value = null; steps.value = []; });

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
        <option v-for="d in devices" :key="d.serial" :value="d.serial" :disabled="d.state !== 'device'">
          {{ devLabel(d) }}
        </option>
      </select>
      <input v-model="caseId" class="mono case" placeholder="用例 ID" />
      <button @click="probe" :disabled="!serial || !!busy">{{ screen ? "重新探屏" : "开始（探当前屏）" }}</button>
      <span class="muted small">产物落 <span class="mono">apps/{{ store.activeSlug }}/recordings/&lt;用例ID&gt;/</span></span>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="msg" class="ok">{{ msg }}</div>

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
          <button class="mini" :class="{ on: mode === 'longdrag' }" @click="mode = mode === 'longdrag' ? 'tap' : 'longdrag'">长拖</button>
          <button class="mini" :class="{ on: mode === 'xy' }" @click="mode = mode === 'xy' ? 'tap' : 'xy'">硬坐标</button>
          <button class="mini" @click="note">备注</button>
        </div>
        <div v-if="screen.png_err" class="err small">
          截图失败（控件框仍可点，只是看不到画面）：{{ screen.png_err }}
        </div>
        <div ref="stage" class="stage" :class="{ grab: mode !== 'tap' }" @mousedown="down"
             :style="screen.png ? {} : { width: '260px', height: '520px' }">
          <!-- .frame 的尺寸完全由 img 撑开（img 是它唯一的 in-flow 子元素），.overlay 用 inset:0
               铺满 .frame ⇒ overlay 与 img 严格同尺寸同位置，框用百分比定位就必然对齐。
               这么绕一层是因为前两种做法都在 WKWebView 上翻过车：JS 测 img 矩形再算像素会量到
               图片解码前的旧尺寸；aspect-ratio 与 img 的 height:auto 算出的高度也未必逐像素一致。
               inset:0 不依赖任何数值计算，跨引擎都成立。 -->
          <div v-if="screen.png" class="frame">
            <img ref="shot" :src="'data:image/png;base64,' + screen.png" @load="onImgLoad" alt="" />
            <div class="overlay">
              <div
                v-for="(b, i) in boxes"
                :key="i"
                class="box"
                :class="b.cls"
                :style="b.style"
                :title="b.tip"
                @click.stop="pick(b.n)"
              />
            </div>
          </div>
          <div
            v-if="line"
            class="line"
            :style="{ left: line.x + 'px', top: line.y + 'px', width: line.len + 'px', transform: `rotate(${line.deg}deg)` }"
          />
          <div v-if="busy" class="mask">{{ busy }}</div>
        </div>
      </div>

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
            dump 后端
            <b :class="screen.backend === 'u2' ? 'c-ok' : ''">{{ screen.backend || "?" }}</b>
            <template v-if="screen.backend === 'u2'">（常驻 atx，比 shell 快约 7×）</template>
            <template v-else>
              —— 这台设备没初始化过 atx，探屏慢约 7×。想提速：<span class="mono">python3 tools/init_target.py &lt;包名&gt; --atx-init</span>
            </template>
          </div>
          <div v-if="align" class="mono align" :class="align.ok ? 'a-ok' : 'a-bad'">{{ align.text }}</div>
        </div>

        <div class="hd2">
          <b>步骤 {{ steps.length }}</b>
          <span v-if="warnCount" class="pill pill-warning">{{ warnCount }} 步需人工处理</span>
          <span class="sp"></span>
          <button class="mini" @click="undo" :disabled="!steps.length">撤销末步</button>
          <button class="mini" @click="doExport" :disabled="!steps.length || !!busy">导出</button>
        </div>

        <div v-if="!steps.length" class="card empty muted small">
          还没有步骤。先「启动应用」，再点屏幕上的框。
        </div>
        <div v-for="s in steps" :key="s.n" class="card step">
          <div class="sn">#{{ s.n }}</div>
          <div class="sbody">
            <div class="slabel">{{ s.label }}</div>
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
.line { position: absolute; height: 2px; background: #0f0; transform-origin: 0 50%; pointer-events: none; }
.mask { position: absolute; inset: 0; background: rgba(255, 255, 255, 0.72); color: #111;
        display: flex; align-items: center; justify-content: center; font-size: 13px; line-height: 1.5; }

.right { flex: 1; min-width: 0; }
.hint { padding: 10px 12px; line-height: 1.6; font-size: 13px; }
/* 对齐自检行（确认后可删） */
.align { font-size: 11px; margin-top: 6px; word-break: break-all; }
.a-ok { color: var(--text-success); }
.a-bad { color: var(--text-danger); font-weight: 500; }
.hd2 { display: flex; align-items: center; gap: 8px; margin: 12px 0 8px; }
.step { display: grid; grid-template-columns: 30px 1fr; gap: 8px; padding: 10px 12px; margin-bottom: 6px; }
.sn { color: var(--text-secondary); font-size: 12px; }
.sbody { min-width: 0; }
.slabel { font-weight: 500; font-size: 13px; }
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
.mini.on { background: var(--text-primary, #111); color: var(--bg, #fff); }
</style>
