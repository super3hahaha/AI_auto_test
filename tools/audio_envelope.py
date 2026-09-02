#!/usr/bin/env python3
"""audio_envelope —— 按时间窗口算音频的 RMS 能量（dB），用来判断"是否发生了渐变/淡入淡出"。

背景：`output-check --ffprobe` 只能核对总时长，核对不了"边界处是不是真的做了音频层面的
淡入淡出混合"——时长对，完全可能是硬切而不是渐变，两者产物时长一样，纯时长断言分不出来
（见 docs/gotchas.md 2026-08-25 MERGE-ADV-01/MERGE-CROSSFADE-01 相关条目）。这个工具补上
这一层：整曲一次性解码成 PCM（避免反复 `ffmpeg -ss` 现场 seek 带来的解码器启动瞬态噪声），
按传入的时间窗口算 RMS 能量（线性功率求平均再转 dB，不是对 dB 值直接算术平均，更符合能量
叠加的物理意义），每个窗口打一行 `FIELD:<name>=<dB>`（找不到/静音窗口打 `FIELD:<name>=NaN`，
前缀跟 `adbkit.py ui --field` 一致，flow 脚本可以复用同一个 `field_of()` 解析函数），
供调用方（flow 脚本）自己比较前后关系判定趋势，本工具不下判定结论。

用法：
  python3 tools/audio_envelope.py <audio_path> --window "name:start_s:end_s" [--window ...]
  # 例：判断 t=60s 处是否有 2.5s 淡出+2.5s 淡入：
  python3 tools/audio_envelope.py out.mp3 \\
    --window "fadeout_front:57.5:58.75" --window "fadeout_back:58.75:60.0" \\
    --window "fadein_front:60.0:61.25" --window "fadein_back:61.25:62.5"

不装 ffmpeg 或解码失败时打印警告到 stderr 并以非0退出——调用方应当据此跳过这层校验，
不要把"环境缺依赖"和"真的检测到异常"混为一谈。
"""
import argparse, math, os, shutil, subprocess, sys, tempfile, wave, audioop


def decode_to_wav(src, sr=8000):
    if not shutil.which("ffmpeg"):
        print("[audio_envelope] 宿主机未装 ffmpeg（brew install ffmpeg），跳过振幅包络分析", file=sys.stderr)
        return None
    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="audio_envelope_")
    os.close(fd)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(sr), "-acodec", "pcm_s16le", wav_path],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"[audio_envelope] ffmpeg 解码失败：{(r.stderr or '').strip()[-300:]}", file=sys.stderr)
        os.unlink(wav_path)
        return None
    return wav_path


def window_rms_db(data, sr, width, start_s, end_s):
    i0, i1 = int(start_s * sr), int(end_s * sr)
    seg = data[i0 * width: i1 * width]
    if len(seg) < width:
        return None
    # 按 0.1s 子块算线性功率再求平均，比对整段一次性 audioop.rms 更贴近"能量随时间的真实均值"
    # （尤其窗口较长时，一次性 rms 会被极端峰值主导，子块平均更稳）。
    chunk_n = max(1, int(sr * 0.1))
    sq_vals = []
    for i in range(i0, i1, chunk_n):
        chunk = data[i * width: min(i + chunk_n, i1) * width]
        if len(chunk) < width:
            continue
        rms = audioop.rms(chunk, width)
        sq_vals.append(rms * rms)
    if not sq_vals:
        return None
    mean_sq = sum(sq_vals) / len(sq_vals)
    if mean_sq <= 0:
        return None
    return 10 * math.log10(mean_sq / (32768.0 ** 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio_path")
    ap.add_argument("--window", action="append", default=[], required=True,
                     help="name:start_s:end_s，可重复传多个窗口")
    ap.add_argument("--sr", type=int, default=8000, help="降采样率，默认8000Hz（够算RMS，解码更快）")
    a = ap.parse_args()

    wav_path = decode_to_wav(a.audio_path, a.sr)
    if wav_path is None:
        sys.exit(1)
    try:
        w = wave.open(wav_path, "rb")
        sr = w.getframerate()
        width = 2
        data = w.readframes(w.getnframes())
        w.close()
        for spec in a.window:
            parts = spec.split(":")
            if len(parts) != 3:
                print(f"[audio_envelope] --window 格式错误（应为 name:start:end）：{spec}", file=sys.stderr)
                continue
            name, start_s, end_s = parts[0], float(parts[1]), float(parts[2])
            db = window_rms_db(data, sr, width, start_s, end_s)
            print(f"FIELD:{name}={'NaN' if db is None else round(db, 1)}")
    finally:
        os.unlink(wav_path)


if __name__ == "__main__":
    main()
