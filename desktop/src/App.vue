<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { store } from "./store";
import Setup from "./views/Setup.vue";
import Overview from "./views/Overview.vue";
import Devices from "./views/Devices.vue";
import Recorder from "./views/Recorder.vue";
import Runner from "./views/Runner.vue";
import Resources from "./views/Resources.vue";
import Evidence from "./views/Evidence.vue";
import Boards from "./views/Boards.vue";
import Cleanup from "./views/Cleanup.vue";

type View = "overview" | "devices" | "recorder" | "runner" | "resources" | "evidence" | "boards" | "cleanup" | "setup";
const active = ref<View>("runner");
const ready = ref(false);

const nav: { key: View; label: string; primary?: boolean }[] = [
  { key: "overview", label: "概览" },
  { key: "devices", label: "设备" },
  { key: "resources", label: "资源库" },
  { key: "recorder", label: "录制器" },
  { key: "runner", label: "执行台", primary: true },
  { key: "evidence", label: "证据" },
  { key: "boards", label: "看板" },
];

// 执行台/执行记录的用例卡片点「↗」→ 切到证据 tab（定位到哪一格由 Evidence 挂载后消费
// store.evidenceJump 完成）。Evidence 不在 keep-alive 名单里，每次都是新挂载，故只需切视图。
watch(
  () => store.evidenceJump,
  (v) => { if (v) active.value = "evidence"; }
);

onMounted(async () => {
  await store.loadConfig();
  if (!store.cfg?.configured) {
    active.value = "setup";
  } else {
    await store.loadApps();
    await store.loadRuns();
  }
  ready.value = true;
});

async function onConfigured() {
  await store.loadApps();
  await store.loadRuns();
  active.value = "runner";
}


</script>

<template>
  <div class="app" v-if="ready">
    <aside class="nav">
      <div class="brand">AI自动化<br /><span class="muted">测试台</span></div>
      <nav>
        <button
          v-for="n in nav"
          :key="n.key"
          class="navitem"
          :class="{ on: active === n.key, primary: n.primary }"
          :disabled="!store.cfg?.configured"
          @click="active = n.key"
        >
          <span v-if="n.primary" class="navitem-mark">▶</span>{{ n.label }}
        </button>
      </nav>
      <div class="nav-foot">
        <button
          class="navitem"
          :class="{ on: active === 'cleanup' }"
          :disabled="!store.cfg?.configured"
          @click="active = 'cleanup'"
        >
          清理
        </button>
        <button class="navitem" :class="{ on: active === 'setup' }" @click="active = 'setup'">
          设置
        </button>
      </div>
    </aside>

    <main class="content">
      <Setup v-if="active === 'setup'" @configured="onConfigured" />
      <!-- 保活 Runner + Recorder：
           · Runner —— 跑固化脚本时切走 tab 不销毁它，执行状态/流式日志得以延续；
           · Recorder —— 录制进度（步骤列表 + 当前屏）只在内存里，销毁就等于白录一遍。
           其余视图仍按原样每次进入重新挂载（切回自动刷新数据）。
           保活的视图不能只靠 onMounted 初始化：切回来走的是 onActivated（见两个视图内的用法）。 -->
      <keep-alive v-else :include="['Runner', 'Recorder']">
        <Overview v-if="active === 'overview'" />
        <Devices v-else-if="active === 'devices'" />
        <Recorder v-else-if="active === 'recorder'" />
        <Runner v-else-if="active === 'runner'" />
        <Resources v-else-if="active === 'resources'" />
        <Evidence v-else-if="active === 'evidence'" />
        <Boards v-else-if="active === 'boards'" @view-evidence="active = 'evidence'" />
        <Cleanup v-else-if="active === 'cleanup'" />
      </keep-alive>
    </main>
  </div>
</template>

<style scoped>
.app {
  display: flex;
  height: 100vh;
}
.nav {
  width: 108px;
  flex-shrink: 0;
  background: var(--surface-1);
  border-right: 0.5px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 14px 10px;
}
.brand {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.3;
  padding: 4px 8px 16px;
}
.brand .muted {
  font-size: 12px;
  font-weight: 400;
}
nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.navitem {
  text-align: left;
  background: transparent;
  border: none;
  border-radius: var(--radius);
  padding: 8px 10px;
  color: var(--text-secondary);
  font-size: 13px;
}
.navitem:hover {
  background: var(--surface-2);
}
.navitem.on {
  background: var(--bg-accent);
  color: var(--text-accent);
}
.navitem.primary {
  background: var(--bg-accent);
  color: var(--text-accent);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px var(--text-accent);
}
.navitem.primary:hover {
  filter: brightness(0.96);
}
.navitem.primary.on {
  background: var(--border-accent);
  color: #fff;
  box-shadow: none;
}
.navitem-mark {
  display: inline-block;
  margin-right: 5px;
  font-size: 9px;
}
.nav-foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.content {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 20px 24px;
}
</style>
