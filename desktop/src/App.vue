<script setup lang="ts">
import { ref, onMounted } from "vue";
import { store } from "./store";
import Setup from "./views/Setup.vue";
import Overview from "./views/Overview.vue";
import Devices from "./views/Devices.vue";
import Runner from "./views/Runner.vue";
import Resources from "./views/Resources.vue";
import Evidence from "./views/Evidence.vue";
import Boards from "./views/Boards.vue";
import Cleanup from "./views/Cleanup.vue";

type View = "overview" | "devices" | "runner" | "resources" | "evidence" | "boards" | "cleanup" | "setup";
const active = ref<View>("runner");
const ready = ref(false);

const nav: { key: View; label: string }[] = [
  { key: "overview", label: "概览" },
  { key: "devices", label: "设备" },
  { key: "resources", label: "资源库" },
  { key: "runner", label: "执行台" },
  { key: "evidence", label: "证据" },
  { key: "boards", label: "看板" },
];

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
          :class="{ on: active === n.key }"
          :disabled="!store.cfg?.configured"
          @click="active = n.key"
        >
          {{ n.label }}
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
      <!-- 只保活 Runner：跑固化脚本时切走 tab 不销毁它，执行状态/流式日志得以延续；
           其余视图仍按原样每次进入重新挂载（切回自动刷新数据）。 -->
      <keep-alive v-else include="Runner">
        <Overview v-if="active === 'overview'" />
        <Devices v-else-if="active === 'devices'" />
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
