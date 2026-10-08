#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================================
s01_带读版_PowerShell.py —— 《Agent Loop（代理循环）》Windows PowerShell 版
=========================================================================

【这个程序是什么？】
一个最简单的"AI 命令行助理"（Windows 专版）：
  你用中文提任务 → AI 决定要执行什么 PowerShell 命令 → 本程序替它执行
  → 把命令输出发回给 AI → AI 看完继续决定，直到它给出最终文字回答。

【与 bash 版（s01.py / s01_带读版.py）的区别】
  1. 工具从 bash 换成了 powershell；
  2. 系统提示词强制模型使用 PowerShell 语法（Get-Command、Get-ChildItem…），
     避免再出现 which / ls / head 这类在 Windows 上报错的命令；
  3. 不再使用 shell=True（那会走 cmd.exe），而是显式调用 powershell.exe；
  4. 命令执行前统一设置 UTF-8 输出编码，保证中文输出不乱码。

【运行环境】
  - Windows + Windows PowerShell 5.1 及以上（Win10/Win11 自带，无需另装）。
  - 仅限 Windows：其他系统上可能找不到 powershell 可执行程序。

【运行前准备】
  1. 安装依赖（虚拟环境里已装可跳过）：
         pip install anthropic python-dotenv
  2. d:\\LLM_AppDev\\.env 至少配置三行（与 bash 版完全相同，例如用 Agnes）：
         ANTHROPIC_BASE_URL = "https://apihub.agnes-ai.com/v1"
         ANTHROPIC_API_KEY  = "你的API钥匙"
         MODEL_ID           = "agnes-2.5-flash"
  3. 在本目录运行：
         python s01_带读版_PowerShell.py
  4. 看到 s01-powershell >> 提示符后输入任务，回车发送；输入 q 回车退出。

【安全提醒】
  危险命令拦截只是学习用的"简易黑名单"，远达不到生产级安全，
  请勿在重要目录或生产环境使用。

【阅读建议】
  注释里带【第X步】的标题就是程序运行的真实顺序，跟着数字读一遍即可。
