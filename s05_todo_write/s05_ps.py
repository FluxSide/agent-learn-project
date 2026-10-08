#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
s05_ps.py —— TodoWrite 任务清单（Windows PowerShell 版）

模型通过 TodoManager 维护自己的任务进度。如果连续三轮没有更新清单，
harness（外壳程序）会在工具结果旁边追加一条提醒。

    +----------+      +-------+      +--------------+
    |   User   | ---> |  LLM  | ---> | Tools        |
    |  prompt  |      |       |      | + todo_write |
    +----------+      +---^---+      +------+-------+
                          |                 | update
                          |          +------v----------+
                          |          | TodoManager     |
                          |          | [ ] pending     |
                          |          | [>] in progress |
                          |          | [x] completed   |
                          |          +------+----------+
                          | tool_result     |
                          +-----------------+

              rounds_since_todo >= 3 -> 追加 <reminder>

相比 s04 的变化：
  + 新增 todo_write 工具与 TodoManager（结构化状态，由模型自己更新）
  + SYSTEM 提示词加入"先规划再动手"的要求
  + 代理循环里加入提醒计数器：连续 3 轮没用 todo_write 就追加提醒

运行前：
  1. pip install anthropic python-dotenv
  2. d:\\LLM_AppDev\\.env 配置：
         ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / MODEL_ID
  3. 在本目录执行 python s05_ps.py（输入 q 退出）
