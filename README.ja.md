**言語:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ f96d0a1 -->

> この翻訳は AI 支援によるものです。ネイティブスピーカーによる修正 PR を歓迎します。

<div align="center">

# 🧠 supamem

**AI コーディングエージェント向け Qdrant ベース デュアルメモリ**

*Claude Code、Cursor、OpenCode にプロジェクトをまたいだ永続的な意味的+構造的メモリを与える。*

[![PyPI](https://img.shields.io/pypi/v/supamem?style=flat-square&logo=pypi&logoColor=white&color=blue)](https://pypi.org/project/supamem/)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-success?style=flat-square)](LICENSE)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.10%2B-DC382D?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![MCP](https://img.shields.io/badge/MCP-1.13%2B-9333EA?style=flat-square)](https://modelcontextprotocol.io/)
[![Powered by SoftChat](https://img.shields.io/badge/Powered%20by-SoftChat-FF4D8D?style=flat-square)](https://app.softchat.ru)

</div>

---

> ### 👋 [**Dzmitry Sukhau**](https://www.linkedin.com/in/dzmitrys/) が制作 — AI ネイティブ ソリューション/ソフトウェアアーキテクト/CTO
>
> **AI プロダクトのコンサルティング**、**既存プロダクトへの AI 統合**、**業務プロセスの自動化** を承ります。
>
> LLM 機能を出荷中、検索パイプラインを評価中、エージェント系を堅牢化中、または AI ファーストのプロダクトをゼロから構築中なら — お声がけください。
>
> [![LinkedIn — Dzmitry Sukhau](https://img.shields.io/badge/LinkedIn-Dzmitry%20Sukhau-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)
> &nbsp;&nbsp;
> [![Open to Consulting](https://img.shields.io/badge/Open%20to-Consulting%20%26%20Architecture-22C55E?style=for-the-badge&logo=anthropic&logoColor=white)](https://www.linkedin.com/in/dzmitrys/)

---

## ✨ supamem とは?

`supamem` は、任意の AI コーディングアシスタントに **本番品質のメモリレイヤー** を配線する単一バイナリ CLI です。
新しいリポジトリにドロップして `supamem init` を実行すれば、エージェントは即座に以下を獲得します:

- 🔍 **意味検索**:プロジェクトのノート、ADR、決定、過去の会話に対するハイブリッド sparse+dense 検索
- 🤖 **MCP サーバー**:互換クライアント(Claude Code、Cursor、OpenCode)が話せる
- 🪝 **クライアント別フック**:セッション開始時とファイル編集時に関連メモリを自動ロード
- 📊 **Welford 利用統計**:何が実際に呼び出されているかが見える
- 🧪 **評価ハーネス**:33 クエリの正解コーパスで検索リグレッションを検出

[SoftChat](https://app.softchat.ru) 内部で実戦投入(80.1–80.5 フェーズ)した後、すべてのチームが採用できる
スタンドアロンパッケージとして抽出されました。

---

## 🎯 supamem が存在する理由

**問題:** コーディングエージェントはセッション間でメモリを持ちません。Claude Code / Cursor / OpenCode で
新しい会話を開くたびに、モデルはあなたのコードベース、過去の決定、ADR、既知の問題、慣習について
ゼロ知識です。だから:

1. すべてのセッション開始時に **5–15 KB のコンテキストを再貼り付け**(遅い、エラーが起きやすい、コストがかかる)、または
2. エージェントに **手探りさせる** — リポを grep ウォークし、冗長な質問をし、先週の決定を忘れ、
   半年前にあなたが文書化した同じ落とし穴を再発見する。

**解決策:** *現在の*プロンプトに対して*正しい* 1–2 KB のコンテキストを自動取得する、永続的な
意味的+構造的メモリレイヤー — 手動貼り付けなし、再説明なし、コンテキスト爆発なし。

> **フェーズ 80.1 ベンチ(33 ラベル付き正解、実 Claude Code セッション):**
> **素朴な全文書検索に対し −78.5% トークン**(同じ recall)、**エンドツーエンド p95 73 ms**。
>
> 完全な評価は、私たちが SoftChat 内部で本番パイプラインを固めるために実行したのと同じものです。
> 方法論:33 の代表的な dev クエリ → 4 つの検索アームを比較(baseline_union、tuned_current、
> tuned_hybrid、mem0_vector) → アームごとにトークン数 + recall CI + レイテンシを測定。

### 📊 トークン消費:メモリありエージェント vs なし

下記の数値は **典型的な 30 ターン Claude Code セッション** あたり、~50 ADR / insights / rules を持つ
実コードベースを想定(≈ SoftChat の規模)。YMMV — ですがアーム間の*比率*は安定しています。

| アプローチ | ターンあたり | 30ターン/セッション | 備考 |
|---|---:|---:|---|
| ❌ メモリレイヤーなし | 自動注入 **≈ 0** だが手動でコンテキスト貼り付け | **30,000–80,000**(手動貼り付けの繰り返し) | 構築ではなくコピーに認知負荷を消費 |
| ⚠️ 素朴な RAG(全文書 embed) | ターンあたり ~5,800 | **~174,000** | 肥大、段落しか必要なくても大きなファイルを呼び出す |
| ✅ **supamem `tuned_hybrid`** | **ターンあたり ~1,250** | **~37,500** | 同じ recall、素朴 RAG に対し **−78.5% トークン** |

### 💰 推論コストのおおよその節約

Anthropic API 公開価格(2026 年 3 月):
**Sonnet 4.6 = $3 / Mtok input** · **Opus 4.7 = $15 / Mtok input**。

| モデル | セッションあたり節約トークン vs 素朴 RAG | セッションあたり節約コスト | 月次(110 セッション) |
|---|---:|---:|---:|
| Sonnet 4.6 | **136,500** | **$0.41** | **~$45/dev** |
| Opus 4.7 | **136,500** | **$2.05** | **~$225/dev** |

10 名のエンジニアチームが Opus を回すと、**月あたり ~$2,250** を入力トークンだけで節約 —
イテレーション遅延、決定の喪失、コンテキスト再貼り付け時間のコストはここに含まれていません。
出力トークン節約(幻覚減少、往復ターン減少)はその上に積み上がります。

### 🥊 代替案との比較

| | メモリなし | 素朴 RAG | mem0 / 原子事実 | **supamem (tuned_hybrid)** |
|---|:---:|:---:|:---:|:---:|
| セッション開始時の自動注入 | ❌ | ⚠️ | ✅ | ✅ |
| ハイブリッド sparse+dense 検索 | ❌ | ❌ | ❌ | ✅ |
| コード識別子の保持 | ❌ | ✅ | ❌(名前を捨てる) | ✅ |
| ロックされたスキーマ + 正解評価 | ❌ | ❌ | ❌ | ✅ |
| マルチクライアント(Claude/Cursor/OpenCode) | ❌ | ❌ | ⚠️ | ✅ |
| p95 レイテンシ | n/a | ~120 ms | ~80 ms | **73 ms** |
| トークン肥大 | 高(手動) | 最高 | 低だが損失あり | **完全 recall で最低** |

**なぜハイブリッド?** BM25 は dense embedding がぼかす *正確な識別子*(`ChatService.generate`、
env-var 名、ファイルパス)を捉えます。Dense は BM25 が見逃す *意味的意図*(「billing webhook はどう処理する?」)を
捉えます。RRF 融合は両方のランキングを組み合わせ、それぞれの長所を得ます。

**なぜ mem0 ではない?** mem0 の原子事実抽出はコード識別子を失います — 33 クエリベンチでの recall は
**0.015**(実質ゼロ)。個人 CRM 風のメモリには素晴らしいですが、コード認識検索には向きません。

---

## ⚡️ 60 秒クイックスタート

```bash
# 1. インストール(uv が最速)
uv tool install supamem

# 2. Qdrant を起動(初回のみ、~30s)
docker run -d -p 6333:6333 -p 6334:6334 -v $HOME/.qdrant:/qdrant/storage qdrant/qdrant:latest

# 3. プロジェクトをブートストラップ
cd your-project
supamem init

# 4. AI クライアントに配線
supamem install --client claude-code   # または cursor / opencode

# 5. 健全性を確認
supamem doctor
```

これで完了。プロジェクト内で Claude Code(または好みのクライアント)を開く — メモリツールはすでにメニューにあります。✨

---

## 👀 動いているところを見る — `supamem live`

`supamem live` を別ターミナルで実行すると、すべての検索呼び出しをリアルタイムで観察できます — Claude Code / Cursor / OpenCode と並行して使えば、サイレントな PreToolUse フック注入(UI を出さないからこそトークンを節約している)を瞬時に可視化できます。

![supamem live dashboard](docs/media/supamem-live.svg)

**SessionStart バナー**(v0.1.4+)は AI クライアントのセッション開始時にも一行ステータスを差し込みます:`🧠 supamem v0.1.4 · <collection> · <N> chunks · audit <path>` — 環境変数から Claude Code / Cursor / OpenCode を自動検出。

> 🎬 **インタラクティブデモ:** [`supamem-live.cast`](docs/media/supamem-live.cast) — [asciinema.org/player](https://asciinema.org/) にドロップするか、ローカルで `asciinema play docs/media/supamem-live.cast` を実行。

---

## 🚀 機能

| 機能 | 説明 |
|---|---|
| 🔍 **ハイブリッド検索** | 調整済み sparse(BM25) + dense(MiniLM) 融合、ロックされたスキーマ D-25 |
| 🎯 **コード対応リランカー** | クロスエンコーダ `mxbai-rerank-base-v2`(Apache-2.0)が既定で `tuned_hybrid` の候補を再採点します。`retrieval.reranker = "off"` で無効化すると v0.2.4a1 以前の挙動に戻ります。(Phase 8, RERANK-01..04) |
| 📚 **Markdown チャンカー** | ヘッダー意識、200 トークン目標 / 250 トークン軟上限(T-1) |
| 🤖 **MCP サーバー** | `stdio`(デフォルト)と `http` トランスポート、公式 `mcp` SDK |
| 🪝 **マルチクライアントフック** | Claude Code セッション開始、OpenCode セッション開始、Cursor MDC |
| 🧰 **ワンコマンドインストール** | 自動バックアップとロールバック付きアトミック設定パッチ |
| 🩺 **`supamem doctor`** | Qdrant をプローブ、設定チェーンを解決、バージョンドリフトを表示 |
| 👀 **`supamem live`** | audit JSONL を追跡する Rich-Live ターミナルダッシュボード — 検索呼び出しのリアルタイム可視化(v0.1.4+) |
| 🎬 **SessionStart バナー** | セッション開始時に注入されるクロスクライアント一行バナー(Claude Code / Cursor / OpenCode)、v0.1.4+ |
| 📊 **Welford カウンタ** | プロジェクトごとの recall レート、レイテンシ、クエリボリュームを追跡 |
| 🧪 **評価ハーネス** | 33 クエリ正解コーパス + リグレッション検出 |
| 🔁 **ブラウンフィールド移行** | 既存 `dev_memory` を検出して非破壊的に移行 |
| 🎨 **スタイリッシュ CLI** | Rich ベースのスピナー、パネル、カラーで常に進捗を確認 |

---

## 📋 前提条件

実際に必要なのは **Python 3.12+** と **Qdrant** の二つだけ。それ以外はオプションです。

<details>
<summary><b>🐍 Python 3.12+ &nbsp;·&nbsp; インストールコマンドを展開</b></summary>

```bash
# macOS (Homebrew)
brew install python@3.12

# Linux (Ubuntu/Debian)
sudo apt install python3.12 python3.12-venv

# Windows (PowerShell)
winget install Python.Python.3.12
```

[`uv`](https://docs.astral.sh/uv/) のインストールを強く推奨 — 最速の Python パッケージマネージャ:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

</details>

<details>
<summary><b>🗄️ Qdrant 1.10+ &nbsp;·&nbsp; ベクトルデータベース(必須)</b></summary>

最も簡単なのは Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v $HOME/.qdrant:/qdrant/storage \
  qdrant/qdrant:latest
```

または `docker compose` で:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333", "6334:6334"]
    volumes: ["./qdrant_data:/qdrant/storage"]
    restart: unless-stopped
```

Docker がない?[Qdrant Cloud](https://cloud.qdrant.io/) でマネージドクラスタを実行(無料枠あり)し、
`supamem init` で URL を指定してください。

</details>

<details>
<summary><b>🤖 MCP 互換クライアント &nbsp;·&nbsp; 少なくとも一つ選択</b></summary>

| クライアント | インストール | 備考 |
|---|---|---|
| [Claude Code](https://claude.com/claude-code) | `npm install -g @anthropic-ai/claude-code` | ファーストクラス MCP サポート |
| [Cursor](https://cursor.com/) | cursor.com からダウンロード | MDC ルール + MCP を使用 |
| [OpenCode](https://opencode.ai/) | `curl -fsSL https://opencode.ai/install \| bash` | オープンソース TUI、ネイティブ MCP |

</details>

---

## 📦 インストール

```bash
# 推奨:uv(最速、隔離)
uv tool install supamem

# 代替:pipx(これも隔離)
pipx install supamem

# プレーン pip(venv 内で)
pip install supamem
```

確認:

```bash
supamem --version
```

カラフルなバナーとクレジット行が表示されるはずです。🎨

> **最新:** `v0.1.5` は [PyPI](https://pypi.org/project/supamem/) で公開されています。Trusted
> Publisher OIDC でリリース — すべての wheel は来歴証明付きです。

### インストール時にモデルをキャッシュ

`supamem install <client>` と `supamem init` は ML 前提条件(MiniLM ~90 MB、
BM25 ~10 MB、mxbai-rerank-base-v2 ~1 GB)をプログレスバー付きで先行ダウンロード
します。インストール後の冷起動 CLI 呼び出し(`supamem --help`、`supamem doctor`、
`supamem --version`)はネットワーク送信ゼロです。エアギャップ環境での初回起動?
`--skip-models` を渡し、ネットワークが利用可能になった後に `supamem repair` を
一度実行してバックフィルしてください。

モデルは `platformdirs.user_cache_dir("supamem")/models/` 配下に置かれます
(`SUPAMEM_CACHE_DIR` で上書き可)。

### サブエージェントの到達性(v0.2.5+)

GSD、superpowers、hookify、その他 agent 定義に `tools:` ホワイトリストを固定する
プラグインの Claude Code サブエージェントを使用している場合、ホワイトリストに
`mcp__supamem__*` が含まれていない限り、それらのエージェントは supamem MCP サーバーに
到達できません — 親セッションが supamem に接続済みでも同様です。サブエージェントは
frontmatter にリストされたツールのみを継承します。

`supamem install` と `supamem repair` は自動的にこのパッチを適用します:

```bash
supamem install --client claude-code   # patches ~/.claude/agents/ + <project>/.claude/agents/
supamem repair                         # re-applies if a plugin overwrites your agents
```

パッチは冪等(2 回実行しても変更ゼロ)で、YAML スタイル(CSV か list か)を保持し、
シンボリックリンクされた agent ファイルは警告付きでスキップします。`tools:` 行が
欠落または空のファイルは Claude Code セマンティクスに従って完全継承され、変更されません。

バックアップマニフェストは `~/.cache/supamem/agent_patches.json` にあります。
クリーンに反転するには:

```bash
supamem unpatch-agents
```

`install` / `init` / `repair` のいずれかで `--skip-patch-agents` を渡すとオプトアウトできます。

#### supamem のアンインストール

```bash
supamem unpatch-agents      # restore agent whitelists first
pip uninstall supamem
```

2026 年時点で `pip` / `uv` / `pipx` には移植可能なアンインストールフックが存在しない
ため、この 2 ステップがサポートされる契約です。`supamem doctor` がマニフェストパスと
リマインダーを表示するので、自然にこのフローを発見できます。

---

## 🎯 CLI 一覧

| コマンド | 用途 |
|---|---|
| `supamem init` | グリーンフィールドブートストラップ — Qdrant をプローブ、コレクション作成、`.supamem/config.toml` 書き込み |
| `supamem install --client <name>` | クライアント設定にパッチ(`claude-code`、`cursor`、`opencode`)— アトミック+バックアップ。v0.2.5+:`~/.claude/agents/` と `<project>/.claude/agents/` の制限的な `tools:` ホワイトリストに `mcp__supamem__*` を自動追加;`--skip-patch-agents` でオプトアウト。 |
| `supamem index` | ロックされた tuned-hybrid パイプラインで dev メモリを Qdrant に embed(D-25) |
| `supamem mcp-server` | MCP サーバー実行(`--transport stdio` デフォルト;`http` で HTTP) |
| `supamem hook <client>` | クライアント別セッション/編集フック(クライアント自身が呼び出す) |
| `supamem doctor` | 🩺 Qdrant をプローブ、解決済み設定チェーンを出力、バージョンドリフトを報告 |
| `supamem stats` | `.supamem/state/` からの Welford schema-v2 利用カウンタ |
| `supamem live` | 👀 audit JSONL を追跡するライブダッシュボード — パイプセーフ(非 TTY 時はプレーン JSONL);ローテーション、リサイズ、Ctrl-C を処理 |
| `supamem migrate` | 既存 `dev_memory` コレクションからのブラウンフィールド移行 |
| `supamem eval` | 内蔵 33 クエリ正解コーパスに対するリグレッションハーネス実行 |
| `supamem uninstall --client <name>` | `supamem install` をクリーンに反転 |
| `supamem unpatch-agents` | 🔄 サブエージェント到達性パッチを反転(v0.2.5+)。`~/.cache/supamem/agent_patches.json` のマニフェストに従って agent ファイルをパッチ前の形に復元。あなたが編集済みのファイルは警告付きでスキップ。クリーンなアンインストールのため `pip uninstall supamem` の前に実行してください。 |

すべての長時間実行コマンドは経過時間付きの **ライブスピナー** を表示するので、動作中だと
常にわかります。任意のサブコマンドに `--help` を付けると詳細が出ます。

---

## 📜 トランスクリプトの取り込み(v0.2.2a1+)

supamem は **Claude Code のセッション履歴**を Q+A 形式のドロワーチャンクとしてプロジェクトの Markdown コーパスと並べてインデックスでき、過去の決定やツール呼び出しの軌跡を `dual_memory_search` に登場させられます。既定はオフ —— `--transcripts` で明示的に有効化してください。

```bash
# 既定の場所(~/.claude/projects/)から Claude Code トランスクリプトを取り込む
supamem index --transcripts

# 特定のディレクトリを指す場合
supamem index --transcripts /path/to/sessions/

# 通常のプロジェクトコーパスを飛ばし、トランスクリプトのみを取り込む
supamem index --transcripts --transcripts-only

# 直近のセッションのみに限定(既定: 180 日; --since 0 でフィルタ無効)
supamem index --transcripts --since 30d
```

`.supamem/config.toml` の `[supamem.transcript]` 配下で設定します:

```toml
[supamem.transcript]
default_root           = "~/.claude/projects/"
since_days             = 180
tool_payload_max_chars = 2000
chunk_soft_max_tokens  = 600
include_paths_glob     = []
exclude_paths_glob     = []   # 機微なセッションを除外、例: ["**/banking-*.jsonl"]
```

> ⚠ **トランスクリプトには機密が含まれる可能性があります。** API キーやトークンその他の認証情報が、たまたま Claude Code セッションに貼り付けられることがあります。v0.2.2a1 は **マスキング処理を行いません** —— 共有する前に `~/.cache/supamem` の Qdrant コレクションを必ず確認してください。`exclude_paths_glob` で機微なセッションを手動除外できます。マスキングは v0.3 で `supamem.redactor` プラグイングループとして提供予定です。

現在サポートされているトランスクリプト形式: **Claude Code JSONL**(Cursor SQLite と ChatGPT エクスポートは後続プラグインに先送り)。

---

## 🔎 スコープ付き検索(v0.2.3a1+)

`dual_memory_search`(および `qdrant_find` エイリアス)の `where` パラメータで、
コードパスのカテゴリごとに検索結果を絞り込めます:

```python
# backend に分類されたチャンクだけを対象にする
dual_memory_search(query="auth flow", where={"room": "backend"})

# 複数の room にまたがる OR(Qdrant MatchAny)
dual_memory_search(query="rate limit", where={"room": ["backend", "tests"]})
```

インデックス済みのすべてのチャンクは `payload.room` を保持します。値は `backend`、
`frontend`、`tests`、`docs`、`scripts`、`config`、`migrations`、`types`、`null` の
いずれかです。分類は **パスコンポーネントの厳密な等価判定**(`/` 区切り)で行われ、
`data/chest_xray/img.png` のようなファイルは **絶対に** `tests` には分類されません。
`where` 内の複数キーは AND、同一キー内のリスト値は OR です。

`.supamem/config.toml` で既定のキーワードマップを上書きできます:

```toml
[supamem.classifier.rooms]
tests      = ["tests", "test", "__tests__"]
backend    = ["src", "backend", "api"]
frontend   = ["frontend", "web", "client", "components"]
# 優先度はキーの順序で表現されます —— 先勝ち(first match wins)。
# `tests` を `backend` より前に置くと、tests/backend/api_test.py は `tests` に分類されます。
```

`supamem doctor` は、有効な rooms マップ(`[source: ...]` 由来表示付き)、保存された
`classifier_hash`、room ごとのヒストグラム(マッチしないチャンク用の `null` バケット
を含む)を表示します。

`[supamem.classifier.rooms]` を変更すると、次回の `supamem index` 実行時に **再分類
スイープ** が一度だけ走ります —— Qdrant の `set_payload` を room 単位で実行する
だけなので、**再埋め込みコストはゼロ** です。v0.2.3 以前のコレクションは、アップ
グレード後の最初の `index` 呼び出しで自動的に移行されます。

トランスクリプト由来のチャンク(chunker == `transcript`)は構造上 `room = null` に
分類されます —— 既存の `payload.chunker` キーで絞り込んでください。

---

## 🎯 コード対応リランカー(v0.2.4a1+)

すべての `tuned_hybrid` クエリは、**既定で** RRF で融合された候補をクロスエンコーダ
(`mixedbread-ai/mxbai-rerank-base-v2`、Apache-2.0、~1 GB)で再採点します。コード
中心のクエリで精度が鋭くなります。v0.2.0 のエスケープハッチは
`retrieval.reranker = "off"` で、Phase 8 以前のバイト一致の挙動に戻ります。

```toml
[supamem.retrieval]
reranker = "mxbai_v2"  # v0.2.4a1+ の既定値;"off" で Phase 8 以前の挙動に戻る

[supamem.retrieval.reranker]
model_id         = "mixedbread-ai/mxbai-rerank-base-v2"
top_n            = 50   # リランクのプールサイズ;融合候補数を超えるとクランプ
prefetch_per_arm = 50   # リランカー有効時に既定の 20 から拡張
batch_size       = 16
```

リランカー有効時、`tuned_hybrid` は `PREFETCH_LIMIT` を arm ごと 50 に拡張し、
T-4 リーセンシー乗数をスキップ(クロスエンコーダ + recency-prior はコード検索で
反作用、PROJECT.md 参照)、T-5 コサイン重複排除と T-8 トークン予算は
リランクの **後** に実行します。`RetrievedChunk.rerank_score` には
クロスエンコーダの logit が入ります。プライマリ `score` も同値で置換されます。

`supamem doctor` は既存の Retrieval パネルの後に **Reranker** パネルを追加します:
有効なリランカー名、model_id、キャッシュパス、ディスク使用量 + 部分ダウンロード
検出、最終ロード遅延、直近 100 件のリランク p50/p95、検出デバイス
(cuda/mps/cpu)。キャッシュが破損または不完全な場合は `supamem repair` を
実行してください —— doctor 主導のセルフヒール正規入口で、欠損したモデル
ファイルを再取得し、`share/` を再同期し、管理対象の CLAUDE.md/AGENTS.md ブロック
を修復し、クライアント設定を復元します。冪等です。

サードパーティは新しい `supamem.reranker` プラグイン入口グループ(retrieval /
embedder / chunker と並ぶ 4 つ目のグループ)を介してカスタムリランカーを登録できます:

```toml
[project.entry-points."supamem.reranker"]
my_reranker = "my_pkg.module:MyReranker"
```

プラグインプロトコル:`rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]`。
初回呼び出しでモデルを遅延ロード;eager ウォームアップは install/init/repair の
fetch パイプラインで実行されます。

---

## 🪛 クライアントへの配線

<details>
<summary><b>Claude Code</b></summary>

```bash
supamem install --client claude-code
```

`~/.claude.json` の `mcpServers` 配下にエントリを追加し、`~/.claude/hooks/` にセッション開始フックを
登録します。`--dry-run` で適用前にプレビュー可能。

</details>

<details>
<summary><b>Cursor</b></summary>

```bash
supamem install --client cursor
```

`.cursor/mcp.json` にパッチを当て、`.cursor/rules/dual-memory.mdc` を書き込みます。

</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
supamem install --client opencode
```

`~/.config/opencode/opencode.json` を更新し、`~/.config/opencode/hooks/` にセッション開始フックを書き込みます。

</details>

> ✨ **v0.2.0 — マルチプロジェクト対応(デフォルトがワークスペース単位に変更)。** `supamem install` は既定で `<repo>/.mcp.json`(Claude Code のプロジェクトスコープ)および `<repo>/.cursor/mcp.json`(Cursor のワークスペース)に書き込み、`SUPAMEM_PROJECT_ROOT` を自動注入します。レガシーなグローバルインストールからの移行は、各 supamem プロジェクトで `supamem repair` を実行 —— 古いグローバル設定を削除し、プロジェクトスコープで再インストールします。
>
> 検索強制ゲート(オプトイン、Claude Code のみ): `supamem install --client claude-code --enforce-search` は PreToolUse ゲートを登録し、現在のユーザーターン内に `mcp__supamem__dual_memory_search` の呼び出しがない `Edit/Write/MultiEdit` を拒否します。セッション内で一時的に無効化: `SUPAMEM_GATE_DISABLE=1`。Cursor の hooks API には fail-closed なプリエディットイベントが未だ無いため、`beforeSubmitPrompt` で `agentMessage` のアドバイザリーを注入します; `SUPAMEM_ADVISORY_DISABLE=1` で無効化できます。
>
> SessionStart バナーは 1 文字のヘルスフラグ(`✓` / `⚠`)を先頭に表示し、ローカルキャッシュに新バージョンが検出された場合は `update v0.X.Y available` を末尾に追加します。

> 🛟 **MCP が間違った cwd から起動されている?** 一部のホスト(Cursor、特定の IDE ラッパー)はワークスペースではなく `$HOME` から MCP サブプロセスを起動するため、supamem はデフォルトの collection(`dev_memory_tuned_hybrid`)にフォールバックし Qdrant が 404 を返します。
> ホストの MCP 設定(例: `~/.cursor/mcp.json` の `env` ブロック、または `~/.claude.json` の `mcpServers.supamem.env`)に `SUPAMEM_PROJECT_ROOT=/abs/path/to/workspace` を設定してください。
> 未設定の場合、supamem は親ディレクトリを遡って `.supamem/config.toml` または `pyproject.toml` の `[tool.supamem]` を探し、見つからなければ stderr に 1 行の警告を出力します。
> リポジトリのルートで `supamem doctor` を実行して検証: 解決された collection が MCP クライアントの `dual_memory_search` が返すものと一致しているはずです。

---

## 🧠 仕組み

```text
┌─────────────────┐    MCP/stdio     ┌─────────────────┐    REST    ┌─────────────┐
│ Claude / Cursor │ ───────────────► │  supamem MCP    │ ─────────► │   Qdrant    │
│   / OpenCode    │ ◄─────────────── │     server      │ ◄───────── │  (ベクトル)  │
└─────────────────┘                  └─────────────────┘            └─────────────┘
        │                                    ▲
        │ セッション開始フック                  │ tuned-hybrid 検索
        ▼                                    │ (BM25 + MiniLM 融合)
┌─────────────────┐                          │
│ supamem hook    │ ─────────────────────────┘
│ (自動 recall)   │
└─────────────────┘
```

- **インデクサ** が Markdown をヘッダーで分割(T-1 chunker、目標 200 トークン / 軟上限 250)
- **エンベッダ** が sparse(BM25)と dense(MiniLM-L6)ベクトルを生成
- **検索** は両アームを並列実行、reciprocal rank fusion で融合、top-k を返却
- **MCP サーバー** は `dual_memory_search`(読み取り)と `dual_memory_write`(書き込み/べき等な
  エージェントメモリ永続化)を公開 — さらに上流 `mcp-server-qdrant` から来たユーザー向けに
  ドロップインエイリアスとして `qdrant_find` と `qdrant_store` も(`SUPAMEM_QDRANT_ALIASES=0` で無効化)
- **フック** が適切なタイミングで `supamem hook <client>` を呼び出すので、メモリは透過的にロードされる

---

## 🤝 貢献

PR を歓迎します!クイックスタート:

```bash
git clone https://github.com/dzmitrys-dev/supamem.git
cd supamem
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest
ruff check .
```

ツリー内 `dev_memory` セットアップから来た?[MIGRATION.md](MIGRATION.md) を参照。

---

## 📜 ライセンス

MIT — [LICENSE](LICENSE) を参照。

---

<div align="center">

### 💜 心を込めてお届け:

<a href="https://app.softchat.ru"><b>SoftChat</b></a> &nbsp;·&nbsp; <a href="https://softskillz.ai"><b>SoftSkillz</b></a>

*ロシア語 AI チャットプラットフォーム &nbsp;·&nbsp; AI ファースト プロダクトエンジニアリング*

`supamem` は SoftChat の本番メモリスタックから抽出され、すべてのチームが同じ実戦テスト済みパイプライン上で
動作できるようになりました。エージェントを賢くしてくれたなら ⭐ をください — そして私たちが何を構築しているか
ぜひ見てください。

<sub>ベラルーシで心を込めて制作 &nbsp;🇧🇾&nbsp; · &nbsp;<a href="https://app.softchat.ru">app.softchat.ru</a> &nbsp;·&nbsp; <a href="https://softskillz.ai">softskillz.ai</a></sub>

</div>
