#!/usr/bin/env python3
"""临时探针：跳过/关闭按钮出现在屏上时运行，dump 当前树看它进不进无障碍树、选择器是什么。
用法：python3 tools/_probe_skip.py [serial]
"""
import sys, re
import xml.etree.ElementTree as ET
import uiautomator2 as u2

S = sys.argv[1] if len(sys.argv) > 1 else "24500fb49a0d7ece"
d = u2.connect(S)
print("前台:", d.app_current())
nodes = list(ET.fromstring(d.dump_hierarchy()).iter("node"))
print("节点总数:", len(nodes))

print("\n=== 所有 clickable=true 节点 ===")
for n in nodes:
    if n.get("clickable") == "true":
        print(f"  cls={n.get('class','').split('.')[-1]:<12} id={(n.get('resource-id') or '')[-26:]:<26} "
              f"text={n.get('text','')[:16]!r} desc={(n.get('content-desc') or '')[:26]!r} bounds={n.get('bounds')}")

print("\n=== 含 skip/close/关闭/跳过/箭头/→ 或在右上角(y<200 且 x>屏宽70%)的节点 ===")
kw = re.compile(r"skip|close|dismiss|关闭|跳过|→|arrow|next|continue", re.I)
W = 1080
for n in nodes:
    b = n.get("bounds", "")
    m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
    topright = False
    if m:
        x1, y1, x2, y2 = map(int, m.groups())
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        topright = (cy < 200 and cx > W * 0.7)
    blob = " ".join([n.get('resource-id',''), n.get('text',''), n.get('content-desc','')])
    if kw.search(blob) or topright:
        print(f"  {'[右上角]' if topright else '[关键词]'} cls={n.get('class','').split('.')[-1]:<12} "
              f"clickable={n.get('clickable')} id={(n.get('resource-id') or '')[-24:]!r} "
              f"text={n.get('text','')[:16]!r} desc={(n.get('content-desc') or '')[:30]!r} bounds={b}")
