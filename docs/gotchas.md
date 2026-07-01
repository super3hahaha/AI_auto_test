# gotchas —— 已知坑（踩到直接记 GAP/BLOCK 继续，别卡死）

来自对原表一次真实跑动的复盘，纯模拟器路径最常见的几个约束：

- **App 必须 debuggable**：`run-as` 才能读 App 私有目录（DB/SP）。用非 debug 包 → `db`/`sp` 命令全失败。装可调试版本。
- **固定时间/日期**：很多状态（late / future / 排卵窗口）依赖"今天"。模拟器日期不固定就复现不了 → 记 `GAP-`。要么固定模拟器时钟，要么在 seed 里把日期算成相对今天。
- **Activity not exported**：`am start` 直拉内部页会被系统拦。走正常 UI 入口，别抄近路。
- **无文件选择器 / 无云账号**：模拟器缺 `ACTION_GET_CONTENT`/`OPEN_DOCUMENT` provider，导入/恢复/云同步类用例跑不了 → 记 `BLOCK-`，或归入"排除用例"。
- **设备无 sqlite3**：`sql` 子命令依赖设备自带 sqlite3；没有就用 `db`（拉出来本地 dump）代替。
- **run-as 路径含空格**：导出脚本里路径别用裸空格，注意引用（原表 RG-NU-01 踩过）。
- **uiautomator dump 偶发失败**：某些动画/弹窗瞬间 dump 不出树，重试一次或先等界面稳定。
- **坐标随分辨率变**：`tap X Y` 是绝对坐标，换设备/分辨率要重算。优先用 `ui` 拿到控件 bounds 再算中心点。

## 需要外部依赖 → 直接排除（写进 excluded.csv）

Wear / Widget / Partner 双端 / 跨端云同步 / 厂商保活（小米华为三星等）/ 旧 UI 专项 / 需真实 Google 账号的备份恢复。这些不在纯模拟器范围内。
