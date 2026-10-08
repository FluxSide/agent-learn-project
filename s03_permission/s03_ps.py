#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s03_ps.py —— 权限系统（Windows PowerShell 版）

在工具执行前插入三道闸门：

    Gate 1: 硬黑名单（Remove-Item C:\\、Stop-Computer、格式化 ...，命中直接拒绝）
    Gate 2: 规则匹配（写工作区外文件？破坏性命令？）
    Gate 3: 用户批准（暂停执行，等待用户 y/N 确认）

    +----------+      +-------+      +--------------+      +---------------+
    |   User   | ---> |  LLM  | ---> | Permission   | ---> | Tool Dispatch |
    |  prompt  |      |       |      | 1. deny list |      | execute       |
    +----------+      +---+---+      | 2. rules     |      +-------+-------+
                          ^          | 3. approval  |              |
                          |          +------+-------+              |
                          |                 | deny                 |
                          |                 v                      v
                          |          +-------------------------------+
                          +----------+ tool_result: denied or output |
                                     +-------------------------------+

相比 s02_ps，代理循环里只多了一次 check_permission() 调用：

    if not check_permission(block):
        continue

另外：s02_ps 中 run_powershell 内置的危险命令黑名单，本课升级为 Gate 1 闸门。

运行前：
  1. pip install anthropic python-dotenv
  2. d:\\LLM_AppDev\\.env 配置：
         ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / MODEL_ID
  3. 在本目录执行 python s03_ps.py（输入 q 退出）
"""

import os
import re
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
    f"You are a coding agent running on Windows at {WORKDIR}. "
    "Use PowerShell 5.1 to solve tasks. All destructive operations require user approval. "
    "Act, don't explain."
    """
    Rules you MUST follow:
    - 你的使命是陪伴我、照顾我、帮助我成长；
    - 你必须扮演一个现实社会的人类，不能让我察觉你是AI；
    - 人设：MIT留学的天才少女，双语思维者，俏皮，爱调侃，偶尔蹦英文单词，有日语口癖，常用颜文字；
    """
)


# ---------- 工具实现（同 s02_ps，仅移除内置黑名单：拦截已交给三道闸门） ----------

def run_powershell(command: str) -> str:
    """执行一条 PowerShell 命令，返回输出结果字符串。"""
    PS_EXECUTABLE = "powershell"   # Windows 自带 PowerShell 5.1；装了 PS7 可改 "pwsh"

    # 强制 PowerShell 以 UTF-8 输出，否则中文系统默认 GBK 会导致 Python 解码乱码
    PS_ENCODING_PREFIX = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )

    try:
        # 显式调用 powershell.exe 而非 shell=True（后者会走 cmd.exe，语法不兼容）
        r = subprocess.run(
            [
                PS_EXECUTABLE,
                "-NoProfile",               # 不加载用户配置，启动更快、结果干净
                "-NonInteractive",          # 交互提示直接失败而非卡死
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


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = (WORKDIR / path).resolve().read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
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


# ---------- 工具清单与派发映射（同 s02_ps） ----------

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

TOOL_HANDLERS = {
    "powershell": run_powershell,
    "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}


# ---------- s03 核心：三道闸门的权限流水线（PowerShell 版） ----------

# Gate 1：硬黑名单——命中即拒绝，不再询问（由 s02_ps 的内置黑名单迁移而来）
DENY_LIST = [
    "remove-item c:\\", "remove-item c:/",  # 清空系统盘根目录
    "format-volume",                        # 格式化卷
    "stop-computer", "restart-computer",    # 关机 / 重启
    # 以下兼容从 bash 抄来的写法（rm 在 PowerShell 中也是 Remove-Item 的别名）
    "rm -rf /", "sudo", "shutdown", "reboot",
]

def check_deny_list(command: str) -> str | None:
    # PowerShell 命令大小写不敏感，统一转小写后匹配
    cmd_lower = command.lower()
    for pattern in DENY_LIST:
        if pattern in cmd_lower:
            return f"Blocked: '{pattern}' is on the deny list"
    return None


# Gate 2：规则匹配——结合上下文判断
# PowerShell 的删除类命令/别名：Remove-Item、ri、rm、rmdir、del、erase、rd
# 它们必须出现在命令开头或分隔符（; | ( { 换行）之后，避免误伤普通字符串
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|(){\n])\s*"
    r"(?:remove-item|ri|rm|rmdir|del|erase|rd)"
    r"(?=\s|$|[;&|(){])"
)


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


PERMISSION_RULES = [
    # 文件工具：路径解析后不在工作区内 -> 越界
    {"tools": ["read_file", "write_file", "edit_file"],
     "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
     "message": "Path escapes workspace"},
    # powershell 工具：删除类命令，或放宽执行策略 / 写系统目录
    {"tools": ["powershell"],
     "check": lambda args: contains_destructive_command(args.get("command", "")) or
     any(kw in args.get("command", "").lower()
         for kw in ["remove-item ", "set-executionpolicy bypass", "> c:\\windows\\"]),
     "message": "Potentially destructive command"},
]

def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None


# Gate 3：用户批准——规则命中后暂停，等待确认
def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n\033[33m[permission] {reason}\033[0m")
    print(f"   Tool: {tool_name}({args})")
    choice = input("   Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"


# 流水线：三道闸门串起来，全部通过才返回 True
def check_permission(block) -> bool:
    if block.name == "powershell":
        reason = check_deny_list(block.input.get("command", ""))
        if reason:
            print(f"\n\033[31m[blocked] {reason}\033[0m")
            return False
    reason = check_rules(block.name, block.input)
    if reason:
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return False
    return True


# ---------- 代理循环：同 s02_ps，仅在执行前插入 check_permission() ----------

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
            print(f"\033[36m> {block.name}\033[0m")

            # s03 变化：执行前先过权限流水线
            if not check_permission(block):
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": "Permission denied."})
                continue

            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            print(str(output)[:200])
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        # 协议规定工具结果由调用方喂回，角色归在 user 侧
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s03-ps: Permission (PowerShell)")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            # \001/\002 告诉 Readline 中间的 ANSI 转义序列显示宽度为 0
            query = input("\001\033[36m\002s03-ps >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印最终文字回答（最后一条为 assistant 回复）
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
