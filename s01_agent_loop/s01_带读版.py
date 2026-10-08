#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=========================================================================
s01_带读版.py —— 《Agent Loop（代理循环）》逐行中文讲解版
=========================================================================

【这个程序是什么？】
一个最简单的"AI 命令行助理"：
  你用中文提任务 → AI 决定要敲什么命令 → 本程序替它在你电脑上执行
  → 把命令输出发回给 AI → AI 看完继续决定，直到它给出最终文字回答。

【一句话记住核心思想】
  AI 只有"脑子"没有"手"，这个程序就是借给 AI 的那只手。

【运行前准备（Windows）】
  1. 先装好依赖：  pip install anthropic python-dotenv
  2. 在 d:\\LLM_AppDev\\.env 文件里至少要有三行配置（示例用 Agnes）：
         ANTHROPIC_BASE_URL = "https://apihub.agnes-ai.com/v1"
         ANTHROPIC_API_KEY  = "你的API钥匙"
         MODEL_ID           = "agnes-2.5-flash"
  3. 在本目录运行：  python s01_带读版.py
  4. 看到 s01 >> 提示符后输入任务，回车发送；输入 q 回车退出。

【阅读建议】
  注释里凡是带【第X步】的标题，就是程序运行的真实顺序，
  跟着数字读一遍，就能看懂整个文件。
