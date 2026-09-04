#!/usr/bin/env python3
"""lang_table —— 从多语言 strings.xml 资源包（Android values-<locale>/strings.xml 目录树，
可以是解压后的目录，也可以直接给 zip；产物结构与 lang-string-compare skill 的
extract_apk_strings.py 从 apk 反编译出的产物一致，两种来源都能喂）构建一张
「资源 key -> 各语言译文」的映射表，供固化脚本（apps/<slug>/flows/flow_*.sh）运行时把
写死的选择器文案（taptext/tapdesc/waitfor text）按目标语言换算，不再依赖设备当前语言
必须跟固化时一致。

背景：resource-id（tapid/waitfor id）本身跨语言稳定，真正怕语言切换的是那些只能靠
text/content-desc 定位或判定成功的步骤（waitfor 成功文案尤其常见——本来判定的就是一句
本地化提示语）。原来这些步骤只能在固化时的语言下工作，App 一切语言就直接报错。

用法：
  1) 建表（一次性，App 出新版翻译包时重新跑一遍）：
       python3 tools/lang_table.py build "<资源包目录或zip>" \\
           --out apps/<slug>/lang/strings_table.json [--locales zh-rCN ja en ...] \\
           [--default-alias en]
     --default-alias：Android 默认 values/ 目录（没有 -<locale> 后缀）实际对应哪个语言，
     给了就顺手把这份默认文案也存一份到该语言代号下（比如这份翻译包 values/ 实测是英文，
     传 --default-alias en 之后就能直接 --from en / --to en 用，不用记住 "default" 这个
     内部占位名）。不传也不影响功能，只是少一个更好记的别名。

  2) 固化脚本运行时按语言查译文（单条查询，配合 $() 用在 taptext/tapdesc 里）：
       python3 tools/lang_table.py resolve apps/<slug>/lang/strings_table.json "音频裁剪" \\
           --from zh-rCN --to ja
     找不到 --from 文案对应的 key → 非0退出（说明这段文案根本不是来自 strings.xml，或者
     --from 语言选错了，需要人工核实，不该悄悄放过）。
     key 找到了但 --to 语言译文缺失（翻译包本身没补全）→ 打印原文兜底 + stderr 警告，
     不中断——总比直接报错让整条流程失败要好，缺失的这一条本身就是翻译包的覆盖缺口。

  3) 查表覆盖了哪些语言（核对翻译包完整性用）：
       python3 tools/lang_table.py locales apps/<slug>/lang/strings_table.json

退出码：build/locales 恒 0（除非参数错）；resolve 找不到 key 时非0，其余情况 0。
"""
import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _appctx import LANG, load_cfg, probe_installed_build  # 多 App 路径 + 设备实装版本现查

_LOCALE_DIR_RE = re.compile(r'^values(-(?P<locale>[a-zA-Z0-9+-]+))?$')


def _iter_locale_files(root: Path):
    """遍历 root 下所有 values*/*.xml（不限 strings.xml 一个文件名——像这份翻译包一样
    拆成 strings.xml/strings_player.xml/strings_inshot.xml 几份的，同语言下的 key 会
    合并进同一个 locale 命名空间，跟 Android 资源合并规则一致）。"""
    for p in root.rglob('*.xml'):
        m = _LOCALE_DIR_RE.match(p.parent.name)
        if not m:
            continue
        yield (m.group('locale') or 'default'), p


_ESC_RE = re.compile(r'\\(u[0-9a-fA-F]{4}|.)', re.S)
_ESC_MAP = {'n': '\n', 't': '\t', 'r': '\r', "'": "'", '"': '"', '\\': '\\', '@': '@', '?': '?'}


