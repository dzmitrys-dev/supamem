**语言:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ b5a3522 -->

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

## 👀 看看它工作 — `supamem live`

在另一个终端里运行 `supamem live`,实时观察每一次检索调用 — 与 Claude Code / Cursor / OpenCode 并行使用,即可瞬间看清 PreToolUse 钩子的注入活动(它们正是因为不显示 UI 才省下了 token)。

![supamem live dashboard](docs/media/supamem-live.svg)

**会话启动横幅**(v0.1.4+)在 AI 客户端打开会话时也会注入一行状态:`🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>` — 通过环境变量自动识别 Claude Code / Cursor / OpenCode。

> 🎬 **交互式演示:** [`supamem-live.cast`](docs/media/supamem-live.cast) — 拖入 [asciinema.org/player](https://asciinema.org/) 或本地运行 `asciinema play docs/media/supamem-live.cast`。

---

## 🚀 功能

| 功能 | 说明 |
|------|------|
| 🔍 **混合检索** | 调优后的稀疏(BM25) + 密集(MiniLM)融合,锁定 schema D-25 |
| 🎯 **代码感知重排器** | 交叉编码器 `mxbai-rerank-base-v2`(Apache-2.0)默认对 `tuned_hybrid` 候选重新打分。通过 `retrieval.reranker = "off"` 关闭,恢复 v0.2.4a1 之前的行为。(Phase 8, RERANK-01..04) |
| ⏳ **按来源时序有效性** | 每个 chunk 都带 `valid_from`/`valid_to`;重新索引被修改的文件会原子性地把旧 chunk 标记为已失效,检索期过滤器在所有后端中统一排除已失效点。可选的 transcript 专属衰减(默认关闭)。`retention_days = 90` 之后自动 GC(设为 `0` 即永不删除 / 审计模式)。(Phase 9, TEMP-01..03) |
| 📚 **Markdown chunker** | 按 H 头切分,200-token 目标 / 250-token 软上限(T-1) |
| 🤖 **MCP 服务器** | `stdio`(默认)和 `http` 传输,基于官方 `mcp` SDK |
| 🪝 **多客户端钩子** | Claude Code 会话开始 / OpenCode 会话开始 / Cursor MDC |
| 🧰 **一键安装** | 原子配置写入,自动备份,可回滚 |
| 🩺 **`supamem doctor`** | 探测 Qdrant、解析配置链、检测版本漂移 |
| 👀 **`supamem live`** | 基于 Rich Live 的实时仪表盘,跟踪 audit JSONL — 实时检索调用可见(v0.1.4+) |
| 🎬 **会话启动横幅** | 跨客户端单行横幅,在会话打开时注入(Claude Code / Cursor / OpenCode),v0.1.4+ |
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

> **最新:** `v0.1.5` 已发布到 [PyPI](https://pypi.org/project/supamem/)。通过 Trusted
> Publisher OIDC 发布 — 每个 wheel 都附带来源证明。

### 安装时缓存模型

`supamem install <client>` 与 `supamem init` 会通过进度条主动下载所有 ML 前置依赖
(MiniLM ~90 MB、BM25 ~10 MB、mxbai-rerank-base-v2 ~1 GB)。安装后冷启动的 CLI 调用
(`supamem --help`、`supamem doctor`、`supamem --version`)不会触发任何网络请求。
气隙首次启动?加 `--skip-models`,等网络可用后再跑一次 `supamem repair` 补齐。

模型缓存目录:`platformdirs.user_cache_dir("supamem")/models/`
(可用 `SUPAMEM_CACHE_DIR` 覆盖)。

### 子代理可达性(v0.2.5+)

如果你使用 GSD、superpowers、hookify 或其他给 agent 定义钉死 `tools:` 白名单的插件
所提供的 Claude Code 子代理(subagent),那么除非白名单包含 `mcp__supamem__*`,
否则这些子代理无法访问 supamem MCP 服务器 —— 即使父会话已连上 supamem。
子代理只继承其 frontmatter 中列出的工具。

`supamem install` 和 `supamem repair` 会自动为你打这个补丁:

```bash
supamem install --client claude-code   # patches ~/.claude/agents/ + <project>/.claude/agents/
supamem repair                         # re-applies if a plugin overwrites your agents
```

补丁是幂等的(重复运行不会产生改动),保留你的 YAML 风格(CSV 还是 list),
并对软链接的 agent 文件附带告警跳过。`tools:` 行缺失或为空的文件按 Claude Code
语义具有完全继承,会被原样保留。

备份清单位于 `~/.cache/supamem/agent_patches.json`,可以干净反向:

```bash
supamem unpatch-agents
```

在 `install` / `init` / `repair` 任意一个上加 `--skip-patch-agents` 可关闭此功能。

#### 卸载 supamem

```bash
supamem unpatch-agents      # restore agent whitelists first
pip uninstall supamem
```

2026 年 `pip` / `uv` / `pipx` 都没有可移植的卸载钩子,所以这两步是受支持的契约。
`supamem doctor` 会显示清单路径和提醒,所以你能自然地发现这套流程。

---

## 🎯 CLI 一览

| 命令 | 用途 |
|------|------|
| `supamem init` | 绿地初始化 — 探测 Qdrant、创建集合、写 `.supamem/config.toml` |
| `supamem install --client <name>` | 给客户端打配置补丁(`claude-code`、`cursor`、`opencode`)— 原子带备份。v0.2.5+:自动为 `~/.claude/agents/` 与 `<project>/.claude/agents/` 的受限 `tools:` 白名单追加 `mcp__supamem__*`;用 `--skip-patch-agents` 关闭。 |
| `supamem index` | 用锁定的 tuned-hybrid 管线把开发记忆嵌入 Qdrant(D-25) |
| `supamem mcp-server` | 运行 MCP 服务器(`--transport stdio` 默认;`http` 走 HTTP) |
| `supamem hook <client>` | 每客户端会话/编辑钩子(由客户端自动调用) |
| `supamem doctor` | 🩺 探测 Qdrant、打印解析后的配置链、报告版本漂移 |
| `supamem stats` | 来自 `.supamem/state/` 的 Welford schema-v2 使用计数 |
| `supamem live` | 👀 跟踪 audit JSONL 的实时仪表盘 — 管道安全(非 TTY 时输出纯 JSONL);处理日志轮转、终端尺寸变化、Ctrl-C |
| `supamem migrate` | 从已有 `dev_memory` 集合的棕地迁移 |
| `supamem eval` | 跑 bench 套件。`--suite goldens`(默认,内置 33 条 golden 回归语料)或 `--suite longmemeval_s`(首次运行时按需拉取 LongMemEval_S,~3 GB;CI 快路径为 10 题按轴分层子集,完整 ~500 题需 `--full`)。v0.3.0a4+:每题同时跑 scoped 与 unscoped 两条检索路径;发布 gate 仅依据 **scoped** 路径([ADR-0001](docs/adr/0001-scoped-only-bench-gate.md))。新增内置 `--suite longmemeval_scoped_smoke`(≤5 题,无需懒加载)用于 CI。输出 MTEB 风格 JSON 到 `~/.supamem/eval/<utc-iso>.json`。默认裁判为离线启发式;传 `--judge ollama:<model>` 接本机 Ollama —— 拒绝 SaaS 端点(D-07)。可选附加包:`pip install supamem[eval]` 启用 RAGAS 三件套(v0.3.0a2+)。保留旧版 `--regress` 模式。 |
| `supamem uninstall --client <name>` | 干净反向 `supamem install` |
| `supamem unpatch-agents` | 🔄 反向子代理可达性补丁(v0.2.5+)。按 `~/.cache/supamem/agent_patches.json` 清单将 agent 文件还原到打补丁前的形态。已被你修改过的文件会带告警跳过。`pip uninstall supamem` 之前先跑这条以获得干净卸载。 |

每个长时间命令都有**实时进度条**显示已用时间,所以你始终知道它在工作。
任意子命令上加 `--help` 可看详细说明。

---

## 📜 转录(Transcript)摄取(v0.2.2a1+)

supamem 可以把你的 **Claude Code 会话历史**作为问答抽屉式 chunk 索引到项目的 Markdown 语料中,让历史决策与工具调用轨迹出现在 `dual_memory_search` 结果里。默认关闭 —— 通过 `--transcripts` 显式启用。

```bash
# 从默认位置(~/.claude/projects/)索引 Claude Code 转录
supamem index --transcripts

# 或指向具体目录
supamem index --transcripts /path/to/sessions/

# 跳过常规项目语料,仅索引转录
supamem index --transcripts --transcripts-only

# 限定到近期会话(默认 180 天;--since 0 关闭过滤)
supamem index --transcripts --since 30d
```

在 `.supamem/config.toml` 的 `[supamem.transcript]` 下配置:

```toml
[supamem.transcript]
default_root           = "~/.claude/projects/"
since_days             = 180
tool_payload_max_chars = 2000
chunk_soft_max_tokens  = 600
include_paths_glob     = []
exclude_paths_glob     = []   # 排除敏感会话,例如 ["**/banking-*.jsonl"]
```

> ⚠ **转录中可能包含密钥。** API key、token 以及其他凭据偶尔会被粘贴进 Claude Code 会话。v0.2.2a1 **不做任何脱敏** —— 在分享 `~/.cache/supamem` Qdrant collection 之前请先审查内容。通过 `exclude_paths_glob` 手动排除敏感会话。脱敏功能计划在 v0.3 通过未来的 `supamem.redactor` 插件组提供。

当前支持的转录格式:**Claude Code JSONL**(Cursor SQLite 与 ChatGPT 导出延后到后续插件)。

---

## 🔎 范围化检索(v0.2.3a1+)

通过 `dual_memory_search`(以及 `qdrant_find` 别名)上的 `where` 参数,按代码路径分类过滤检索结果:

```python
# 仅检索归类为 backend 的代码块
dual_memory_search(query="auth flow", where={"room": "backend"})

# 跨多个 room 取并集(Qdrant MatchAny)
dual_memory_search(query="rate limit", where={"room": ["backend", "tests"]})
```

每个被索引的 chunk 都会携带 `payload.room`,取值为 `backend`、`frontend`、`tests`、`docs`、
`scripts`、`config`、`migrations`、`types` 或 `null` 之一。分类基于**精确路径分量相等**
(按 `/` 切分)—— 像 `data/chest_xray/img.png` 这样的文件**绝不会**被错分为 `tests`。
`where` 中多个 key 之间是 AND,同一个 key 下的列表值之间是 OR。

在 `.supamem/config.toml` 中覆盖默认关键词映射:

```toml
[supamem.classifier.rooms]
tests      = ["tests", "test", "__tests__"]
backend    = ["src", "backend", "api"]
frontend   = ["frontend", "web", "client", "components"]
# 优先级由 key 的顺序决定 —— 先匹配优先。
# 将 `tests` 排在 `backend` 之前会让 tests/backend/api_test.py 归类为 `tests`。
```

`supamem doctor` 会展示当前生效的 rooms 映射(带 `[source: ...]` 来源标注)、已存储的
`classifier_hash`,以及每个 room 的分布直方图(包括未匹配 chunk 的 `null` 桶)。

修改 `[supamem.classifier.rooms]` 会在下一次 `supamem index` 时触发一次性**重分类扫描** ——
通过 Qdrant `set_payload` 按 room 批量更新,**零重新嵌入成本**。v0.2.3 之前的旧 collection
会在升级后首次 `index` 时自动迁移。

转录类 chunk(chunker == `transcript`)按设计归类为 `room = null` —— 请通过现有的
`payload.chunker` key 过滤它们。

---

## 🎯 代码感知重排器(v0.2.4a1+)

每次 `tuned_hybrid` 查询现在**默认**通过交叉编码器
(`mixedbread-ai/mxbai-rerank-base-v2`,Apache-2.0,~1 GB)对 RRF 融合后的候选重新打分。
代码类查询的精度更锐利;v0.2.0 的回退选项是 `retrieval.reranker = "off"`,可恢复
Phase 8 之前完全字节一致的行为。

```toml
[supamem.retrieval]
reranker = "mxbai_v2"  # v0.2.4a1+ 的默认值;"off" 恢复 Phase 8 之前的行为

[supamem.retrieval.reranker]
model_id         = "mixedbread-ai/mxbai-rerank-base-v2"
top_n            = 50   # 重排池大小;若大于融合候选数会自动收敛
prefetch_per_arm = 50   # 重排器开启时从默认 20 加宽到 50
batch_size       = 16
```

重排器开启时,`tuned_hybrid` 会把 `PREFETCH_LIMIT` 加宽到每条 arm 50,跳过 T-4
的时间衰减乘子(交叉编码器叠加 recency-prior 在代码检索上是反向作用,详见
PROJECT.md),并把 T-5 余弦去重 + T-8 token 预算挪到重排之后执行。
`RetrievedChunk.rerank_score` 会带上交叉编码器 logit;主 `score` 字段也会被替换。

`supamem doctor` 在原有 Retrieval 面板之后新增 **Reranker** 面板:当前重排器名称、
model_id、缓存路径、磁盘大小 + 半下载检测、上一次加载延迟、近 100 次查询的重排
p50/p95、检测到的设备(cuda/mps/cpu)。当缓存损坏或缺文件时,运行
`supamem repair` —— 这是 doctor 驱动的自愈入口,会重新拉取缺失的模型文件、重新
同步 `share/`、修复受管的 CLAUDE.md/AGENTS.md 块、恢复客户端配置。幂等。

第三方可以通过新的 `supamem.reranker` 插件入口点组(第 4 个组,与
retrieval / embedder / chunker 并列)注册自定义重排器:

```toml
[project.entry-points."supamem.reranker"]
my_reranker = "my_pkg.module:MyReranker"
```

插件协议:`rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]`。
首次调用时懒加载模型;预热由 install/init/repair 的 fetch 流程驱动。

---

## ⏳ 按来源时序有效性(v0.3.0a1+)

每个被索引的 chunk 都携带二元字段 `valid_to`:

- `valid_to = null` → 当前生效
- `valid_to ≤ now()` → 已被取代(从所有检索中过滤掉)

当文件发生变更并重新索引时,索引器原子性地完成:

1. scroll 出该文件路径下的全部既有 chunk。
2. 对每条调用 `set_payload(valid_to = now())`(关闭其有效性窗口)。
3. 用基于内容哈希生成的 UUID upsert 新 chunk,且 `valid_to = null`。

新旧 chunk 在 Qdrant 中共存,检索只返回新 chunk;直到自动 GC 在 `retention_days`
之后清理旧 chunk。检索期过滤器只在一处构建并被所有后端继承(`tuned_hybrid` 的两条
Prefetch 臂、`dense`、`bm25`、`qdrant_find`、`dual_memory_search`)——使用 Qdrant
的 `IsEmptyCondition` 而非 `IsNullCondition`(详见
[Qdrant#5342](https://github.com/qdrant/qdrant/issues/5342):`IsNull` 不会匹配缺失字段)。

在 `.supamem/config.toml` 中配置:

```toml
[supamem.retrieval.temporal]
retention_days = 90          # 0 = 永不删除(合规 / 审计场景)
```

### Transcript 专属时序衰减(可选,默认关闭)

代码、ADR、文档不会"过期"。但 transcript 经常会过期——旧的支持对话里残留着已被废弃
的 API,会让 agent 偏离当前讨论。Phase 9 提供了一个可选的乘性带下限衰减开关,**只**
对 transcript chunk 生效,在 rerank 之后运行,绝不会自动作用于代码 / ADR / 文档:

```toml
[supamem.retrieval.recency.per_source.transcript]
enabled        = true            # 默认 false
half_life_days = 14.0
alpha          = 0.7             # 下限:最旧的 transcript 仍保留 0.7 倍得分
```

锁定默认值下的样例(`alpha = 0.7`,`half_life_days = 14`):

| Age (days) | Multiplier         |
|------------|--------------------|
| 0          | 1.000              |
| 7          | 0.924              |
| 14         | 0.850              |
| 28         | 0.775              |
| ∞          | 0.700 (floor at α) |

切换该开关时,代码 / ADR / 文档的排序保持字节级一致——由端到端字节相等性测试覆盖
(TEMP-03 验收标准)。

参考:[Customers.ai recency-weighted scoring](https://customers.ai/recency-weighted-scoring)、
[Snowflake Cortex Search scoring docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-customize-scoring)。

### Doctor 面板

`supamem doctor` 在 Reranker 与 Subagent reachability 之间新增 `Temporal validity` 面板,
展示 live / superseded / awaiting_gc / future-dated 计数、按来源拆分、`valid_from`
最旧/最新值,以及 `retention_days` 的来源行。仅读,绝不会改变 doctor 退出码。

### 迁移

升级后第一次运行 `supamem index` 会回填遗留点的 `valid_to=null`(由 manifest
保留键控制,后续运行幂等)。这是与 `IsEmpty` 运行期过滤器并行的纵深防御。

> ⚠ **默认 retention 是破坏性的**:从 v0.2.x 升级且持有超过 90 天的审计型集合的用户
> 受影响。设置 `[supamem.retrieval.temporal] retention_days = 0` 即可完全禁用自动 GC。

---

## 🔭 带过滤的检索后端(v0.3.0a3+)

`filtered_dense` 是一个"作用域+截断"的检索后端,在 `tuned_hybrid` 之上叠加 `where`
过滤与每条命中的预览长度上限。当你希望在后端层面强制"按指定路径/room 返回排序结果,
并且预览在离开 Qdrant 之前就被截断到 N 字"时使用它。

```toml
[supamem.retrieval]
backend = "filtered_dense"

[supamem.retrieval.filtered_dense]
preview_chars = 240   # 默认 240;0 完全禁用截断
```

选择方式与其他后端(`tuned_hybrid`、`dense`、`bm25`)完全一致——通过
`supamem.retrieval` 插件 entry-point 注册;切换只需改配置,无需改代码。MCP 传输层上限
(`mcp.caps.max_preview_chars`)继续叠加在后端上限之上;两者均可独立设为 `0` 关闭。

### `where` 过滤器 —— 魔法键

`dual_memory_search`(及别名 `qdrant_find`)接受 `where: dict[str, str | list[str]]`
参数,会被翻译为 Qdrant 的 payload 过滤器。除 Phase 7 的 `room` 之外,新增两个魔法键:

```python
# 1. path_prefix —— 左锚定的精确路径段匹配
dual_memory_search(query="auth flow", where={"path_prefix": "src/supamem/retrieval"})

# 多个前缀(Qdrant MatchAny)
dual_memory_search(
    query="rate limit",
    where={"path_prefix": ["src/supamem", "tests/test_filtered_dense.py"]},
)

# 2. valid_to: "now" —— Phase 9 始终在线时序条件的空操作别名
dual_memory_search(query="session", where={"valid_to": "now"})
```

语义:

- **`path_prefix`** 在 `/` 段边界上左锚定。索引器为每个 chunk 存储
  `payload.path_prefixes: list[str]`(例如 `src/supamem/retrieval/filters.py`
  → `["src", "src/supamem", "src/supamem/retrieval", "src/supamem/retrieval/filters.py"]`)。
  `path_prefix="src/supa"` **不会**匹配 `src/supamem/...`,因为 `"src/supa"` 不是被存储
  的前缀段——只有完整的 `/` 段边界会命中(贴近文件系统语义)。
- **`valid_to: "now"`** 作为空操作别名,显式表达 Phase 9 的始终在线时序条件。其他取值
  会抛出 `ValueError`——时间穿越查询不在范围内。要控制集合中保留哪些历史 chunk,请使用
  `retention_days`。

`where` 多个键之间是 AND;同一键内的列表值是 OR(`MatchAny`)。

| 键 | 语义 |
|----|------|
| `room` | Phase 7 —— 编码路径分类器(`backend`、`frontend`、`tests` 等)。字符串或列表。由 `supamem index` 写入。 |
| `path_prefix` | Phase 11 —— 对 `payload.path_prefixes` 的左锚精确路径段匹配。字符串或列表。由 `supamem index` 写入。 |
| `valid_to` | Phase 9 —— 仅接受 `"now"` 作为常驻时序子句的别名;其他值抛 `ValueError`。 |
| `session_id` | **仅 bench** —— 由 LongMemEval ingestion(`supamem.eval.longmemeval_ingest`)写入;为 pass-through 键。**`supamem index` 不会写入。** Phase 14 scoped bench 路径在专用 `supamem_eval_longmemeval_s` 集合上使用。详见 [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md)。 |

### 迁移

v0.3.0a3 之前索引的遗留 chunk 没有 `path_prefixes`。升级后第一次运行 `supamem index`
会执行一次性的 scroll-and-`set_payload` 扫描,为每个 chunk 回填 `path_prefixes`——
纯元数据更新,**零再嵌入开销**,后续运行幂等。**无需** `--force` 重新索引。

### Doctor 面板

`supamem doctor` 新增 "Filtered-dense backend" 面板,显示 `preview_chars` 解析值
及其 `[source: ...]` 来源。仅读,绝不会改变 doctor 退出码。

---

## 📊 Benchmarks(v0.3.0a4+)

**方法学变更。** `supamem eval --suite longmemeval_s` 每题同时跑 **unscoped**
与 **scoped** 两条检索路径。scoped 路径根据 LongMemEval haystack 的 session id
构造每题 `where` 过滤(`{"session_id": [...]}`),从端到端串通 Phases 7 / 9 /
11 / 14 引入的索引侧 filter payload(`room`、`path_prefix`、`valid_to`、
`session_id`)。发布 gate(`tokens_per_correct_answer` 相对 v0.1.5 baseline
的 delta)只读 **scoped** 路径;unscoped 仅用于透明披露,不参与 gate。完整
依据见 [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md)。

**可复现性提示。** 默认 unscoped 调用 `dual_memory_search` / `qdrant_find`
不一定能复现 scoped 数字。希望复现的用户必须显式传 `where={...}` 过滤,且
collection 中的 chunk 必须携带匹配的 payload —— 这是方法学披露,不是缺陷。

**Baseline 语料。** v0.1.5 baseline 已在专用 bench collection
(`supamem_eval_longmemeval_s`)上重新采集。Phase 14 之前的绝对数字与之后
的不可直接对比 —— 语料变了。原 devdocs collection 的旧值以
`legacy_devdocs_unscoped_tpca` 字段保留在 `eval/baselines/v0.1.5.json` 供
历史参考,但**不**参与 gate。

**FUTURE-24(rerank composition rework)** 是同辈解锁项,单独追踪。Phase 14
的 scoped 路径以 rerank-OFF 运行,使得 scoped vs unscoped 的差值干净归因于
scoping。关于 scoping 收益的公开说辞**不**外推到「等到 rerank composition
也修好就能再缩小 X% gap」。

**Smoke fixture。** 内置静态 fixture 位于
`src/supamem/eval/datasets/longmemeval_scoped_smoke.json`(≤5 题、≤200 KB、
自包含),通过新套件名 `longmemeval_scoped_smoke` 暴露 —— 在 CI 中运行而
不触发 ~3 GB 懒加载。

---

## 🚫 supamem **不做**的事情

`supamem` **不会**自动向 agent 调用注入 identity / wake-up / prelude 上下文
—— 检索始终通过显式查询发起。不存在隐藏的"agent 身份"层、不存在 SessionStart
时机塞入隐式上下文的 wake-up 负载、也不存在 `query` 为空时仍会触发检索的 MCP 工具。

这一点从两层加锁:

1. **Schema 层(v0.3.0a3+):** 每个检索工具的 `query` 参数都是
   `Field(..., min_length=1, max_length=...)`——必填、非空,在工具注册时即由
   schema 强制。空 `query` 会被 MCP 验证错误显式拒绝,不会被悄悄替换为默认上下文。
2. **测试层(FILT-02):** `tests/test_no_identity_tier.py` 是 CI 强制的回归测试,
   一旦未来注册的 MCP 工具名命中
   `(?i)(wake[_-]?up|identity|prelude|inject)`,或任一检索工具的 JSON Schema 把
   `query` 移出 `required` / 丢失 `minLength >= 1`,构建立即失败。

如果你希望在会话开启时加载 supamem 上下文,现有的 SessionStart 横幅 hook 是受支持的入口
——它注入一行状态(集合、chunk 计数、audit 日志路径),绝不会暗中把检索结果塞给模型。
模型仍需调用 `dual_memory_search` 才能读取语料。

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

> ✨ **v0.2.0 — 多项目安装(默认改为按工作区写入)。** `supamem install` 默认写入 `<repo>/.mcp.json`(Claude Code 项目作用域)和 `<repo>/.cursor/mcp.json`(Cursor 工作区作用域),并自动注入 `SUPAMEM_PROJECT_ROOT`。从旧版全局安装迁移:在每个 supamem 项目运行 `supamem repair` —— 它会清除全局陈旧条目并按项目作用域重新安装。
>
> 启用强制搜索门(可选,仅 Claude Code):`supamem install --client claude-code --enforce-search` 会注册一个 PreToolUse 门,在当前用户回合内未调用 `mcp__supamem__dual_memory_search` 时拒绝 `Edit/Write/MultiEdit`。每会话临时绕过:`SUPAMEM_GATE_DISABLE=1`。Cursor 的 hooks API 目前不支持失败关闭式预编辑事件 —— 改为通过 `beforeSubmitPrompt` 注入建议消息;通过 `SUPAMEM_ADVISORY_DISABLE=1` 关闭。
>
> 会话开始横幅现在带 1 字符健康标志(`✓` / `⚠`)并在本地缓存检测到新版本时附加 `update v0.X.Y available`。

> 🛟 **MCP 从错误的 cwd 启动?** 某些宿主(Cursor、部分 IDE 封装器)会从 `$HOME` 而非工作区启动 MCP 子进程,导致 supamem 回退到默认 collection(`dev_memory_tuned_hybrid`)并返回 Qdrant 404。
> 在宿主的 MCP 配置中设置 `SUPAMEM_PROJECT_ROOT=/abs/path/to/workspace`(例如 `~/.cursor/mcp.json` 的 `env` 块,或 `~/.claude.json` 中 `mcpServers.supamem.env`)。
> 若未设置,supamem 会向上搜索父目录查找 `.supamem/config.toml` 或 `pyproject.toml` 的 `[tool.supamem]`,找不到时会在 stderr 输出一行警告。
> 在仓库根目录运行 `supamem doctor` 验证:解析出的 collection 应与 MCP 客户端 `dual_memory_search` 返回的一致。

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
