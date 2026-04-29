**语言:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ bc60222 -->

> 本翻译由 AI 协助生成。欢迎母语开发者通过 PR 修正用词。

<div align="center">

# 🧠 supamem

**面向 AI 编程代理的 Qdrant 双向记忆层**

*让 Claude Code、Cursor、OpenCode 在每个项目中拥有持久化的语义 + 结构化记忆。*

[![PyPI](https://img.shields.io/pypi/v/supamem?style=flat-square&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

---

> ### 👋 由 [**Dzmitry Sukhau**](https://www.linkedin.com/in/dzmitrys/) 打造 — AI 原生解决方案 / 软件架构师 / CTO
>
> 提供 **AI 产品咨询**、**将 AI 集成到现有产品**、以及 **业务流程自动化** 服务。
>
> 如果你正在交付 LLM 功能、评估检索管线、加固代理系统、或从零构建 AI 优先的产品 — 欢迎联系。
>
> [![LinkedIn — Dzmitry Sukhau](https://img.shields.io/badge/LinkedIn-Dzmitry%20Sukhau-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)
> &nbsp;&nbsp;
> [![Open to Consulting](https://img.shields.io/badge/Open%20to-Consulting%20%26%20Architecture-22C55E?style=for-the-badge&logo=anthropic&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)

---

## ✨ supamem 是什么?

`supamem` 是一个单二进制 CLI,为任何 AI 编程助手提供**生产级记忆层**。把它放进新仓库,
执行 `supamem init`,你的代理立即获得:

- 🔍 **语义搜索**:覆盖项目笔记、ADR、决策记录和过往对话(稀疏 + 密集混合检索)
- 🤖 **MCP 服务器**:任何兼容客户端(Claude Code、Cursor、OpenCode)都可以接入
- 🪝 **每客户端钩子**:在会话开始和文件编辑时自动加载相关记忆
- 📊 **Welford 使用统计**:看到记忆实际被召回的情况
- 🧪 **评测套件**:33 条标注 golden 查询,自动检测召回回归

在 [SoftChat](https://app.softchat.ru) 内部经过实战验证(80.1–80.5 阶段),
然后被抽离为独立包,让每个团队都能采用。

---

## 🎯 为什么需要 supamem?

**问题:** 编程代理在不同会话之间没有任何记忆。每次在 Claude Code / Cursor / OpenCode 打开新对话,
模型都对你的代码库、过往决策、ADR、已知问题或约定一无所知。所以你只能:

1. 在每次会话开始时**重新粘贴 5–15 KB 上下文**(慢、易错、费 token),或
2. 让代理**胡乱摸索** — 它会到处 grep、问重复的问题、忘记上周的决策、把你六个月前
   写好的踩坑记录又踩一遍。

**解决方案:** 一个持久的语义 + 结构记忆层,根据*当前*提示自动检索*正确*的 1–2 KB 上下文 —
不需要手动粘贴、不需要重复解释、不会爆掉上下文。

> **80.1 阶段评测(33 条标注 golden 查询,真实 Claude Code 会话):**
> **相对朴素整文档检索 token 减少 78.5%**,召回率持平,**端到端 p95 = 73 ms**。
>
> 完整评测就是我们在 SoftChat 内部用来锁定生产管线的同一套。
> 方法学:33 条代表性开发查询 → 比较 4 种检索方案(baseline_union、tuned_current、
> tuned_hybrid、mem0_vector) → 每方案测量 token 数、召回置信区间、延迟。

### 📊 Token 消耗对比:有记忆 vs 无记忆

下面的数字基于**典型 30 轮 Claude Code 会话**,假设代码库带有约 50 个 ADR / insights / rules
(大致是 SoftChat 的规模)。具体数值因项目而异 — 但*比例*是稳定的。

| 方案 | 每轮 token | 30 轮会话 token | 备注 |
|------|------:|------:|------|
| ❌ 无记忆层 | 自动注入 **≈ 0**,但你手动粘贴上下文 | **30,000–80,000**(重复手动粘贴) | 你把脑力花在复制上,而不是搭建上 |
| ⚠️ 朴素 RAG(整文档嵌入) | 每轮 ~5,800 | **~174,000** | 臃肿,只需要一段时却把整个文件召回 |
| ✅ **supamem `tuned_hybrid`** | **每轮 ~1,250** | **~37,500** | 召回率持平,token **−78.5% vs 朴素 RAG** |

### 💰 大致推理成本节省

Anthropic API 标价(2026 年 3 月):
**Sonnet 4.6 = $3 / Mtok 输入** · **Opus 4.7 = $15 / Mtok 输入**。

| 模型 | 每会话节省 token vs 朴素 RAG | 每会话节省成本 | 月度(110 会话) |
|---|---:|---:|---:|
| Sonnet 4.6 | **136,500** | **$0.41** | **~$45/开发者** |
| Opus 4.7 | **136,500** | **$2.05** | **~$225/开发者** |

10 人工程师团队若运行 Opus,**仅输入 token 一项每月就节省 ~$2,250** —
这还没计入更慢迭代、丢失决策、重复粘贴上下文的隐性成本。
输出 token 节省(更少幻觉、更少来回轮次)在此基础上叠加。

### 🥊 与备选方案对比

| | 无记忆 | 朴素 RAG | mem0 / 原子事实 | **supamem (tuned_hybrid)** |
|---|:---:|:---:|:---:|:---:|
| 会话开始自动注入 | ❌ | ⚠️ | ✅ | ✅ |
| 稀疏 + 密集混合检索 | ❌ | ❌ | ❌ | ✅ |
| 保留代码标识符 | ❌ | ✅ | ❌(丢失符号名) | ✅ |
| 锁定的 schema + golden 评测 | ❌ | ❌ | ❌ | ✅ |
| 多客户端(Claude/Cursor/OpenCode) | ❌ | ❌ | ⚠️ | ✅ |
| p95 延迟 | n/a | ~120 ms | ~80 ms | **73 ms** |
| Token 膨胀 | 高(手动) | 最高 | 低但有损 | **最低且全召回** |

**为什么用混合?** BM25 抓住*精确标识符*(`ChatService.generate`、环境变量名、
文件路径)— 这些会被密集嵌入糊掉。密集向量抓住*语义意图*("我们怎么处理计费 webhook?")—
这些 BM25 抓不到。RRF 融合把两种排序结合起来,各取所长。

**为什么不用 mem0?** mem0 的原子事实抽取会丢失代码标识符 — 在 33 条 golden 评测上
召回率仅 **0.015**(几乎为零)。它适合个人 CRM 式记忆,不适合代码感知检索。

---

## ⚡️ 60 秒上手

```bash
# 1. 安装(uv 是最快路径)
uv tool install supamem

# 2. 启动 Qdrant(一次性,~30 秒)
docker run -d -p 6333:6333 -p 6334:6334 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest

# 3. 在你的项目中初始化
cd your-project
supamem init

# 4. 接入你的 AI 客户端
supamem install --client claude-code   # 或 cursor / opencode

# 5. 检查健康状态
supamem doctor
```

完成。在项目里打开 Claude Code(或你常用的客户端) — 记忆工具已经在工具菜单里了。✨

---

## 🚀 功能

| 功能 | 说明 |
|------|------|
| 🔍 **混合检索** | 调优后的稀疏(BM25) + 密集(MiniLM)融合,锁定 schema D-25 |
| 📚 **Markdown chunker** | 按 H 头切分,200-token 目标 / 250-token 软上限(T-1) |
| 🤖 **MCP 服务器** | `stdio`(默认)和 `http` 传输,基于官方 `mcp` SDK |
| 🪝 **多客户端钩子** | Claude Code 会话开始 / OpenCode 会话开始 / Cursor MDC |
| 🧰 **一键安装** | 原子配置写入,自动备份,可回滚 |
| 🩺 **`supamem doctor`** | 探测 Qdrant、解析配置链、检测版本漂移 |
| 📊 **Welford 计数器** | 跟踪每项目的召回率、延迟、查询量 |
| 🧪 **评测套件** | 33 条 golden 查询 + 回归检测 |
| 🔁 **棕地迁移** | 检测已有的 `dev_memory` 并非破坏性迁移 |
| 🎨 **精致 CLI** | 基于 Rich 的进度条、面板、配色 — 始终知道在跑什么 |

---

## 📋 前置条件

实际上只需要两样东西:**Python 3.12+** 和 **Qdrant**。其他都是可选的。

<details>
<summary><b>🐍 Python 3.12+ &nbsp;·&nbsp; 点击展开安装命令</b></summary>

```bash
# macOS (Homebrew)
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt install python3.12 python3.12-venv

# Windows (PowerShell)
winget install Python.Python.3.12
```

强烈推荐安装 [`uv`](https://docs.astral.sh/uv/) — Python 包管理器中最快的:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

</details>

<details>
<summary><b>🗄️ Qdrant 1.10+ &nbsp;·&nbsp; 向量数据库(必需)</b></summary>

最简单的方式是 Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $HOME/.qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

或使用 `docker compose`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./qdrant_data:/qdrant/storage"]
    restart: unless-stopped
```

没有 Docker?可以使用 [Qdrant Cloud](https://cloud.qdrant.io/) 托管集群(有免费档),
然后在 `supamem init` 时把 URL 指过去。

</details>

<details>
<summary><b>🤖 兼容 MCP 的客户端 &nbsp;·&nbsp; 至少选一个</b></summary>

| 客户端 | 安装 | 说明 |
|------|------|------|
| [Claude Code](https://claude.com/claude-code) | `npm install -g @anthropic-ai/claude-code` | 一流 MCP 支持 |
| [Cursor](https://cursor.com/) | 从 cursor.com 下载 | 使用 MDC 规则 + MCP |
| [OpenCode](https://opencode.ai/) | `curl -fsSL https://opencode.ai/install \| bash` | 开源 TUI,原生支持 MCP |

</details>

---

## 📦 安装

```bash
# 推荐:uv(最快、隔离)
uv tool install supamem

# 备选:pipx(也隔离)
pipx install supamem

# 普通 pip(在 venv 中)
pip install supamem
```

验证:

```bash
supamem --version
```

你会看到一个彩色的横幅和制作人员行。🎨

> **最新:** `v0.1.3` 已发布到 [PyPI](https://pypi.org/project/supamem/)。通过 Trusted
> Publisher OIDC 发布 — 每个 wheel 都附带来源证明。

---

## 🎯 CLI 一览

| 命令 | 用途 |
|------|------|
| `supamem init` | 绿地初始化 — 探测 Qdrant、创建集合、写 `.supamem/config.toml` |
| `supamem install --client <name>` | 给客户端打配置补丁(`claude-code`、`cursor`、`opencode`)— 原子带备份 |
| `supamem index` | 用锁定的 tuned-hybrid 管线把开发记忆嵌入 Qdrant(D-25) |
| `supamem mcp-server` | 运行 MCP 服务器(`--transport stdio` 默认;`http` 走 HTTP) |
| `supamem hook <client>` | 每客户端会话/编辑钩子(由客户端自动调用) |
| `supamem doctor` | 🩺 探测 Qdrant、打印解析后的配置链、报告版本漂移 |
| `supamem stats` | 来自 `.supamem/state/` 的 Welford schema-v2 使用计数 |
| `supamem migrate` | 从已有 `dev_memory` 集合的棕地迁移 |
| `supamem eval` | 对内置 33 条 golden 查询跑回归测试 |
| `supamem uninstall --client <name>` | 干净反向 `supamem install` |

每个长时间命令都有**实时进度条**显示已用时间,所以你始终知道它在工作。
任意子命令上加 `--help` 可看详细说明。

---

## 🪛 接入你的客户端

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code
```

在 `~/.claude.json` 的 `mcpServers` 下添加条目,并在 `~/.claude/hooks/` 注册会话开始钩子。
加 `--dry-run` 可以预览不写入。

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor
```

打补丁到 `.cursor/mcp.json`,并写 `.cursor/rules/dual-memory.mdc`。

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

更新 `~/.config/opencode/opencode.json`,并在 `~/.config/opencode/hooks/` 写会话开始钩子。

</details>

---

## 🧠 工作原理

```text
┌─────────────────┐    MCP/stdio     ┌─────────────────┐    REST    ┌─────────────┐
│ Claude / Cursor │ ───────────────► │  supamem MCP    │ ─────────► │   Qdrant    │
│   / OpenCode    │ ◄─────────────── │     server      │ ◄───────── │  (vectors)  │
└─────────────────┘                  └─────────────────┘            └─────────────┘
        │                                    ▲
        │ 会话开始钩子                          │ tuned-hybrid 检索
        ▼                                    │ (BM25 + MiniLM 融合)
┌─────────────────┐                          │
│ supamem hook    │ ─────────────────────────┘
│  (自动召回)      │
└─────────────────┘
```

- **索引器**按 H 头切分 Markdown(T-1 chunker,目标 200 token / 软上限 250)
- **嵌入器**生成稀疏(BM25)和密集(MiniLM-L6)向量
- **检索**两路并行,用 reciprocal rank fusion 融合,返回 top-k
- **MCP 服务器**暴露 `dual_memory_search`(读)和 `dual_memory_write`(写/幂等代理记忆持久化) —
  外加 `qdrant_find` 和 `qdrant_store` 作为来自上游 `mcp-server-qdrant` 用户的直接别名
  (用 `SUPAMEM_QDRANT_ALIASES=0` 关闭)
- **钩子**在合适的时刻调用 `supamem hook <client>`,记忆透明加载

---

## 🤝 贡献

欢迎 PR!快速上手:

```bash
git clone https://github.com/dzmitrys-dev/supamem.git
cd supamem
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

从树内 `dev_memory` 配置迁移?见 [MIGRATION.md](MIGRATION.md)。

---

## 📜 许可证

MIT — 见 [LICENSE](LICENSE)。

---

<div align="center">

### 💜 用心交付:

<a href="https://app.softchat.ru"><b>SoftChat</b></a> &nbsp;·&nbsp; <a href="https://softskillz.ai"><b>SoftSkillz</b></a>

*俄语 AI 聊天平台 &nbsp;·&nbsp; AI 优先产品工程*

`supamem` 从 SoftChat 的生产记忆栈中抽离,让每个团队都能跑在同一条经过实战检验的管线上。
如果它让你的代理变聪明了,给我们点个 ⭐ — 也欢迎看看我们用它构建的产品。

<sub>用心制作于白俄罗斯 &nbsp;🇧🇾&nbsp; · &nbsp;<a href="https://app.softchat.ru">app.softchat.ru</a> &nbsp;·&nbsp; <a href="https://softskillz.ai">softskillz.ai</a></sub>

</div>
