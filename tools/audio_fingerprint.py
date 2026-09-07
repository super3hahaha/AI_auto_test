#!/usr/bin/env python3
"""audio_fingerprint —— 用 Chromaprint（fpcalc）算两段音频的内容相似度，回答"这段音频的
内容是不是那段音频（或那段音频的某个时间窗口）"，而不是"响度/时长像不像"。

背景：`output-check --ffprobe`/`tools/audio_envelope.py` 都只能证明"产物存在、时长对、
某个窗口响度对不对"——两段完全不同的音频完全可能凑出同样的时长、甚至同样的 RMS 响度
（尤其是要验证"混合时把某条音轨音量调到 0% 后，产物是否等于另一条音轨原样保留"这类场景，
RMS dB 只能证明"响度接近"，证不出"内容真的是同一份录音，不是碰巧同样响的别的声音"）。
Chromaprint 是专门做"这段音频是不是同一份录音"的内容指纹算法，抗有损转码/比特率变化，
比 RMS 能量、逐字节 diff 或波形图肉眼比对都更能直接回答"内容对不对"这个问题——本工具从
`apps/MP3Cutter/flows/check_merge_order.py`（验证合并产物各段跟源文件顺序是否一致）抽出
最小公共核心（提取片段→算指纹→算相似度），供任何"验证某段产物内容 == 某个源片段"的
固化脚本复用，不用每次都重新拼一遍 ffmpeg/fpcalc 调用。

相似度判读参考（`check_merge_order.py` 真机复核值，同一套算法两个用例复用同一套判读经验）：
同一份内容（哪怕经过不同编码/码率）相似度 0.9~1.0；互不相关的音频相似度落在 0.49~0.53
（指纹本身按位比较的随机基线，不是 0，别把"接近0.5"误判成"有一半内容相同"）。

用法：
  # 整段 vs 整段：
  python3 tools/audio_fingerprint.py <文件A> <文件B>
  # 各自可选截取一段（比如只用某个源文件的前 N 秒，去跟一个更短的产物比）：
  python3 tools/audio_fingerprint.py <文件A> --offset-a 0 --duration-a 40 <文件B>
  # 例：验证"混合产物(40s)"内容是否等于"track1(60s)的前40秒"：
  python3 tools/audio_fingerprint.py 产物.mp3 track1.mp3 --duration-b 40

打印 `FIELD:similarity=<0~1的浮点数>`（算不出时打印 `FIELD:similarity=NaN`，跟 `field_of()`/
`db_ge()` 的 NaN 哨兵值约定一致，调用方可直接复用同一套 NaN 防护），本工具只测量、不下
通过/失败结论，阈值判读交给调用方（不同场景该多接近才算"内容相同"、多低才算"内容无关"
需要结合具体素材真机复核后再定，见 check_merge_order.py 头注的判读经验）。

不装 ffmpeg/fpcalc（chromaprint）时打印警告到 stderr 并以非0退出——调用方应当据此跳过这层
校验，不要把"环境缺依赖"和"真的检测到内容不匹配"混为一谈。
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def need_tools():
    missing = [t for t in ("ffmpeg", "fpcalc") if not shutil.which(t)]
    if missing:
        print(f"[audio_fingerprint] 宿主机缺工具 {missing}"
              f"（brew install ffmpeg chromaprint），跳过内容指纹比对", file=sys.stderr)
        sys.exit(2)


def decode(src, out_path, rate, offset, duration):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if offset is not None:
        cmd += ["-ss", str(offset)]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-ar", str(rate), "-ac", "1", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[audio_fingerprint] ffmpeg 解码失败：{(r.stderr or '').strip()[-300:]}", file=sys.stderr)
        return False
    return True


def fingerprint(path):
    r = subprocess.run(["fpcalc", "-raw", "-plain", str(path)], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"[audio_fingerprint] fpcalc 解析失败：{(r.stderr or '').strip()[-300:]}", file=sys.stderr)
        return None
    return [int(x) for x in r.stdout.strip().split(",") if x]


def similarity(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return None
    same_bits = sum(32 - bin((a[i] ^ b[i]) & 0xffffffff).count("1") for i in range(n))
    return same_bits / (n * 32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file_a")
    ap.add_argument("file_b")
    ap.add_argument("--offset-a", type=float, default=None, help="A 截取起点(秒)，不传=从头")
    ap.add_argument("--duration-a", type=float, default=None, help="A 截取时长(秒)，不传=到结尾")
    ap.add_argument("--offset-b", type=float, default=None, help="B 截取起点(秒)，不传=从头")
    ap.add_argument("--duration-b", type=float, default=None, help="B 截取时长(秒)，不传=到结尾")
    ap.add_argument("--rate", type=int, default=11025, help="统一解码采样率，避免两边格式采样率不同干扰指纹（默认11025，同 check_merge_order.py）")
    args = ap.parse_args()

    need_tools()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wav_a, wav_b = td / "a.wav", td / "b.wav"
        if not decode(args.file_a, wav_a, args.rate, args.offset_a, args.duration_a):
            print("FIELD:similarity=NaN")
            sys.exit(1)
        if not decode(args.file_b, wav_b, args.rate, args.offset_b, args.duration_b):
            print("FIELD:similarity=NaN")
            sys.exit(1)
        fp_a, fp_b = fingerprint(wav_a), fingerprint(wav_b)
        if fp_a is None or fp_b is None:
            print("FIELD:similarity=NaN")
            sys.exit(1)
        sim = similarity(fp_a, fp_b)
        if sim is None:
            print("FIELD:similarity=NaN")
            sys.exit(1)
        print(f"FIELD:similarity={sim:.4f}")


if __name__ == "__main__":
    main()