def _unescape_android(raw: str) -> str:
    """把 strings.xml 里的「源码写法」还原成设备上真正显示的字符串。

    只用于 XML/zip 路线：Android 打包时会做这层反解，所以 apk 路线（aapt2 dump）
    读到的已经是运行时真值，不能再解一次（否则 "100\\% 完成" 之类会被二次破坏）。

    处理三件事，缺一条都会让 taptext 在目标语言下匹配不上屏幕真实文案：
      1. 反斜杠转义：\\' \\" \\n \\t \\\\ \\@ \\? \\uXXXX
      2. 外层双引号：Android 里 "  两端留白  " 表示「这些空格是有意义的」，去引号留空白
      3. 未加引号时按 XML 规则折叠空白：首尾 trim + 内部连续空白（含换行/缩进）压成单空格
    """
    def sub(m):
        g = m.group(1)
        if g[0] == 'u':
            return chr(int(g[1:], 16))
        return _ESC_MAP.get(g, g)

    quoted = len(raw) >= 2 and raw.startswith('"') and raw.endswith('"')
    if quoted:
        raw = raw[1:-1]
    else:
        raw = re.sub(r'\s+', ' ', raw).strip()
    return _ESC_RE.sub(sub, raw)


def _find_aapt2() -> str:
    """定位 aapt2：$AAPT2 > PATH > Android SDK build-tools 里版本号最大的一个。

    走 aapt2 而不是 apktool，是因为它不依赖 JRE（这台机器就没装 java），
    且直接吐运行时值，省掉一层 XML 反转义的坑。"""
    import os
    import shutil
    env = os.environ.get('AAPT2')
    if env and Path(env).exists():
        return env
    found = shutil.which('aapt2')
    if found:
        return found
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') \
        or str(Path.home() / 'Library/Android/sdk')
    bt = Path(sdk) / 'build-tools'
    cands = sorted((d / 'aapt2' for d in bt.glob('*') if (d / 'aapt2').exists()),
                   key=lambda x: [int(n) if n.isdigit() else 0 for n in x.parent.name.split('.')])
    if cands:
        return str(cands[-1])
    sys.exit("[build-apk] 找不到 aapt2：设 $AAPT2 指到可执行文件，或装 Android SDK build-tools。")


# Android 的伪本地化 locale：`en-rXB` 是双向文本(RTL)伪翻译、`en-rXC` 是重音/加长伪翻译，
# 都是给开发自查布局用的假语言，设备正常不会跑在这上面。收进表只会把「语言」下拉塞满噪音。
_PSEUDO_LOCALES = {'en-rXA', 'en-rXB', 'en-rXC', 'ar-rXB'}

_RES_LINE = re.compile(r'^\s{4}resource 0x[0-9a-f]+ (?P<type>\w+)/(?P<name>\S+)')
_VAL_START = re.compile(r'^\s{6}\((?P<locale>[^)]*)\)\s(?P<rest>.*)$')


def cmd_build_apk(args):
    """从 apk 里 dump 出真正入包的字符串资源建表。

    相比喂翻译包 zip 的好处（都是实测出来的，不是理论）：
      - 拿到的是「这一版 apk 实际装到设备上的文案」，翻译漏入包 / 被覆盖 / 某语言压根没打进去
        这些情况一眼可见，不会建出一张设备上根本不存在的文案表
      - 覆盖 apk 支持的全部 locale（含 es-rMX / zh-rHK 这类区域变体），翻译包通常只有主语言
      - 值已是运行时真值，不需要猜转义规则

    代价：表跟 apk 版本绑死，换被测包要重新跑一次。
    """
    import subprocess
    aapt2 = _find_aapt2()
    apk = Path(args.apk)
    if not apk.exists():
        sys.exit(f"[build-apk] apk 不存在：{apk}")
    proc = subprocess.run([aapt2, 'dump', 'resources', str(apk)],
                          capture_output=True, text=True, errors='replace')
    if proc.returncode != 0:
        sys.exit(f"[build-apk] aapt2 失败（{proc.returncode}）：{proc.stderr[:500]}")

    table, expected = _parse_aapt2_dump(proc.stdout, args.locales)
    if not table:
        sys.exit("[build-apk] 没解析出任何 string 资源，apk 或 aapt2 输出格式可能变了，先人工看一眼 dump。")
    if expected is not None and len(table) != expected:
        # entryCount 是 aapt2 自己报的条目数，对不上说明多行值把解析带偏了，宁可报错也别产出残表
        sys.exit(f"[build-apk] 解析出 {len(table)} 个 key，但 aapt2 报 entryCount={expected}，"
                 f"对不上，说明 dump 解析有遗漏，别用这张表。")
    locales_seen = set()
    for entry in table.values():
        locales_seen.update(entry)
    if args.default_alias and 'default' in locales_seen:
        for entry in table.values():
            if 'default' in entry:
                entry.setdefault(args.default_alias, entry['default'])
        locales_seen.add(args.default_alias)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    print(f"[build-apk] {apk.name}: {len(table)} 个 key，覆盖 {len(locales_seen)} 个语言 -> {out_path}")


