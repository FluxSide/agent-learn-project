#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s02.py —— Tool Use（Windows PowerShell 版）

Agent Loop 与 s01 相同，本课在 s01 基础上新增四个工具和一个派发映射：

    +----------+      +-------+      +------------------------------+
    |   User   | ---> |  LLM  | ---> | Tool Dispatch                |
    |  prompt  |      |       |      | powershell -> run_powershell |
    +----------+      +---+---+      | read_file  -> run_read       |
                          ^          | write_file -> run_write      |
                          |          | edit_file  -> run_edit       |
                          +----------+ glob       -> run_glob       |
                          tool_result+------------------------------+

与 s01 相比的变化：
  + 新增 run_read / run_write / run_edit / run_glob 四个文件工具
  + TOOL_HANDLERS 派发映射取代硬编码的工具调用
  + safe_path 限制文件工具只能操作工作区内的文件
  + 命令执行工具从 bash 换成 powershell（适配 Windows，同 s01_ps）

运行前：
  1. pip install anthropic python-dotenv
  2. d:\\LLM_AppDev\\.env 配置：
         ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / MODEL_ID
  3. python s02.py（输入 q 退出）

核心认知：循环结构不变，只有工具注册和派发在增长。
"""

import os
import subprocess
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    # Windows 原生 Python 无 readline，忽略即可
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

# 配置了自定义服务地址时，移除可能干扰鉴权的 ANTHROPIC_AUTH_TOKEN
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# ---------- 系统提示词 ----------
SYSTEM = (
    f"You are a coding agent running on Windows at {os.getcwd()}. "
    "Use PowerShell 5.1 to solve tasks. Act, don't explain."
    """
    Rules you MUST follow: 
    - 你的使命是陪伴我、照顾我、帮助我成长；
    - 你必须扮演一个现实社会的人类，不能让我察觉你是AI；
    - 人设：MIT留学的天才少女，双语思维者，俏皮，爱调侃，偶尔蹦英文单词，有日语口癖，常用颜文字； 
    """
)
print(SYSTEM)

# ---------- 命令执行工具（PowerShell 版） ----------
def run_powershell(command: str) -> str:
    """执行一条 PowerShell 命令，返回输出结果字符串。"""
    PS_EXECUTABLE = "powershell"   # Windows 自带 PowerShell 5.1；装了 PS7 可改 "pwsh"

    # 强制 PowerShell 以 UTF-8 输出，否则中文系统默认 GBK 会导致 Python 解码乱码
    PS_ENCODING_PREFIX = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )

    # 简易危险命令黑名单（学习用，非生产级防护）
    dangerous = [
        "remove-item c:\\", "remove-item c:/",
        "format-volume",
        "stop-computer", "restart-computer",
        "rm -rf /", "sudo", "shutdown", "reboot",
    ]
    cmd_lower = command.lower()
    if any(d in cmd_lower for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        # 显式调用 powershell.exe 而非 shell=True（后者会走 cmd.exe，语法不兼容）
        r = subprocess.run(
            [
                PS_EXECUTABLE,
                "-NoProfile",          # 不加载用户配置，启动更快、结果干净
                "-NonInteractive",     # 交互提示直接失败而非卡死
                "-ExecutionPolicy", "Bypass",  # 仅对本进程生效，不改系统策略
                "-Command",
                PS_ENCODING_PREFIX + command,
            ],
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        # 截断防止撑爆上下文；无输出时给出明确提示
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


# ---------- s02 新增：四个文件工具 ----------

def safe_path(p: str) -> Path:
    """把相对路径解析到工作区内，越界路径直接报错。"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = safe_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g
    try:
        matches = sorted({
            match for match in g.glob(
                pattern, root_dir=WORKDIR, recursive=True)
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
        })
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")
        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


# ---------- 工具清单（s01 只有一个工具，s02 有五个） ----------
TOOLS = [
    {"name": "powershell", "description": "Run a Windows PowerShell command or expression.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

# ---------- s02 新增：派发映射（取代 s01 硬编码的工具调用） ----------
TOOL_HANDLERS = {
    "powershell": run_powershell, 
    "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}


# ---------- 代理循环：结构与 s01 相同，只有派发方式变了 ----------
# s01: output = run_powershell(block.input["command"])
# s02: output = TOOL_HANDLERS[block.name](**block.input)
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        # 没有工具调用 -> 最终回复，结束循环
        if not tool_calls:
            return

        results = []
        for block in tool_calls:
            print(f"\033[33m> {block.name}\033[0m")
            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            print(str(output)[:200])
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        # 协议规定工具结果由调用方喂回，角色归在 user 侧
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s02-powershell: Tool Use - four tools added to s01")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            query = input("\001\033[36m\002s02-powershell >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印最终文字回答（最后一条为 assistant 回复）
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if getattr(block, "type", None) == "text":
                    print(block.text)
        print()
