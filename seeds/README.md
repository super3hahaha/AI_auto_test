# seeds/ —— 构造前置态

存"造前置数据"的脚本，供 RUNBOOK 主循环第 3 步用。**空是正常的**——只有需要精确初始状态、且当前 App 支持对应手段时才往里放。

## 三种前置手段（按 App 情况选）

### 1. DB seed（.sql）——需要 debuggable 包
往 App 的 SQLite 直接灌数据，一条 `.sql` 秒建复杂状态。
```
python3 tools/adbkit.py --serial <S> seed seeds/<用例>.sql
```
文件按用例命名，如 `seeds/CRUD-REMOVE-01.sql`。内容是若干 `INSERT/UPDATE/DELETE`。
> 依赖 `run-as`，**非 debug 包用不了**；此时 `config.db_name` 留空，走下面两种。

### 2. 文件素材 seed（.sh / adb push）——非 debug 也能用
App 依赖外部素材（如 MP3Cutter 要一个音频文件）时，推一个已知文件进去并触发媒体扫描：
```
adb -s <S> push assets/sample_30s.mp3 /sdcard/Music/
adb -s <S> shell "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Music/sample_30s.mp3"
```
这类可写成 `seeds/<用例>.sh`，把素材放 `assets/`。

### 3. UI 前置——走设置/操作把状态点好
开关类前置（如"关闭某显示项"）直接在用例 steps 里用选择器点到位，不单独建 seed。

## 现状

当前被测 `ringtone.maker.mp3.cutter.audio` 是**非 debug** 包，DB seed 不可用；已跑的 CUT-CORE-01 前置（设备已有音频）天然满足，故 seeds/ 暂空。需要确定性素材时用手段 2。