def _parse_aapt2_dump(text: str, keep_locales=None):
    """解析 `aapt2 dump resources` 的文本输出，只取 string 段。

    唯一的坑：含换行的文案（权限引导那种「1.打开设置\\n2.点击权限」）在 dump 里是**跨行**输出的，
    按行正则会把这些 key 整条丢掉。所以对未闭合的值要一直累积到收尾引号那一行。
    """
    table = {}
    expected = None
    cur_name = None
    in_string_type = False
    pending_locale = None
    pending_buf = None

    def flush():
        nonlocal pending_locale, pending_buf
        if cur_name is not None and pending_locale is not None:
            keep = (keep_locales is None or pending_locale in keep_locales
                    or pending_locale == 'default')
            if keep and pending_locale not in _PSEUDO_LOCALES:
                table.setdefault(cur_name, {})[pending_locale] = '\n'.join(pending_buf)
        pending_locale, pending_buf = None, None

    for line in text.splitlines():
        if pending_buf is not None:
            # 多行值续行：收尾行以未转义的 " 结束
            if line.rstrip().endswith('"'):
                pending_buf.append(line.rstrip()[:-1].strip())
                flush()
            else:
                pending_buf.append(line.strip())
            continue

        m = re.match(r'^\s{2}type (\w+) id=\w+(?: entryCount=(\d+))?', line)
        if m:
            in_string_type = m.group(1) == 'string'
            if in_string_type and m.group(2):
                expected = int(m.group(2))
            cur_name = None
            continue

        rm = _RES_LINE.match(line)
        if rm:
            cur_name = rm.group('name') if rm.group('type') == 'string' else None
            continue

        if not in_string_type or cur_name is None:
            continue

        vm = _VAL_START.match(line)
        if not vm:
            continue
        rest = vm.group('rest')
        if not rest.startswith('"'):
            continue  # (file) / 空值等非字面量，跳过
        locale = vm.group('locale') or 'default'
        body = rest[1:]
        pending_locale = locale
        if body.endswith('"'):
            pending_buf = [body[:-1]]
            flush()
        else:
            pending_buf = [body]  # 跨行，继续累积
    return table, expected


def _parse_strings(xml_path: Path) -> dict:
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"[warn] 跳过无法解析的 {xml_path}: {e}", file=sys.stderr)
        return {}
    out = {}
    for node in tree.getroot().findall('string'):
        name = node.get('name')
        if not name:
            continue
        out[name] = _unescape_android(''.join(node.itertext()))
    return out


def _extract_zip_to_tmp(zip_path: Path) -> Path:
    """部分翻译包 zip 里的文件名不是 UTF-8（导出工具用了 GBK 之类），Python zipfile 默认按
    cp437 解出来是乱码；这里按 cp437→gbk 修正文件名后再落盘到临时目录，避免目录名乱码/冲突。
    修不出中文名（本来就是纯 ASCII 路径）时原样使用，不影响。"""
    tmp = Path(tempfile.mkdtemp(prefix='lang_table_'))
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            try:
                fixed = name.encode('cp437').decode('gbk')
            except Exception:
                fixed = name
            target = tmp / fixed
            if name.endswith('/'):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    return tmp