=========================================================================
"""

# ===================== 【第 0 步】借用现成的"工具箱" =====================
# Python 把常用功能做成了"库"，import 就是把库借来用。

import os          # os = 操作系统接口：用来读环境变量、获取当前文件夹路径
import subprocess  # subprocess = 子进程：用来启动 PowerShell 并执行命令

# ---------- readline：仅用于让输入框更好用，不是核心逻辑 ----------
try:
    # readline 能让你在输入时用左右箭头移动、上下箭头翻历史命令。
    # Mac/Linux 自带；Windows 原生 Python 没有这个库，会抛 ImportError。
    import readline
    # 下面四行是给 Mac 用户修复"中文 + 退格键"显示错乱的老问题，照抄即可。
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    # Windows 走到这里：没有 readline 也无所谓，直接 pass（什么都不做）。
    pass

# Anthropic：官方提供的"打电话给 AI"的工具包（SDK）。
# 只要对方服务兼容 Anthropic 协议（Agnes 就兼容），这个包就能直接用。
from anthropic import Anthropic
# dotenv：负责把 .env 文件里写的配置读进来，变成"环境变量"。
from dotenv import load_dotenv

# ===================== 【第 1 步】读取配置，接通 AI =====================

# 找到项目里的 .env 文件，把里面的 KEY=VALUE 全部加载为环境变量。
# override=True 表示：即使系统里已有同名变量，也以 .env 文件里的为准。
load_dotenv(override=True)

# 小细节：如果 .env 里配置了自定义服务地址（如 Agnes），
# 就移除 ANTHROPIC_AUTH_TOKEN，防止 SDK 误用另一种认证方式导致鉴权失败。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 创建 AI 客户端：可以理解为"一部已存好总机号码的电话"。
# 括号里传入自定义服务器地址；不填（None）时 SDK 默认连 Anthropic 官方。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# 从环境变量取模型名称。
# 方括号 [] 取值意味着：.env 里没配 MODEL_ID 就直接报错，提醒你配置没写全。
MODEL = os.environ["MODEL_ID"]

# ===================== 【第 2 步】给 AI 写"岗位说明书" =====================
# system（系统提示词）每轮对话都会带给 AI，它自己看不到也改不了。
# f"..." 是格式化字符串，{os.getcwd()} 会替换成你当前的真实文件夹路径。
SYSTEM = (
    f"You are a coding agent running on Windows at {os.getcwd()}. "
    "You solve tasks by executing Windows PowerShell commands (PowerShell 5.1). "
    "Rules you MUST follow: "
    "(1) Use PowerShell cmdlets only — never use bash/cmd-only commands. "
    "Use Get-Command (NOT 'where': in PowerShell 'where' is an alias for "
    "Where-Object and cannot locate programs), Get-ChildItem instead of ls/dir, "
    "Select-Object -First N instead of head, Select-String instead of grep, "
    "Remove-Item instead of rm, Get-Content instead of cat. "
    "(2) Use Windows paths (backslashes, e.g. C:\\Users); there is no /mnt/c. "
    "(3) Separate statements with ';' and build pipelines with '|'. "
    "Act, don't explain."
)
# 中文翻译：
#   你是运行在 Windows【当前目录】的编码助理，通过 Windows PowerShell（5.1）
#   执行任务。必须遵守：只用 PowerShell 命令；查程序用 Get-Command（千万别用
#   where——它在 PowerShell 里是 Where-Object 的别名）；列目录用 Get-ChildItem；
#   取前 N 行用 Select-Object -First；搜文本用 Select-String；删文件用
#   Remove-Item；读文件用 Get-Content。路径用 Windows 反斜杠风格，不存在
#   /mnt/c。语句之间用分号，管道用竖线。多动手、少解释。

# ===================== 【第 3 步】告诉 AI："你有哪些本事" =================
# 工具清单。这里只开放一个本事：powershell（运行一条 PowerShell 命令）。
# 清单格式由 Anthropic 协议规定，照模板写即可：
TOOLS = [{
    "name": "powershell",                     # 工具名：AI 之后会用这个名字"点单"
    "description": "Run a Windows PowerShell command or expression.",  # 工具说明
    "input_schema": {                         # input_schema = 入参格式说明书
        "type": "object",
        "properties": {                       # properties = 这个工具接受哪些参数
            # 参数名仍叫 command，内容是一条 PowerShell 命令/表达式字符串。
            "command": {"type": "string"}
        },
        "required": ["command"],              # required = 必填参数
    },
}]

# PowerShell 启动参数（统一放这里，方便阅读）：
#   powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <命令>
PS_EXECUTABLE = "powershell"   # Windows 自带的 Windows PowerShell 5.1；若装了 PS7 可改 "pwsh"

# 每条命令前都先拼接这段"前缀"：把 PowerShell 的输出编码强制设为 UTF-8。
# 原因：PowerShell 5.1 在中文系统上默认按 GBK 代码页输出，Python 直接按
# UTF-8 解码会乱码。本方案已在本机实测中文输出正常。
PS_ENCODING_PREFIX = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$OutputEncoding = [System.Text.Encoding]::UTF8; "
)


# ===================== 【第 4 步】定义"替 AI 动手"的函数 =================
# 接收一条 PowerShell 命令字符串，返回执行结果字符串。
def run_powershell(command: str) -> str:
    # 危险命令黑名单（学习版简易防护，非常简陋，生产环境远远不够）。
    # PowerShell 语境的高危操作：删盘、格式化卷、关机/重启等。
    dangerous = [
        "remove-item c:\\", "remove-item c:/",   # 对 C 盘根目录递归删除（写法粗糙仅作示意）
        "format-volume",                          # 格式化磁盘卷
        "stop-computer", "restart-computer",      # 关机 / 重启（PowerShell cmdlet）
        "rm -rf /", "sudo", "shutdown", "reboot",  # 顺手拦住混进来的 bash/cmd 危险词
    ]
    # 用 lower() 做大小写不敏感匹配：PowerShell 命令大小写随意（FORMAT-VOLUME 也算）。
    cmd_lower = command.lower()
    if any(d in cmd_lower for d in dangerous):
        return "Error: Dangerous command blocked"  # 不执行，直接把错误文字还给 AI
    try:
        # 显式以"参数列表"方式启动 powershell.exe。
        # 为什么不用 shell=True？shell=True 在 Windows 上会把命令交给 cmd.exe 解释，
        # 那 AI 写的 PowerShell 语法（cmdlet、管道对象）就又跑错解释器了。
        r = subprocess.run(
            [
                PS_EXECUTABLE,
                "-NoProfile",          # 不加载用户的 PowerShell 配置文件：启动更快、结果干净
                "-NonInteractive",     # 非交互模式：命令若弹出确认/输入提示会直接失败而不是卡死
                "-ExecutionPolicy", "Bypass",
                # Bypass 只对"本次启动的这个 PowerShell 进程"生效，
                # 不修改注册表和系统执行策略，目的是允许内连命令正常执行。
                "-Command",            # 下一个参数就是要执行的命令文本
                PS_ENCODING_PREFIX + command,
            ],
            cwd=os.getcwd(),           # cwd = 在哪个文件夹里执行（当前项目目录）
            capture_output=True,       # 接住正常输出和错误输出，不直接刷屏
            text=True,                 # 输出按文本（字符串）处理
            encoding="utf-8",          # 按 UTF-8 解码 PowerShell 输出（与前缀设置配套）
            errors="replace",          # 万一还有无法解码的字符，用占位符替代而不是崩溃
            timeout=120,               # 最多执行 120 秒，超时强制终止
        )
        # 把"正常输出 + 错误输出"拼在一起，strip() 去掉首尾空白行。
        out = (r.stdout + r.stderr).strip()
        # 有输出就只保留前 50000 个字符（防止刷屏/撑爆上下文）；
        # 没输出就告诉 AI"(no output)"，空字符串容易让 AI 误以为执行失败。
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        # 命令超过 120 秒还没跑完：返回超时提示。
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        # FileNotFoundError：找不到 powershell（比如在非 Windows 系统运行本文件）
        # OSError：其他系统级错误。把错误信息原样返回给 AI，让它决定怎么办。
        return f"Error: {e}"


# ============== 【第 5 步】全片主角：Agent Loop（代理循环） ==============
# 参数 messages 是"聊天记录本"（一个列表），按顺序装着所有对话。
def agent_loop(messages: list):
    while True:  # 无限循环；AI 不再点工具时用 return 退出

        # ---- 5.1 给 AI 打一次电话，把记录本、身份设定、工具清单都发给它 ----
        response = client.messages.create(
            model=MODEL,           # 用哪个模型（.env 里的 MODEL_ID）
            system=SYSTEM,         # 岗位说明书（第 2 步）
            messages=messages,     # 完整聊天记录（多轮上下文全靠它）
            tools=TOOLS,           # 可用工具清单（第 3 步）
            max_tokens=8000,       # 本轮 AI 最多输出 8000 个 token
        )

        # ---- 5.2 把 AI 这一轮的回复原样订进聊天记录本 ----
        # response.content 是"内容块列表"，块类型主要有：
        #   text（说的话）和 tool_use（申请用工具）。
        messages.append({"role": "assistant", "content": response.content})

        # ---- 5.3 检查：AI 这轮有没有申请执行命令？ ----
        # 把所有类型为 tool_use 的块挑出来。
        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        # 一张"命令申请单"都没有 → AI 在说人话给最终答案 → 任务结束。
        if not tool_calls:
            return

        # ---- 5.4 有申请单：逐条执行，收集结果 ----
        results = []  # 空列表，装每条命令的执行结果
        for block in tool_calls:
            # block.input['command'] 就是 AI 想要执行的 PowerShell 命令。
            # 用 ANSI 转义码把命令打印成黄色（\033[33m ... \033[0m），
            # 提示符用 PS>，一眼能看出这是 PowerShell 版。
            print(f"\033[33mPS> {block.input['command']}\033[0m")
            # 调用第 4 步的函数，真正动手执行。
            output = run_powershell(block.input["command"])
            # 屏幕上只预览前 200 个字符避免刷屏（完整结果照样发给 AI）。
            print(output[:200])
            # 按协议拼一张"执行回执"：
            results.append({
                "type": "tool_result",          # 类型：工具执行结果
                "tool_use_id": block.id,        # 回执编号，必须和申请单的 id 一一对应
                "content": output,              # 命令的真实输出内容
            })

        # ---- 5.5 把所有回执作为一条 user 消息订进记录本 ----
        # 协议规定工具结果由"调用方"喂回，角色上归在 user 这一侧。
        # 塞完后回到 while 开头 → 带着执行结果再次询问 AI，循环继续。
        messages.append({"role": "user", "content": results})


# ===================== 【第 6 步】程序大门：人机对话界面 =====================
# 只有"直接运行本文件"时下面的代码才执行；被别的文件 import 时不自动跑。
if __name__ == "__main__":
    # 启动欢迎语（\n 是空一行）。
    print("s01-powershell: Agent Loop (Windows PowerShell edition)")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []  # 一本空的聊天记录本，整个程序运行期间持续累积。

    while True:  # 外层循环：每轮等用户输入一句话
        try:
            # input(...) 显示提示符并等待你打字、回车。
            # \033[36m 是青色、\033[0m 是恢复默认颜色；
            # \001 和 \002 是给 Mac readline 的"零宽度标记"，
            # Windows 上不认识也无大碍，最多提示符略有显示瑕疵。
            query = input("\001\033[36m\002s01-powershell >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            # EOFError：输入流结束（如 Ctrl+Z 回车）；
            # KeyboardInterrupt：按了 Ctrl+C。两种情况都优雅退出。
            break

        # 输入 q、exit 或直接回车，也退出程序。
        if query.strip().lower() in ("q", "exit", ""):
            break

        # 把你的问题作为一条 user 消息订进记录本。
        history.append({"role": "user", "content": query})

        # 进入第 5 步的代理循环：AI 可能在里面来回执行很多条命令，
        # 直到它给出不带工具调用的最终回复才返回。
        agent_loop(history)

        # ---- 打印 AI 的最终文字回答 ----
        # 循环结束后，记录本最后一条一定是 assistant 的回复。
        response_content = history[-1]["content"]
        # content 是列表（结构化内容块）时，逐块找出 text 类型并打印。
        if isinstance(response_content, list):
            for block in response_content:
                # getattr：安全地取 block.type 属性，没有就返回 None。
                if getattr(block, "type", None) == "text":
                    print(block.text)
        # 每轮回答后空一行，和下一轮输入视觉上分开。
        print()
