# README_MY

这份文档专门解释这次把项目从 Anthropic 风格调用切到 ChatGPT Plus / Pro 的 OpenAI Codex OAuth 的改造原理。

当前 `git status` 里与这次迁移直接相关的变更，一共是 8 个：

1. `.env.example`
2. `requirements.txt`
3. `anthropic.py`
4. `agents/anthropic.py`
5. `agents/login_openai_codex.py`
6. `pyproject.toml`
7. `uv.lock`
8. `README_MY.md`

## 总体思路

我没有去逐个重写 `agents/s01` 到 `agents/s12` 这些脚本的主体逻辑，而是做了一层兼容适配。

原来的脚本基本都是这种结构：

```python
from anthropic import Anthropic

client = Anthropic(...)
response = client.messages.create(
    model=MODEL,
    system=SYSTEM,
    messages=messages,
    tools=TOOLS,
    max_tokens=8000,
)
```

这些脚本后面还依赖 Anthropic 风格的响应结构，比如：

- `response.content`
- `response.stop_reason`
- `block.type == "tool_use"`
- `block.text`
- `block.name`
- `block.input`
- `block.id`

如果直接改成 OpenAI 官方 SDK，`agents/` 目录里几乎每个文件都要一起改。

所以这次采用的是更稳的方案：

- 表面上继续让这些脚本 `from anthropic import Anthropic`
- 实际导入的是我新增的本地兼容层
- 本地兼容层内部再把请求翻译成 ChatGPT Codex OAuth 请求
- 最后把返回结果再包装回 Anthropic 风格

也就是说：

`agents/*.py`
-> `from anthropic import Anthropic`
-> 命中本地兼容实现
-> OAuth 登录拿 token
-> 请求 `chatgpt.com/backend-api/codex/responses`
-> 解析返回
-> 包装成原脚本看得懂的 `response`

因此 `agents/` 目录主体代码几乎不用动，就能跑 GPT。

## 这 8 个改动分别做了什么

### 1. `anthropic.py`

这是整个改造的核心。

它的职责有 5 个：

1. 伪装出一个和 Anthropic SDK 兼容的接口
2. 负责 OAuth 登录和 token 获取
3. 把原来的 `messages` / `tools` 转成 Codex 请求格式
4. 调用 `https://chatgpt.com/backend-api/codex/responses`
5. 把 Codex 返回重新包装成 Anthropic 风格的结果

这个文件里最重要的点如下：

- `Anthropic` 类
  作用：让原代码里的 `client = Anthropic(...)` 还能继续成立。

- `_MessagesAPI.create(...)`
  作用：承接原来的 `client.messages.create(...)` 调用。

- `ensure_openai_codex_auth(...)`
  作用：优先读取已有 OAuth token；没有的话再走交互式登录。

- `refresh_openai_codex_auth(...)`
  作用：强制重新走登录流程，解决缓存 token 过期的问题。

- `_convert_messages(...)`
  作用：把原来的 Anthropic 风格消息转换成 Codex 需要的 `instructions` 和 `input`。

- `_convert_tools(...)`
  作用：把原来的 `input_schema` 工具定义转换成 Codex 的函数工具定义。

- `_request_codex_once(...)`
  作用：真正通过 `httpx` 把请求发到 ChatGPT 的 Codex 后端。

- `_consume_sse(...)`
  作用：解析服务端事件流，把文本输出和工具调用拆出来。

- `TextBlock` / `ToolUseBlock` / `MessageResponse`
  作用：把结果包装成原有脚本能直接读取的对象结构。

结论：

这个文件本质上就是一个“Anthropic -> ChatGPT Codex”的翻译器。

### 2. `agents/anthropic.py`

这个文件是一个很小但很关键的转发层。

原因是：

- 当你运行 `uv run python agents/s07_task_system.py` 时
- Python 的模块搜索路径会优先看 `agents/` 目录
- 如果只在项目根目录放一个 `anthropic.py`
- 那么有些直接从 `agents/` 里启动的脚本，可能会先导入到虚拟环境里真正的 `anthropic` 包

这个文件的作用就是强制把 `agents/*.py` 的 `from anthropic import Anthropic`，转到项目根目录的本地兼容实现上。

所以它解决的是“导入路径稳定性”的问题。

没有它的话：

- 有时候命中本地兼容层
- 有时候命中 site-packages 里的官方 `anthropic`

行为会不稳定。

### 3. `agents/login_openai_codex.py`

这是显式登录入口。

它做两件事：

- `uv run python agents/login_openai_codex.py`
  强制重新走一次 ChatGPT Codex OAuth 登录

- `uv run python agents/login_openai_codex.py --check`
  只检查本地有没有可用 token，不弹浏览器

这个脚本本身不调用模型，它只是提前把认证这一步做掉，方便你调试和排查。

它解决的是“把登录动作从业务脚本里单独抽出来”的问题。

这样你就可以：

1. 先登录
2. 再运行 agent
3. 出现 401 时单独刷新登录

### 4. `pyproject.toml`

这个文件把项目切换成 `uv` 管理。

主要作用：

- 定义项目依赖
- 定义构建后端
- 让 `uv sync` 可以在当前项目里正常工作
- 把 `agents` 包和根目录的 `anthropic.py` 显式纳入构建配置