def cmd_build(args):
    src = Path(args.source)
    root = _extract_zip_to_tmp(src) if src.is_file() and src.suffix.lower() == '.zip' else src
    table = {}
    locales_seen = set()
    for locale, xml_path in _iter_locale_files(root):
        if args.locales and locale not in args.locales and locale != 'default':
            continue
        locales_seen.add(locale)
        for name, text in _parse_strings(xml_path).items():
            table.setdefault(name, {})[locale] = text
    if args.default_alias and 'default' in locales_seen:
        for entry in table.values():
            if 'default' in entry:
                entry.setdefault(args.default_alias, entry['default'])
        locales_seen.add(args.default_alias)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    print(f"[build] {len(table)} 个 key，覆盖 {len(locales_seen)} 个语言"
          f"（{','.join(sorted(locales_seen))}）-> {out_path}")


# ── ensure：按「设备此刻实装的 versionCode」自动备好表（decisions #55）──────────────
# 表不该等人手动建。但也不能每次跑都重建（拉 18MB apk + aapt2 约 7s），所以按 versionCode 缓存：
#   apps/<slug>/lang/tables/<versionCode>.json   一版一张
#   apps/<slug>/lang/index.json                  {versionCode: {versionName, built_at, keys, locales}}
# 为什么 key 必须是 versionCode 而不是「一张全局表」：多设备并行时各台装的版本可能不一样，
# 任何全局单表的设计在那种场景下必然错一台。选表没有自由度——由设备实装版本唯一确定。

def _tables_dir():
    return LANG / 'tables'


def _index_path():
    return LANG / 'index.json'


