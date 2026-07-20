#!/bin/bash
# AI_auto_test 桌面壳 (Tauri + Vue3) 开发模式启动器（双击运行）
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.cargo/env"

# 首次运行自动装前端依赖
[ -d node_modules ] || npm install

npm run tauri dev