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
        out[name] = ''.join(node.itertext())
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


def _load_table(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


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
    target = table[hit_key].get(args.to_locale)
    if target is None:
        print(f"[resolve][warn] key={hit_key} 在语言 {args.to_locale} 下没有译文，"
              f"回退用原文 {args.text!r}（翻译包本身覆盖缺口，非本工具问题）", file=sys.stderr)
        target = args.text
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