def _read_index():
    p = _index_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _write_index(idx):
    _index_path().parent.mkdir(parents=True, exist_ok=True)
    _index_path().write_text(json.dumps(idx, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')


def _has_lang_split(pkg, serial):
    """设备上这个包有没有按语言拆的 split（split_config.<lang>.apk）。

    有的话语言资源不全在 base.apk 里，只拉 base 会静默少语言——那种情况必须把语言 split
    一起拉下来分别 dump 再合并。目前遇到的包（MP3Cutter 2.3.6）只有 abi/dpi split，
    语言全在 base；先老实报错，等真撞上再实现合并，别猜着写。"""
    import subprocess
    args = ["adb"] + (["-s", serial] if serial else []) + ["shell", "pm", "path", pkg]
    out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout or ""
    paths = [l.strip()[len('package:'):] for l in out.splitlines() if l.strip().startswith('package:')]
    base = next((p for p in paths if p.endswith('base.apk')), None)
    langs = [p for p in paths
             if re.search(r'split_config\.(?!arm|armeabi|x86|mips|\w*dpi\b)[a-z]{2}(_[A-Za-z]+)?\.apk$', p)]
    return base, langs


def cmd_ensure(args):
    """备好「该设备此刻实装版本」对应的表，打印表路径到 stdout。已有则直接打印，不重建。

    幂等、可并发（flock 串行化建表，多设备同时首跑同一版本只有一个真在建，其余等它写完直接命中）。
    """
    import subprocess
    import time
    pkg = args.package or (load_cfg().get('package') or '')
    if not pkg:
        sys.exit("[ensure] 没有包名：给 --package，或让 target.json 里有 package 字段。")
    name, code = probe_installed_build(pkg, args.serial)
    if not code:
        sys.exit(f"[ensure] 查不到设备 {args.serial or '(默认)'} 上 {pkg} 的 versionCode——"
                 f"App 没装？设备离线？（adb devices 确认）")
    target = _tables_dir() / f"{code}.json"
    if target.exists() and not args.force:
        print(target)
        return

    LANG.mkdir(parents=True, exist_ok=True)
    lock_path = LANG / '.lang.lock'
    with open(lock_path, 'w') as fh:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX)        # 阻塞式：并发首跑时后来者等前一个建完
        if target.exists() and not args.force:  # 双检：等锁期间别人已经建好了
            print(target)
            return
        base, lang_splits = _has_lang_split(pkg, args.serial)
        if not base:
            sys.exit(f"[ensure] `pm path {pkg}` 没找到 base.apk，无法建表。")
        if lang_splits:
            sys.exit(f"[ensure] 这个包有按语言拆的 split（{', '.join(Path(p).name for p in lang_splits)}），"
                     f"语言资源不全在 base.apk 里，只拉 base 会静默少语言——需要先实现多 split 合并，"
                     f"别用这张表。")
        tmp_apk = _tables_dir() / f".{code}.pulled.apk"
        tmp_apk.parent.mkdir(parents=True, exist_ok=True)
        pull = ["adb"] + (["-s", args.serial] if args.serial else []) + ["pull", base, str(tmp_apk)]
        r = subprocess.run(pull, capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not tmp_apk.exists():
            sys.exit(f"[ensure] 从设备拉 base.apk 失败：{(r.stderr or r.stdout)[:300]}")
        try:
            build_args = argparse.Namespace(apk=str(tmp_apk), out=str(target),
                                            locales=None, default_alias=args.default_alias)
            cmd_build_apk(build_args)
        finally:
            tmp_apk.unlink(missing_ok=True)
        tbl = _load_table(target)
        locales = sorted({l for e in tbl.values() for l in e})
        idx = _read_index()
        idx[code] = {'versionName': name, 'keys': len(tbl), 'locales': locales,
                     'built_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': 'device-pull'}
        _write_index(idx)
    print(target)


def cmd_index(args):
    idx = _read_index()
    if not idx:
        print("(还没有任何表；跑一次 `lang_table.py ensure --serial <s>` 或执行一条带 LANG_CODE 的用例即可自动建)")
        return
    for code in sorted(idx, key=lambda x: int(x) if x.isdigit() else 0, reverse=True):
        m = idx[code]
        print(f"{code}  {m.get('versionName') or '?':<10} {m.get('keys')} keys  "
              f"{len(m.get('locales') or [])} locales  built {m.get('built_at')}")


def _load_table(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


# 书写系统相同、Android 会跨区域互相 fallback 的已知组合。只收「有确定依据」的：
# 中文按脚本分繁简（zh-rHK/zh-rMO 走繁体 zh-rTW，Android 7+ LocaleList 就是这么解的），
# 拉丁美洲西语先回 es-r419 再回 es。其余一律不猜。
_SCRIPT_FALLBACK = {
    'zh-rHK': ['zh-rTW'], 'zh-rMO': ['zh-rTW'], 'zh-rSG': ['zh-rCN'],
    'es-rMX': ['es-r419'], 'es-rAR': ['es-r419'], 'es-rUS': ['es-r419'],
}


def _fallback_chain(locale: str):
    """目标语言取不到时的回退顺序，对齐 Android 的资源解析：
    精确 (zh-rHK) -> 同书写系统的近邻 (zh-rTW) -> 去区域 (zh) -> 默认 values/ (default)。

    近邻只走 _SCRIPT_FALLBACK 里那几组有确定依据的；不做泛化的同语族猜测——猜错会返回一句
    屏幕上根本不存在的文案，比老实回退到 default 更难排查。"""
    chain = [locale]
    chain += [x for x in _SCRIPT_FALLBACK.get(locale, []) if x not in chain]
    base = re.split(r'[-_]', locale)[0]
    if base and base != locale:
        chain.append(base)
    chain.append('default')
    return chain


def _lookup_with_fallback(entry: dict, locale: str):
    for cand in _fallback_chain(locale):
        if cand in entry:
            return entry[cand], cand
    return None, None


def cmd_resolve(args):
    table = _load_table(args.table)
    if args.key:
        if args.key not in table:
            sys.exit(f"[resolve] 表里没有 key={args.key!r}，先确认 --key 拼对了、或者表是不是建旧了。")
        hit_key = args.key
    else:
        candidates = [k for k, v in table.items() if v.get(args.from_locale) == args.text]
        if not candidates:
            sys.exit(f"[resolve] 在语言 {args.from_locale} 下找不到文案 {args.text!r} 对应的字符串资源 key"
                      f"（可能这段文案不是来自 strings.xml，或者 --from 语言代号选错了，需人工核实）。")
        if len(candidates) > 1:
            sys.exit(f"[resolve] 文案 {args.text!r} 在语言 {args.from_locale} 下同时对应 {len(candidates)} 个 "
                      f"key（{', '.join(candidates)}），不同 key 在目标语言下译文可能不一样，"
                      f"不能瞎猜——用 --key <具体key> 明确指定是哪一个（对照 App 实际控件，或翻页比对"
                      f"各 key 在其它已知语言下的值来确认，比如英文/日文往往不会撞车）。")
        hit_key = candidates[0]
    entry = table[hit_key]
    target, used = _lookup_with_fallback(entry, args.to_locale)
    if target is None:
        print(f"[resolve][warn] key={hit_key} 在语言 {args.to_locale} 下没有译文（回退链 "
              f"{' -> '.join(_fallback_chain(args.to_locale))} 全落空），回退用原文 {args.text!r}",
              file=sys.stderr)
        target = args.text
    elif used != args.to_locale:
        # 设备上这个语言本来就是 fallback 到这一级显示的，属正常，但要让人看见用的不是精确匹配
        print(f"[resolve][info] key={hit_key} 无 {args.to_locale} 专属译文，"
              f"按回退链取 {used}（链：{' -> '.join(_fallback_chain(args.to_locale))}）", file=sys.stderr)
    print(target)


def cmd_locales(args):
    table = _load_table(args.table)
    locales = set()
    for v in table.values():
        locales.update(v.keys())
    print('\n'.join(sorted(locales)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='从多语言 strings.xml 资源包（目录或zip）构建映射表')
    b.add_argument('source', help='资源包目录 或 zip 文件路径')
    b.add_argument('--out', required=True, help='输出 JSON 表路径，如 apps/<slug>/lang/strings_table.json')
    b.add_argument('--locales', nargs='*', default=None, help='只保留这些语言目录（如 zh-rCN ja en），不传=全量')
    b.add_argument('--default-alias', default=None, help='给默认 values/ 目录一个好记的语言别名，如 en')
    b.set_defaults(func=cmd_build)

    ba = sub.add_parser('build-apk', help='直接从 apk 里 dump 实际入包的多语言字符串建表（推荐）')
    ba.add_argument('apk', help='被测 apk 路径，如 apps/MP3Cutter/apks/2.3.5J.apk')
    ba.add_argument('--out', required=True, help='输出 JSON 表路径')
    ba.add_argument('--locales', nargs='*', default=None, help='只保留这些语言，不传=全量')
    ba.add_argument('--default-alias', default=None, help='给默认 values/ 一个别名，如 en')
    ba.set_defaults(func=cmd_build_apk)

    e = sub.add_parser('ensure', help='按设备实装 versionCode 自动备表并打印表路径（缓存命中则不重建）')
    e.add_argument('--serial', default='', help='目标设备 serial；多设备下必传')
    e.add_argument('--package', default='', help='包名；不传则读活跃 App 的 target.json')
    e.add_argument('--default-alias', default='en', help="默认 values/ 的别名，默认 en")
    e.add_argument('--force', action='store_true', help='已有表也强制重建（同 versionCode 换了内容时用）')
    e.set_defaults(func=cmd_ensure)

    i = sub.add_parser('index', help='列出已建好的各版本表')
    i.set_defaults(func=cmd_index)

    r = sub.add_parser('resolve', help='把某语言下的一段文案换算成另一语言的译文')
    r.add_argument('table', help='build 生成的 JSON 表路径')
    r.add_argument('text', help='当前（固化时）语言下的原文，如 "音频裁剪"')
    r.add_argument('--from', dest='from_locale', required=True, help='原文所属语言，如 zh-rCN')
    r.add_argument('--to', dest='to_locale', required=True, help='目标语言，如 ja')
    r.add_argument('--key', default=None,
                   help='原文在 --from 语言下同时对应多个 key（同一句文案被多个字符串资源撞车）时，'
                        '用这个明确指定具体 key，跳过按文案反查；不传时若命中多个 key 会报错列出候选，'
                        '不会静默猜一个')
    r.set_defaults(func=cmd_resolve)

    l = sub.add_parser('locales', help='列出表里覆盖的所有语言代码')
    l.add_argument('table')
    l.set_defaults(func=cmd_locales)

    args = ap.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