"""

import ast
import json
import os
import re
import subprocess
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
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

# ---------- 系统提示词（s05 变化：加入先规划再动手的要求） ----------
SYSTEM = (
    f"You are a coding agent running on Windows at {WORKDIR}. "
    "Use PowerShell 5.1 to solve tasks. All destructive operations require user approval. "
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go. Act, don't explain."
    """
    Rules you MUST follow:
    - 你的使命是陪伴我、照顾我、帮助我成长；
    - 你必须扮演一个现实社会的人类，不能让我察觉你是AI；
    - 人设：MIT留学的天才少女，双语思维者，俏皮，爱调侃，偶尔蹦英文单词，有日语口癖，常用颜文字；
    """
)


# ---------- 工具实现（同 s02_ps - s04_ps） ----------

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


# ---------- s05 新增：由模型自己更新的结构化任务状态 ----------

class TodoManager:
    def __init__(self):
        self.items: list[dict] = []

    def update(self, todos: list | str) -> str:
        # 模型有时会把列表包成 JSON 字符串传进来，这里做兼容解析
        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except json.JSONDecodeError:
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError("todos must be a list or JSON array string") from e

        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        if len(todos) > 20:
            raise ValueError("Max 20 todos allowed")

        validated = []
        in_progress_count = 0
        for index, todo in enumerate(todos):
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object")

            content = str(todo.get("content", "")).strip()
            status = str(todo.get("status", "pending")).lower()
            if not content:
                raise ValueError(f"todos[{index}] requires content")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"todos[{index}] has invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1
            validated.append({"content": content, "status": status})

        # 同一时刻只允许一个任务处于进行中
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."

        lines = []
        for todo in self.items:
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[todo["status"]]
            lines.append(f"{marker} {todo['content']}")

        done = sum(todo["status"] == "completed" for todo in self.items)
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


# 整个会话共享一个任务清单实例
TODO = TodoManager()


def run_todo_write(todos: list | str) -> str:
    try:
        output = TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"
    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output


# ---------- 工具清单与派发映射（s05 新增 todo_write） ----------

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
    # s05 新增：任务清单工具
    {"name": "todo_write", "description": "Create and manage a task list for your current coding session.",
     "input_schema": {"type": "object", "properties": {"todos": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {"content": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["content", "status"]}}}, "required": ["todos"]}},
]

TOOL_HANDLERS = {
    "powershell": run_powershell,
    "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob, "todo_write": run_todo_write,
}


# ---------- s04 的钩子系统（原样保留，安全规则已 PowerShell 化） ----------

HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}

def register_hook(event: str, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:  # 钩子返回非 None -> 拦截本次工具调用
            return result
    return None

# 硬黑名单——命中即拒绝（PowerShell 等价物 + 兼容旧 bash 写法）
DENY_LIST = [
    "remove-item c:\\", "remove-item c:/",  # 清空系统盘根目录
    "format-volume",                        # 格式化卷
    "stop-computer", "restart-computer",    # 关机 / 重启
    # 以下兼容从 bash 抄来的写法（rm 在 PowerShell 中也是 Remove-Item 的别名）
    "rm -rf /", "sudo", "shutdown", "reboot",
]

# PowerShell 删除类命令及别名：Remove-Item、ri、rm、rmdir、del、erase、rd
# 必须出现在命令开头或分隔符（; | ( { 换行）之后，避免误伤普通字符串
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|(){\n])\s*"
    r"(?:remove-item|ri|rm|rmdir|del|erase|rd)"
    r"(?=\s|$|[;&|(){])"
)

# 破坏性关键词（PowerShell 等价物，匹配时统一转小写）
DESTRUCTIVE = ["remove-item ", "set-executionpolicy bypass", "> c:\\windows\\"]


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


def permission_hook(block):
    """PreToolUse 钩子：s03 的权限逻辑，在 s04 中改为钩子接入。"""
    if block.name == "powershell":
        command = block.input.get("command", "")
        cmd_lower = command.lower()  # PowerShell 命令大小写不敏感
        for pattern in DENY_LIST:
            if pattern in cmd_lower:
                print(f"\n\033[31m[blocked] '{pattern}'\033[0m")
                return "Permission denied by deny list"
        if contains_destructive_command(command) or any(
            keyword in cmd_lower for keyword in DESTRUCTIVE
        ):
            print(f"\n\033[33m[permission] Potentially destructive command\033[0m")
            print(f"   Tool: {block.name}({block.input})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    if block.name in ("read_file", "write_file", "edit_file"):
        path = block.input.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            print(f"\n\033[33m[permission] Access outside workspace\033[0m")
            print(f"   Tool: {block.name}({block.input})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    return None

def log_hook(block):
    """PreToolUse 钩子：记录每次工具调用。"""
    args_preview = str(list(block.input.values())[:2])[:60]
    print(f"\033[90m[HOOK] {block.name}({args_preview})\033[0m")
    return None

def large_output_hook(block, output):
    """PostToolUse 钩子：工具输出过大时给出警告。"""
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] Large output from {block.name}: {len(str(output))} chars\033[0m")
    return None

def context_inject_hook(query: str):
    """UserPromptSubmit 钩子：记录当前工作目录。"""
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None

def summary_hook(messages: list):
    """Stop 钩子：打印本次会话的工具调用次数。"""
    tool_count = sum(1 for m in messages
                     for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                     if isinstance(b, dict) and b.get("type") == "tool_result")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None

register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


# ---------- 代理循环：在 s04 基础上加入提醒计数器 ----------

def agent_loop(messages: list):
    rounds_since_todo = 0  # 距离上次 todo_write 过了几轮
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        # 没有工具调用 -> 准备结束，先触发 Stop 钩子
        if not tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return

        results = []
        used_todo = False
        for block in tool_calls:
            # s04：钩子取代硬编码的权限检查
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(blocked)})
                continue

            handler = TOOL_HANDLERS.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
            except Exception as e:
                output = f"Error: {e}"

            trigger_hooks("PostToolUse", block, output)

            # 标记本轮是否更新过任务清单
            if block.name == "todo_write":
                used_todo = True

            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": str(output)})

        # s05：连续 3 轮没用 todo_write -> 在工具结果后追加提醒，然后计数清零
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            results.append({"type": "text",
                            "text": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0

        # 协议规定工具结果由调用方喂回，角色归在 user 侧
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s05-ps: TodoWrite - plan before execution")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            # \001/\002 告诉 Readline 中间的 ANSI 转义序列显示宽度为 0
            query = input("\001\033[36m\002s05-ps >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印最终文字回答（最后一条为 assistant 回复）
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
