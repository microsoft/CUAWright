# Webwright

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

<p align="center">
  <img src="assets/webwright_logo.svg" alt="Webwright logo" width="320">
</p>

<p align="center"><b>让你的编程模型成为最先进的浏览器智能体</b></p>

<p align="center">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/playwright-chromium-green" alt="Playwright">
  <img src="https://img.shields.io/badge/backends-OpenAI%20%7C%20Anthropic%20%7C%20OpenRouter-orange" alt="Backends">
  <img src="https://img.shields.io/badge/footprint-%E2%89%A4~1.5k%20LoC-brightgreen" alt="Footprint">
</p>

- 📝 **博客：** [Webwright：Web 智能体只需要一个终端](https://www.microsoft.com/en-us/research/articles/webwright-a-terminal-is-all-you-need-for-web-agents/)
- 🌐 **项目页面：** [microsoft.github.io/Webwright](https://microsoft.github.io/Webwright/)

Webwright 为 LLM 提供一个终端，使其能够启动多个浏览器会话来检查页面并完成 Web 任务。它只在需要时捕获和检查页面截图及状态。它要求每个 Web 任务都在一个可重新运行的 Python 脚本中端到端完成，也就是说，Web 智能体的浏览历史就是一个代码文件。没有多智能体系统，没有图引擎，没有插件层，也没有隐藏的编排——只有终端、浏览器和模型。

已经有了喜欢的智能体，并想知道如何让 Claude Code、Codex、Hermes、OpenClaw 更擅长浏览器任务？可以考虑添加 [Webwright 插件/技能](#-作为插件使用)！

---

## 📰 动态

- **2026-05-11** — 支持 Task2UI 模式：Webwright 完成任务后，会将任务结果渲染为便于查看和复用的 HTML Web 应用。
- **2026-05-06** — 新增 Codex 和 Claude Code 插件清单；可通过 `/plugin install webwright@webwright` 安装。已发布 OpenClaw 和 Hermes Agent 集成；同一个 `skills/webwright/` 文件夹现在可由 Claude Code、Codex、OpenClaw 和 Hermes 加载。
- **2026-05-04** — 首次公开发布：约 1.5k LoC，支持 OpenAI / Anthropic / OpenRouter 后端和 Playwright 环境。

---

<details>
<summary><strong>💡 动机：超越有状态浏览器中的逐步 Web 交互</strong></summary>

如今，大多数 Web 智能体都把浏览器会话本身当作工作空间：模型在每一步接收当前页面状态，然后预测一个操作——点击、输入、DOM 选择器或简短的工具调用。无论采用哪种格式，智能体都被限制在预定义的交互循环中，每次只能预测一个 Web 操作。当 LLM 能力较弱时，这种框架很有用。随着模型更擅长编写和调试代码，同一套框架反而成了瓶颈。

Webwright 采取不同的立场：**将智能体与浏览器分离**，把浏览器视为智能体在开发程序时可以启动、检查和丢弃的对象。持久化的产物不是浏览器会话，而是**本地工作空间中的代码和日志**。

- 🧱 **稳健、可复用的 Web 环境交互** — 编程智能体不依赖脆弱的像素级操作，而是通过终端查询元素、等待条件，并处理延迟加载或重新渲染等动态行为。生成的脚本可以重新运行、调整并在任务之间共享，无需每次从头摸索。
- ⚡ **高效组合复杂工作流** — 选择日期或填写表单等多步交互可以变成紧凑的程序。循环、函数和抽象让智能体能够泛化到相似任务（例如不同日期），无需反复预测相同的底层步骤。交互轮次更少，执行更快，长流程中的错误累积也更少。
- 🧪 **以工作空间为状态，而不是以浏览器为状态** — 智能体可以编写探索脚本、启动全新的浏览器会话，并自行决定何时截图和检查故障，就像人类工程师迭代 RPA 脚本一样。
- 🪄 **极简却出乎意料地有效** — 事实证明，这种精简设计能够很好地处理复杂任务，尤其是长时程 Web 任务（参见[性能](#-性能)）。

</details>

---

<details>
<summary><strong>🌟 为什么选择 Webwright</strong></summary>

大多数 Web 智能体框架把真正的智能体循环埋在多层抽象之下。Webwright 反其道而行：

- 🪶 **轻量设计** — 核心智能体循环位于单个约 450 行的文件中，Playwright 环境约 570 行，CLI 约 150 行。
- 🧩 **可插拔模型后端** — OpenAI、Anthropic 和 OpenRouter 各约 150–200 行。
- 🔍 **没有隐藏框架** — 只使用 `httpx`、`pydantic`、`playwright` 和 `typer`。
- 🔁 **扁平的提示 → 观察 → 执行脚本循环** — 端到端可读，易于调试和复刻。
- 🧪 **运行产物优先** — 每次运行都会把轨迹和截图写入磁盘以便检查。

如果你想要一个最小、易调试的浏览智能体起点，而不是另一个重量级平台，Webwright 正适合你。

</details>

---

<details>
<summary><strong>🆚 Webwright 与其他浏览器智能体仓库有何不同</strong></summary>

它们在架构层面的差异如下：

|                     | **Stagehand (Browserbase)**                                  | **agent-browser (Vercel)**                                                | **browser-use**                                       | **Webwright**                                                       |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------- |
| **范式**        | 混合：代码 + 自然语言原语（`act` / `extract` / `agent`）   | 由*另一个*智能体（Claude Code、Codex 等）调用的 CLI 工具            | 基于 DOM/AX 快照的自主 LLM 智能体循环       | **拥有终端的编程智能体**；浏览器只是它启动的一个环境 |
| **操作空间**    | Playwright 代码，或自然语言 → 经 LLM 转换的 Playwright           | 离散子命令（`open`、`click @e2`、`snapshot`、`eval`）            | 由 LLM 选择的索引化点击/输入操作        | **自由形式 Python（自行编写 Playwright 脚本）**                       |
| **什么是“状态”？**| 浏览器会话                                          | 浏览器会话（由守护进程在 CLI 调用之间保持）                     | 浏览器会话                                   | **本地工作空间——代码、截图、日志。** 浏览器可随时丢弃。 |
| **循环形式**      | 命令式；`agent()` 在需要时执行多步操作            | 每个微步骤调用一次 CLI                                         | 观察 → 预测下一操作 → 执行 → 重复      | 编写代码 → 执行 → 检查截图 → 修复（代码即操作）      |
</details>


---

## 🎥 演示
https://github.com/user-attachments/assets/4ed94cd5-11be-4daa-b2d7-1260a803baca

---

## 📊 性能

在两个真实网站基准测试中，以 100 步预算取得最先进的结果——完整详情请参阅[博客文章](https://www.microsoft.com/en-us/research/articles/webwright-a-terminal-is-all-you-need-for-web-agents/)。

- 🏆 **Online-Mind2Web（300 个任务）：** 使用 GPT-5.4 达到 **86.7%**——在 AutoEval 类别的开源框架中最高。Claude Opus 4.7 达到 **84.7%**，并在困难集上更强（N=100 时为 **80.5%**，GPT-5.4 为 76.6%）。
- 🚀 **Odysseys（200 个长时程任务）：** 使用 GPT-5.4 达到 **60.1%**（平均 76.1 步）——比此前的 SOTA（Opus 4.6 为 44.5%，采用基于视觉的方法和持久化浏览器）高 **15.6 个百分点**，比基础 GPT-5.4（使用 xy 坐标预测和持久化浏览器时为 33.5%）高 **26.6 个百分点**。
- 🧠 **代码即操作优于坐标预测：** Webwright 在所有难度划分上都显著优于复现的 GPT-5.4 截图 + xy 坐标基线。
- 🧰 **小模型 + 可复用工具：** 生成的脚本可以打包成参数化 CLI 工具——即使有 5 个以上工具可用，**Qwen-3.5-9B** 也能很好地完成 Online-Mind2Web 网站上的任务。

<p align="center">
  <img src="assets/odysseys_eval_step100.png" alt="Odysseys long-horizon eval @ 100 steps" width="49%">
  <img src="assets/om2w_autoeval_step100.png" alt="Online-Mind2Web AutoEval @ 100 steps" width="49%">
</p>

---

## 🗺️ 项目结构

```
webwright/
├── pyproject.toml           # package: webwright
├── src/webwright/
│   ├── run/cli.py           # CLI entrypoint (`webwright`)
│   ├── agents/default.py    # core agent loop
│   ├── environments/        # Playwright browser workspace
│   ├── tools/               # image_qa, self_reflection
│   ├── models/              # openai_model, anthropic_model, base
│   ├── config/              # base.yaml, model_openai.yaml, model_claude.yaml
│   └── utils/
├── assets/
│   └── task_showcase/       # tiny Flask dashboard for repeatable runs
│       ├── app.py
│       ├── templates/       # dashboard.html, task.html
│       └── tasks/<short_id>/ # task.json + report.json per task
├── tests/
└── outputs/                 # run artifacts (trajectories, screenshots)
```

---

## 📰 任务展示（以仪表板形式呈现的可重复运行）

[`assets/task_showcase/`](assets/task_showcase/README.md) 下的小型 Flask 应用将**可重复**的 Odyssey 任务（优惠、库存、列表、招聘网站、天气等）的 Webwright 运行结果整合到一个仪表板中。每个任务仅包含两个文件——`task.json`（元数据）和 `report.json`（经过整理的结构化输出：来源 + 表格、列表、摘要等结果区块）——模板会以通用方式渲染它们，因此添加新任务只需在 `assets/task_showcase/tasks/` 中放入一个新文件夹。

```bash
pip install flask
python assets/task_showcase/app.py    # http://127.0.0.1:5005
```

若要让 Webwright 在运行时生成可直接渲染的任务文件夹，请叠加 Task Showcase 配置层：

```bash
python -m webwright.run.cli \
    -c base.yaml -c model_openai.yaml -c task_showcase.yaml \
    -t "<repeatable web task>" \
    --task-id my_repeatable_task \
    -o outputs/default
```

> **注意：** 只有包含 `-c task_showcase.yaml` 时才会生成 `report.json`。仅使用 `base.yaml` 的运行会生成 `trajectory.json` 和调试产物，但不会生成 `report.json`。

运行会在输出工作空间中写入 `task_showcase/tasks/<short_id>/task.json` 和 `report.json`。无需将这些生成文件复制回仓库即可渲染：

```bash
python assets/task_showcase/app.py \
    --tasks-dir outputs/default/<run>/task_showcase/tasks
```

---

## 🚀 快速开始

### 前置条件

- Python 3.10+
- 通过 Playwright 安装的 Chromium
- 所选后端（OpenAI、Anthropic 或 OpenRouter）的 API 密钥

### 安装

```bash
pip install -e .
playwright install chromium
```

### 运行

导出已配置后端的凭据（例如，使用 `model_openai.yaml` 时设置 `OPENAI_API_KEY`，或使用 `model_claude.yaml` 时设置 `ANTHROPIC_API_KEY`）。`image_qa` 和 `self_reflection` 工具默认使用同一个已配置模型，因此 Anthropic 运行不需要 OpenAI 密钥。然后执行：

```bash
python -m webwright.run.cli \
    -c base.yaml -c model_openai.yaml \
    -t "Search for flights from SEA to JFK on 2026-08-15 to 2026-08-20" \
    --start-url https://www.google.com/flights \
    --task-id demo_openai \
    -o outputs/default
```

### 🚩 参数

| 参数 | 说明 |
|------|-------------|
| `-c` | 来自 `src/webwright/config/` 的配置文件（可叠加）。 |
| `-t` | 任务指令。 |
| `--start-url` | 初始页面。 |
| `--task-id` | 输出子文件夹名称。 |
| `-o` | 输出目录。 |

---

## 🔌 作为插件使用

Webwright 同时提供 [Claude Code](https://docs.claude.com/en/docs/claude-code/plugins)（[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)）和 [OpenAI Codex](https://developers.openai.com/codex/plugins)（[`.codex-plugin/plugin.json`](.codex-plugin/plugin.json)）的插件清单，共享技能位于 [`skills/webwright/`](skills/webwright/)，斜杠命令位于 [`skills/webwright/commands/`](skills/webwright/commands/)。宿主智能体会原生驱动 Webwright 循环——除了宿主订阅外，不需要额外的 LLM API 密钥或费用。能够原生读取 PNG 截图的宿主会跳过 `image_qa` / `self_reflection` 工具。

两种方式共用的运行时依赖（安装一次即可）：

```bash
pip install -e .
playwright install chromium
```

<details>
<summary><b>Claude Code</b></summary>

### 安装

在 Claude Code 中通过内置 marketplace 安装：

```text
# 1. Add this repo as a Claude Code plugin marketplace
/plugin marketplace add microsoft/Webwright

# 2. Install the plugin from that marketplace
/plugin install webwright@webwright
```

更喜欢本地检出？让 marketplace 命令指向克隆的仓库：

```text
/plugin marketplace add /absolute/path/to/Webwright
/plugin install webwright@webwright
```

### 使用

安装后请**启动一个新的 Claude Code 会话**——插件会在会话启动时加载，重启前不会出现。

你可以直接用自然语言询问 Claude Code（技能会根据描述自动激活），也可以使用以下斜杠命令之一：

```
/webwright:run search Google Flights for flights from SEA to JFK on 2026-08-15 to 2026-08-20
/webwright:craft search a ticket on Google Flights from LAX to SFO depart June 7 return June 14
```

- `/webwright:run`（或任何普通提示）会针对任务中的具体值生成一次性的 `final_script.py`。
- `/webwright:craft` 会生成可复用的 CLI 工具：`final_script.py` 会变成一个参数化函数，带有 Google 风格的 `Args:` 文档字符串和 `argparse` 包装器，其参数标志默认使用任务中的具体值，便于你稍后使用不同参数重新运行——例如 `python final_script.py --origin JFK --destination LAX --depart-date 2026-07-01`。

在这两种模式下，Claude Code 都会搭建包含 `plan.md` 的工作空间，在 `final_runs/run_<id>/` 下运行带检测逻辑的 Playwright 脚本，并依据保存的截图对每个关键点进行视觉自验证。

</details>

<details>
<summary><b>OpenAI Codex</b></summary>

### 安装

Codex 可以读取 Claude 风格的 marketplace，因此同一个仓库也可作为 Codex 插件 marketplace。使用 Codex CLI：

```bash
# 1. Add this repo as a Codex plugin marketplace
codex plugin marketplace add microsoft/Webwright

# 2. Open the plugin browser and install Webwright
codex
/plugins
```

更喜欢本地检出？

```bash
codex plugin marketplace add /absolute/path/to/Webwright
```

然后重启 Codex，使新的 marketplace 和插件生效。

### 使用

在新的 Codex 任务中，可以直接用自然语言提出请求（技能会根据描述自动激活），也可以通过 `@webwright` 显式调用内置技能：

```
@webwright search Google Flights for flights from SEA to JFK on 2026-08-15 to 2026-08-20
```

Codex 会搭建包含 `plan.md` 的工作空间，在 `final_runs/run_<id>/` 下运行带检测逻辑的 Playwright 脚本，并依据保存的截图对每个关键点进行视觉自验证。

若要在不卸载的情况下关闭插件，请在 `~/.codex/config.toml` 中将其条目设置为 `enabled = false`，然后重启 Codex。

</details>

<details>
<summary><b>🦞 OpenClaw</b></summary>

### 安装

直接从本地检出安装（支持路径、归档、npm spec、git 仓库或 `clawhub:` spec）：

```bash
openclaw plugins install /absolute/path/to/Webwright
openclaw gateway restart   # reload so the plugin and skill are picked up
```

验证：

```bash
openclaw plugins list | grep webwright
openclaw skills  list | grep webwright   # should show "✓ ready"
```

### 使用

现在，任何 OpenClaw 智能体界面（CLI、Telegram 等）都可使用 `webwright` 技能——可以用自然语言请求智能体，也可以通过 [`skills/webwright/commands/`](skills/webwright/commands/) 下提供的斜杠命令调用，例如 `/webwright run <task>`。

卸载：`openclaw plugins uninstall webwright`。

</details>

<details>
<summary><b>Hermes Agent</b></summary>

### 安装

[Hermes Agent](https://github.com/NousResearch/hermes-agent) 是一个[兼容 skills 的客户端](https://agentskills.io)，因此它可以加载同一个 `skills/webwright/` 文件夹作为 Hermes 技能。将其符号链接到 Hermes 用户技能目录：

```bash
mkdir -p ~/.hermes/skills
ln -sfn /absolute/path/to/Webwright/skills/webwright ~/.hermes/skills/webwright
```

无需 Hermes 专用清单；只会加载 `SKILL.md`。

### 使用

启动 Hermes（`hermes`），然后用自然语言让它执行 Web 任务——技能会根据描述自动激活。也可以通过 `/webwright` 显式调用。

请注意，[`skills/webwright/commands/`](skills/webwright/commands/) 下提供的命名子命令（`/webwright:run`、`/webwright:craft`）属于 Claude Code / Codex 约定，在 Hermes 中不起作用；技能本身仍可端到端运行。

</details>

## 📃 轨迹比较与查看器

你可以使用 Webwright 框架及其 Codex / GitHub Copilot 技能变体运行相同任务，并比较不同框架的 token 使用量和轨迹。轨迹查看器支持 Codex、GitHub Copilot 和 Webwright 框架轨迹。

![Trajectory comparison](assets/trajectory-compare.png)

### 使用方法

```bash
cd assets/compare_trajectory/
python3 -m http.server
```

在浏览器中打开网页，上传 Webwright 的 `raw_responses.jsonl`，并附加 `trajectory.json` 即可查看。然后可以在另一侧上传 Codex 或 GitHub Copilot 轨迹。

### 获取 Codex 轨迹：

```
ls ~/.codex/sessions/2026/MONTH/DAY/SESSION_ID.jsonl
```

### 获取 GitHub Copilot 轨迹：

```
/export file session
-> session.md is the uploadable trace
```

### 快速比较

#### “找出最便宜的二手 8 缸宝马，生产年份在 2005–2015 年之间，价格在 25,000–50,000 美元之间，里程不超过 50,000 英里。”

| Token | Webwright 框架（本地浏览器模式） | Codex Webwright 技能 |
| --- | ---: | ---: |
| 输入 | 420,433 | 3,271,143 |
| 输出 | 3,593 | 20,040 |
| 推理 | 0 | 4,410 |
| 缓存 | 217,216 | 3,081,3440 |
| 总计 | 424,026 | 3,291,183 |

各次运行和结果可能有所不同。

---

## 致谢

- [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent/tree/main) — 极简智能体循环的设计灵感。
- [Playwright](https://playwright.dev/) — 浏览器自动化。

## 引用

如果你在研究中使用 Webwright 或在其基础上进行构建，请引用本仓库：

```bibtex
@misc{webwright2026,
  title        = {Webwright: A terminal is all you need for web agents},
  author       = {Lu, Yadong and Xu, Lingrui and Huang, Chao and Awadallah, Ahmed},
  year         = {2026},
  howpublished = {\url{https://github.com/microsoft/Webwright}},
  note         = {GitHub repository}
}
```
