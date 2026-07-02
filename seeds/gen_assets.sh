#!/bin/bash
# 造数据：生成多格式测试音频到 assets/（正弦波，便于区分与校验时长）。
# 依赖 ffmpeg（brew install ffmpeg）。ape=Monkey's Audio，ffmpeg 无编码器 → 需另找 .ape 样本。
set -e
cd "$(dirname "$0")/.."
mkdir -p assets
export PATH="/opt/homebrew/bin:$PATH"
gen() { # $1=秒 $2=频率Hz $3=basename
  for fmt in mp3 aac flac wav; do   # ogg 不生成：ffmpeg lavfi 产的 ogg 缺时长头，ogg 用真实 assets/real_tagged.ogg
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=$2:duration=$1" "assets/$3.$fmt"
  done
}
gen 30 440 test_a_30s   # A：30秒 440Hz
gen 10 880 test_b_10s   # B：10秒 880Hz（合并/混合时区分）
echo "生成完成："; ls -1 assets/
echo "注意：ape 未生成（ffmpeg 不支持编码），如需 ape 格式用例请手动放入一个 .ape 样本。"