最关键的是这一步让项目依赖管理从原来的“只有一个 `requirements.txt`”升级成了真正可复现的 `uv` 项目。

### 5. `uv.lock`

这个文件是 `uv` 生成的锁文件。

作用是固定版本，避免不同时间、不同机器装到不一致的依赖版本。

它本身不提供业务逻辑，但它保证：

- `oauth-cli-kit`
- `httpx`
- `python-dotenv`
- 其它依赖

在你当前项目里的版本是可重复的。

也就是说，`uv.lock` 解决的是“环境复现”问题。

### 6. `requirements.txt`

虽然现在已经切到 `uv`，但我还是保留并补充了这个文件。

这里新增了这几个关键依赖：

- `httpx`
- `oauth-cli-kit`

它的作用主要是兼容一些旧的安装习惯，或者给你一个更直观的依赖清单。

严格来说，现在真正的主入口已经是 `pyproject.toml + uv.lock`，但 `requirements.txt` 仍然有文档和兼容价值。

### 7. `.env.example`

这个文件从原来的 Anthropic 配置，改成了 Codex OAuth 配置说明。

变化的核心是：

以前你会配置：

```env
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...
MODEL_ID=claude-...
```

现在变成：

```env
MODEL_ID=gpt-5.1-codex
OPENAI_CODEX_BASE_URL=...
OPENAI_CODEX_ORIGINATOR=...
OPENAI_CODEX_AUTO_LOGIN=1
OPENAI_CODEX_VERIFY_SSL=1
```

这一步解决的是“环境变量语义已经换了，示例配置也必须同步换掉”的问题。

### 8. `README_MY.md`

这个文件就是你现在在看的这份说明。

它解决的是“项目为什么能直接从 Anthropic 风格切到 GPT，以及具体改了哪些文件”的文档问题。

如果没有这个说明，后面你过几天回来再看，很容易只看到“代码能跑”，但不知道原理。

## 真正的调用链是什么

以 `uv run python agents/s07_task_system.py` 为例，实际执行链路如下：

1. Python 启动 `agents/s07_task_system.py`
2. 脚本执行 `from anthropic import Anthropic`
3. 因为当前脚本在 `agents/` 目录内启动，所以先命中 `agents/anthropic.py`
4. `agents/anthropic.py` 再把导入转发到项目根目录的 `anthropic.py`
5. `client = Anthropic(...)` 实际实例化的是我们自己的兼容类
6. 脚本调用 `client.messages.create(...)`
7. 兼容层把原来的 `messages/system/tools` 转成 Codex 请求体
8. 兼容层用 `oauth-cli-kit` 获取 ChatGPT Plus / Pro 的 OAuth token
9. 兼容层通过 `httpx` 向 `chatgpt.com/backend-api/codex/responses` 发请求
10. 服务端以 SSE 的形式返回文本增量和函数调用信息
11. 兼容层把 SSE 解析成文本块和工具调用块
12. 兼容层再把这些块包装成原脚本预期的 `response.content` / `response.stop_reason`
13. 原来的 agent loop 继续按旧逻辑运行，完全不知道底层已经不是 Anthropic 了

这就是为什么你几乎没改 `agents/s07_task_system.py` 本身，却能直接用 GPT。

## 为什么原有 agent 代码基本不用改

因为原有代码依赖的不是“Anthropic 这个公司”，而是“Anthropic SDK 的接口形状”。

只要我把下面这些接口形状伪装出来，原脚本就能继续工作：

- `Anthropic(...)`
- `client.messages.create(...)`
- `response.content`
- `response.stop_reason`
- `content` 里的 text block
- `content` 里的 tool_use block

所以这次迁移的关键不是“把所有脚本替换成 OpenAI 代码”，而是“把接口边界伪装成旧样子”。

这就是典型的适配器思路。

## 现在这套方案的优点

- `agents/` 主体教学代码几乎不用改
- 迁移成本低
- 你以后想继续保持这套 Anthropic 风格示例，也不会被破坏
- 登录、请求、响应转换都集中在一处，后续维护点少

## 现在这套方案的限制

- 它不是官方 OpenAI Python SDK 直连，而是 Codex OAuth 兼容层
- 依赖本地 ChatGPT Plus / Pro 的 OAuth 登录状态
- 如果 token 过期，需要重新登录
- 如果后端协议有变化，兼容层可能需要跟着调整

## 你现在应该怎么理解这次迁移

一句话版本：

我没有把 `agents/` 改写成 OpenAI 风格，而是插入了一个“看起来像 Anthropic，实际上调用 ChatGPT Codex”的中间层。

所以你现在运行：

```bash
uv run python agents/s07_task_system.py
```

表面上：

- 代码还在 `from anthropic import Anthropic`

实际上：

- 已经在用 ChatGPT Plus / Pro 的 OAuth token 调 GPT 了

## 常用命令

安装依赖：

```bash
uv sync
```

检查是否已有登录态：

```bash
uv run python agents/login_openai_codex.py --check
```

强制重新登录：

```bash
uv run python agents/login_openai_codex.py
```

运行某个 agent：

```bash
uv run python agents/s07_task_system.py
```
