#!/usr/bin/env python3
"""Claude Code / Codex CLI 的 headless 调用适配层。

桌面壳只负责把 ``AI_PROVIDER`` 与当前引擎的模型写进子进程环境；Python 工具通过这里统一：

- 定位 GUI App 最容易漏掉 PATH 的本机 CLI；
- 生成各 CLI 的非交互命令；
- 保留空模型字符串 = 跟随对应 CLI 默认模型的语义；
- 把调用结果收敛成相同的 ``(returncode, output)`` 契约。

不要把两个 CLI 的参数散落回 auto_repair.py / issue_register.py。Codex 的 ``-a never`` 是全局
参数，必须放在 ``exec`` 前；放在后面会被当前 CLI 当作未知的 exec 参数。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROVIDERS = ("claude", "codex")
DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "codex": "gpt-5.6-sol",
}
DISPLAY_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
}


def normalize_provider(value: str | None) -> str:
    provider = (value or "claude").strip().lower()
    return provider if provider in PROVIDERS else "claude"


def provider_from_env() -> str:
    return normalize_provider(os.environ.get("AI_PROVIDER"))


def model_from_env(env_name: str, provider: str) -> str:
    """键不存在才用引擎默认；键存在且为空必须保留，表示跟随 CLI 默认。"""
    if env_name in os.environ:
        return os.environ[env_name]
    return DEFAULT_MODELS[normalize_provider(provider)]


def display_name(provider: str) -> str:
    return DISPLAY_NAMES[normalize_provider(provider)]


def find_cli(provider: str) -> str | None:
    """定位本机 CLI；Finder/Launchpad 启动的 GUI 进程通常没有用户 shell 的完整 PATH。"""
    name = normalize_provider(provider)
    candidates = [
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ]
    if home_value := os.environ.get("HOME"):
        home = Path(home_value)
        candidates[0:0] = [
            home / ".local" / "bin" / name,
            home / ".npm-global" / "bin" / name,
            home / ".bun" / "bin" / name,
        ]
    if os.name == "nt":
        if app_data := os.environ.get("APPDATA"):
            candidates.append(Path(app_data) / "npm" / f"{name}.cmd")
        if user_profile := os.environ.get("USERPROFILE"):
            candidates.append(
                Path(user_profile) / "AppData" / "Roaming" / "npm" / f"{name}.cmd"
            )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def build_headless_command(
    provider: str,
    cli_bin: str,
    repo: Path,
    model: str,
    task: str,
) -> list[str]:
    """构造命令但不执行，便于单测守住两套 CLI 的参数顺序与权限边界。"""
    provider = normalize_provider(provider)
    if provider == "claude":
        allowed_tools = (
            ["Read", "Edit", "Glob", "Grep"]
            if task == "repair"
            else ["Read", "Glob", "Grep", "Bash(python3 tools/case_issue.py:*)"]
        )
        command = [
            cli_bin,
            "-p",
            "",  # 调用方在 run_headless() 里替换成完整 user prompt
            "--allowedTools",
            *allowed_tools,
            "--permission-mode",
            "acceptEdits",
            "--add-dir",
            str(repo),
            "--max-turns",
            "40",
            "--output-format",
            "text",
        ]
        if model:
            command += ["--model", model]
        return command

    # -a 是 codex 的全局参数，必须位于 exec 子命令之前。workspace-write 允许自愈改指定 flow，
    # 也允许问题登记助手执行 case_issue.py 写账本；具体可改范围仍由任务提示词收窄。
    command = [
        cli_bin,
        "-a",
        "never",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(repo),
        "--ephemeral",
        "--color",
        "never",
    ]
    if model:
        command += ["--model", model]
    command.append("-")  # 完整提示从 stdin 传，避开超长 argv 与 shell 转义
    return command


def run_headless(
    provider: str,
    cli_bin: str,
    repo: Path,
    model: str,
    task: str,
    user_prompt: str,
    system_prompt: str,
    timeout: int,
) -> tuple[int, str]:
    provider = normalize_provider(provider)
    command = build_headless_command(provider, cli_bin, repo, model, task)
    stdin = subprocess.DEVNULL
    input_text = None
    if provider == "claude":
        command[2] = user_prompt
        # Claude 支持追加 system prompt，继续沿用原来的强约束优先级。
        command += ["--append-system-prompt", system_prompt]
    else:
        # Codex CLI 没有 append-system-prompt；把框架边界与任务正文合成一个完整的 stdin prompt。
        input_text = (
            "以下是本次后台任务的强制边界，必须完整遵守：\n\n"
            f"{system_prompt}\n\n"
            "===== 本次任务 =====\n"
            f"{user_prompt}"
        )
        stdin = None

    result = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        input=input_text,
        stdin=stdin,
        capture_output=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    output = result.stdout or ""
    if result.returncode != 0 and result.stderr:
        output += "\n" + result.stderr
    return result.returncode, output.strip()