=========================================================================
"""

# ===================== 【第 0 步】借用现成的"工具箱" =====================
# Python 把常用功能做成了"库"，import 就是把库借来用。

import os          # os = 操作系统接口：用来读环境变量、获取当前文件夹路径
import subprocess  # subprocess = 子进程：用来在你的电脑上真正执行一条命令行命令

# ---------- readline：仅用于让输入框更好用，不是核心逻辑 ----------
try:
    # readline 能让你在输入时用左右箭头移动、上下箭头翻历史命令。
    # 它在 Mac/Linux 上自带；Windows 原生 Python 没有这个库，
    # 执行 import readline 会抛 ImportError。
    import readline
    # 下面四行是给 Mac 用户修复"中文 + 退格键"显示错乱的老问题，照抄即可。
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    # Windows 走到这里：没有 readline 也无所谓，直接 pass（什么都不做）。
    # 这个 try/except 的意义就是：有这个库就用，没有也绝不崩溃。
    pass

# Anthropic：官方提供的"打电话给 AI"的工具包（SDK）。
# 注意：只要对方服务兼容 Anthropic 协议（Agnes 就兼容），这个包也能直接用。
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
# 注意这里用的是方括号 [] 取值：如果 .env 里没配 MODEL_ID，程序会直接报错，
# 这是在提醒你"配置没写全"。
MODEL = os.environ["MODEL_ID"]

# ===================== 【第 2 步】给 AI 写"岗位说明书" =====================
# system（系统提示词）是每轮对话都会带给 AI 的"身份设定"，它自己看不到也改不了。
# f"..." 是格式化字符串，{os.getcwd()} 会被替换成你当前的真实文件夹路径，
# 这样 AI 就知道自己正在哪个目录里干活。
SYSTEM = f"You are a coding agent at {os.getcwd()}. Use bash to solve tasks. Act, don't explain."
# 翻译：你是一名在【当前目录】工作的编码助理，用命令行解决任务，多动手、少解释。

# ===================== 【第 3 步】告诉 AI："你有哪些本事" =================
# TOOLS 是一个工具清单。这里只给 AI 开放了一个本事：bash（运行命令）。
# 清单的格式由 Anthropic 协议规定，照模板写即可：
TOOLS = [{
    "name": "bash",                # 工具名：AI 之后会用这个名字"点单"
    "description": "Run a shell command.",   # 工具说明：告诉 AI 这工具是干嘛的
    "input_schema": {              # input_schema = 入参格式说明书
        "type": "object",
        "properties": {            # properties = 这个工具接受哪些参数
            "command": {"type": "string"}    # command：一条字符串类型的命令
        },
        "required": ["command"],   # required = 必填参数：必须给 command
    },
}]


# ===================== 【第 4 步】定义"替 AI 动手"的函数 =================
# def 用来定义函数。run_bash 接收一条命令字符串，返回执行结果字符串。
def run_bash(command: str) -> str:
    # 危险命令黑名单（学习版的简易防护，非常简陋，生产环境远远不够）。
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    # any(...)：只要命令里包含黑名单中的任意一个词，就拦截。
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"  # 不执行，直接把错误文字还给 AI
    try:
        # subprocess.run：真正在你电脑上执行命令的地方。
        r = subprocess.run(
            command,               # 要执行的命令，例如 "dir" 或 "ls"
            shell=True,            # shell=True = 交给系统外壳解释执行
                                   # （Windows 上是 cmd.exe，Mac/Linux 是 bash）
            cwd=os.getcwd(),       # cwd = 在哪个文件夹里执行（当前项目目录）
            capture_output=True,   # 把命令的正常输出和报错都"接住"，而不是直接刷屏
            text=True,             # 输出按文本（字符串）处理，而不是字节
            errors="replace",      # 遇到乱码字符时用符号替代，避免程序崩溃
            timeout=120,           # 最多执行 120 秒，超时强制终止
        )
        # 把"正常输出 + 错误输出"拼在一起，strip() 去掉首尾空白行。
        out = (r.stdout + r.stderr).strip()
        # 有输出就只保留前 50000 个字符（防止刷屏/撑爆上下文）；
        # 没输出就告诉 AI"(no output)"，因为空字符串可能让 AI 误以为执行失败。
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        # 命令超过 120 秒还没跑完：返回超时提示。
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        # FileNotFoundError：命令/程序不存在（比如 Windows 上敲 ls）
        # OSError：其他系统级错误。把错误信息原样返回给 AI，让它自己想办法换命令。
        return f"Error: {e}"


# ============== 【第 5 步】全片主角：Agent Loop（代理循环） ==============
# 参数 messages 是"聊天记录本"（一个列表），里面按顺序装着所有对话。
def agent_loop(messages: list):
    while True:  # 无限循环；循环内部在"AI 不再点工具"时用 return 退出

        # ---- 5.1 给 AI 打一次电话，把记录本、身份设定、工具清单都发给它 ----
        response = client.messages.create(
            model=MODEL,           # 用哪个模型（.env 里的 MODEL_ID）
            system=SYSTEM,         # 岗位说明书（第 2 步）
            messages=messages,     # 完整聊天记录（多轮上下文全靠它）
            tools=TOOLS,           # 可用工具清单（第 3 步）
            max_tokens=8000,       # 本轮 AI 最多输出 8000 个 token
        )

        # ---- 5.2 把 AI 这一轮的回复原样订进聊天记录本 ----
        # AI 的回复 response.content 是一个"内容块列表"，
        # 里面可能有两种块：text（说的话）和 tool_use（申请用工具）。
        messages.append({"role": "assistant", "content": response.content})

        # ---- 5.3 检查：AI 这轮有没有申请执行命令？ ----
        # 用列表推导式，把所有类型为 tool_use 的块挑出来。
        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        # 一张"命令申请单"都没有 → 说明 AI 在说人话给最终答案了 → 任务结束。
        if not tool_calls:
            return

        # ---- 5.4 有申请单：逐条执行，收集结果 ----
        results = []  # 准备一个空列表，装每条命令的执行结果
        for block in tool_calls:
            # block.input['command'] 就是 AI 想要执行的那条命令。
            # 下面用 ANSI 转义码把命令打印成黄色（\033[33m ... \033[0m）。
            print(f"\033[33m$ {block.input['command']}\033[0m")
            # 调用第 4 步的函数，真正动手执行。
            output = run_bash(block.input["command"])
            # 屏幕上只预览前 200 个字符，避免太长刷屏（完整结果照样发给 AI）。
            print(output[:200])
            # 按协议拼一张"执行回执"：
            results.append({
                "type": "tool_result",          # 类型：工具执行结果
                "tool_use_id": block.id,        # 回执编号，必须和申请单的 id 一一对应
                "content": output,              # 命令的真实输出内容
            })

        # ---- 5.5 把所有回执作为一条 user 消息订进记录本 ----
        # 为什么 role 是 "user"？协议规定：工具结果由"调用方"喂回，
        # 在消息角色上就归在 user 这一侧。
        # 塞完后回到 while 开头 → 带着执行结果再次询问 AI，循环继续。
        messages.append({"role": "user", "content": results})


# ===================== 【第 6 步】程序大门：人机对话界面 =====================
# 这句的意思是：只有"直接运行本文件"时，下面的代码才执行；
# 如果本文件被别的文件 import，这部分不会自动跑。
if __name__ == "__main__":
    # 启动时先打印两行欢迎语（\n 是空一行）。
    print("s01: Agent Loop")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []  # 准备一本空的聊天记录本，整个程序运行期间持续累积。

    while True:  # 外层循环：每轮等用户输入一句话
        try:
            # input(...) 显示提示符并等待你打字、回车。
            # \033[36m 是青色、\033[0m 是恢复默认颜色；
            # \001 和 \002 是给 Mac readline 的"零宽度标记"，
            # Windows 上不认识也无大碍，最多提示符略有显示瑕疵。
            query = input("\001\033[36m\002s01 >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            # EOFError：输入流结束（如 Ctrl+Z 回车）；
            # KeyboardInterrupt：按了 Ctrl+C。两种情况都优雅退出。
            break

        # 输入 q、exit 或直接回车，也退出程序。
        if query.strip().lower() in ("q", "exit", ""):
            break

        # 把你的问题作为一条 user 消息订进记录本。
        history.append({"role": "user", "content": query})

        # 进入第 5 步的代理循环：AI 可能在这里面来回执行很多条命令，
        # 直到它给出不带工具调用的最终回复才返回。
        agent_loop(history)

        # ---- 打印 AI 的最终文字回答 ----
        # agent_loop 结束后，记录本最后一条一定是 assistant 的回复。
        response_content = history[-1]["content"]
        # content 是列表（结构化内容块）时，逐块找出 text 类型并打印。
        if isinstance(response_content, list):
            for block in response_content:
                # getattr：安全地取 block.type 属性，没有就返回 None。
                if getattr(block, "type", None) == "text":
                    print(block.text)
        # 每轮回答后空一行，视觉上和下一轮输入分开。
        print()
