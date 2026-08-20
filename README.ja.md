**言語:** [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [Русский](README.ru.md)

<!-- synced-with: README.md @ 7f58a70 -->

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
uv tool install 'supamem==0.4.0a2'

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
| ⏳ **ソース別の時間有効性** | 各 chunk に `valid_from`/`valid_to` を付与;変更されたファイルを再インデックスすると、旧 chunk をアトミックに無効化し、検索時フィルタが全バックエンド共通で無効化済み点を除外します。トランスクリプト専用の任意の recency 減衰(既定 OFF)。`retention_days = 90` を超えると自動 GC(`0` で恒久保持 / 監査用コレクション)。(Phase 9, TEMP-01..03) |
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
uv tool install 'supamem==0.4.0a2'

# 代替:pipx(これも隔離)
pipx install supamem==0.4.0a2

# プレーン pip(venv 内で)
pip install supamem==0.4.0a2
```

> **バージョン固定は必須です。** `0.4.0a2` はプレリリースであり、最新の*安定版*(`0.2.0`)は `0.3.x` / `0.4.x` 系列全体より古いため、固定なしの `install supamem` は本ドキュメントが説明する機能よりも古いバージョンへ**逆方向に**解決されます。厳密なプレリリース固定は追加フラグなしで解決でき、依存関係は安定版のままです。`--prerelease allow` は**使用しないでください**(解決全体に適用され、プレリリース依存を引き込みます)。

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
| `supamem eval` | bench ハーネスを実行。`--suite goldens`(デフォルト、内蔵 33 クエリのリグレッション正解コーパス)または `--suite longmemeval_s`(初回実行時に LongMemEval_S を遅延フェッチ、~3 GB;CI 高速パスは軸別層化された 10 問サブセット、完全な ~500 問は `--full` でゲート)。v0.3.0a4+:質問ごとに scoped と unscoped の 2 パスを発行し、公開 gate は **scoped 専用**([ADR-0001](docs/adr/0001-scoped-only-bench-gate.md))。CI 用に新たな内蔵 `--suite longmemeval_scoped_smoke`(≤5 問、遅延フェッチなし)を追加。MTEB 形式の JSON envelope を `~/.supamem/eval/<utc-iso>.json` に出力。デフォルトジャッジはオフラインのヒューリスティック;ローカル Ollama ジャッジは `--judge ollama:<model>` で指定 — SaaS エンドポイントは拒否されます(D-07)。オプション extra: `pip install supamem[eval]` で RAGAS トライアド有効(v0.3.0a2+)。レガシー `--regress` モードは温存。 |
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

## ⏳ ソース別の時間有効性(v0.3.0a1+)

インデックスされた各 chunk は二値の `valid_to` フィールドを持ちます:

- `valid_to = null` → 有効
- `valid_to ≤ now()` → 置き換え済み(あらゆる検索で除外)

ファイルが変更されて再インデックスを行うと、インデクサはアトミックに:

1. そのファイルパスの既存 chunk すべてを scroll します。
2. 各々に `set_payload(valid_to = now())` を適用(従前の有効期間を閉じる)。
3. 内容ハッシュベースの UUID で `valid_to = null` の新 chunk を upsert します。

新旧の chunk は Qdrant 内に共存し、検索結果には新 chunk のみが返ります。`retention_days`
を超えた古い chunk は自動 GC スイープで削除されます。検索時フィルタは単一の場所で
構築され、すべてのバックエンド(`tuned_hybrid` の両 Prefetch アーム、`dense`、
`bm25`、`qdrant_find`、`dual_memory_search`)に継承されます。Qdrant の
`IsEmptyCondition` を使用します(`IsNullCondition` は使いません — 詳細は
[Qdrant#5342](https://github.com/qdrant/qdrant/issues/5342):`IsNull` は欠損フィールドに
マッチしません)。

`.supamem/config.toml` で設定:

```toml
[supamem.retrieval.temporal]
retention_days = 90          # 0 = 恒久保持(コンプライアンス / 監査用コレクション)
```

### トランスクリプト専用の recency 減衰(opt-in、既定 OFF)

コード、ADR、ドキュメントは「古くなる」ことはありません。しかしトランスクリプトはしばしば
古くなります — 旧サポートチャットの非推奨 API がエージェントを現在の対話から逸脱させます。
Phase 9 ではトランスクリプト chunk **のみ**に作用し、rerank 後に走り、コード / ADR /
ドキュメントには絶対に自動適用されない、乗法フロア型の任意 decay knob を提供します:

```toml
[supamem.retrieval.recency.per_source.transcript]
enabled        = true            # 既定 false
half_life_days = 14.0
alpha          = 0.7             # フロア:最古のトランスクリプトでも 0.7 倍の score を保つ
```

ロックされた既定値での例(`alpha = 0.7`、`half_life_days = 14`):

| Age (days) | Multiplier         |
|------------|--------------------|
| 0          | 1.000              |
| 7          | 0.924              |
| 14         | 0.850              |
| 28         | 0.775              |
| ∞          | 0.700 (floor at α) |

knob を切り替えてもコード / ADR / ドキュメントのランキングはバイト一致 — エンドツーエンド
のバイト同一性テストで検証されています(TEMP-03 受け入れ基準)。

参考:[Customers.ai recency-weighted scoring](https://customers.ai/recency-weighted-scoring)、
[Snowflake Cortex Search scoring docs](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/cortex-search-customize-scoring)。

### Doctor パネル

`supamem doctor` は Reranker と Subagent reachability の間に `Temporal validity`
パネルを追加し、live / superseded / awaiting_gc / future-dated のカウント、ソース別
内訳、コレクション内の最古/最新の `valid_from`、そして `retention_days` のプロビナンス
を表示します。構造的に読み取り専用で、doctor の終了コードを変えることはありません。

### マイグレーション

アップグレード後の最初の `supamem index` は、レガシーポイントに `valid_to=null` を
バックフィルします(マニフェストの予約キーで管理、以降の実行は冪等)。ランタイムの
`IsEmpty` フィルタと並行する、深層防御です。

> ⚠ **既定の retention は破壊的**:90 日を超える監査モードコレクションを持つ v0.2.x
> からのアップグレードユーザは要注意。`[supamem.retrieval.temporal] retention_days = 0`
> に設定すれば自動 GC を完全に無効化できます。

---

## 🔭 フィルタ付き retrieval バックエンド(v0.3.0a3+)

`filtered_dense` は、`tuned_hybrid` の上に `where` フィルタとヒットごとの preview 上限を
重ねた "scoped+capped" な retrieval バックエンドです。「指定したパス/room にスコープした
ランク済み結果を、preview を Qdrant から出る前に N 文字に切り詰めて返す」という挙動を
バックエンドレベルで強制したいときに使います。

```toml
[supamem.retrieval]
backend = "filtered_dense"

[supamem.retrieval.filtered_dense]
preview_chars = 240   # 既定 240;0 で truncation 完全無効
```

選び方は他のバックエンド(`tuned_hybrid`、`dense`、`bm25`)と同じです —
`supamem.retrieval` プラグイン entry-point から登録され、切り替えはコードを書かずに
config だけで完結します。MCP トランスポート層の上限(`mcp.caps.max_preview_chars`)は
バックエンド上限のさらに上に重ねて適用され、両方とも `0` で個別に無効化できます。

### `where` フィルタ — magic key

`dual_memory_search`(およびエイリアス `qdrant_find`)は
`where: dict[str, str | list[str]]` 引数を受け取り、Qdrant の payload フィルタに変換
されます。Phase 7 の `room` キーに加えて、新たに 2 つの magic key を認識します:

```python
# 1. path_prefix — 左端アンカーの厳密パスセグメント一致
dual_memory_search(query="auth flow", where={"path_prefix": "src/supamem/retrieval"})

# 複数 prefix の OR(Qdrant MatchAny)
dual_memory_search(
    query="rate limit",
    where={"path_prefix": ["src/supamem", "tests/test_filtered_dense.py"]},
)

# 2. valid_to: "now" — Phase 9 の常時オン時間条件の no-op エイリアス
dual_memory_search(query="session", where={"valid_to": "now"})
```

セマンティクス:

- **`path_prefix`** は `/` セグメント境界で左端アンカーされます。indexer は chunk ごとに
  `payload.path_prefixes: list[str]` を保存します(例:
  `src/supamem/retrieval/filters.py` →
  `["src", "src/supamem", "src/supamem/retrieval", "src/supamem/retrieval/filters.py"]`)。
  `path_prefix="src/supa"` は `src/supamem/...` に **一致しません** ——`"src/supa"` は
  保存されている prefix セグメントではなく、完全な `/` セグメント境界のみが一致します
  (ファイルシステムのパス意味論に従います)。
- **`valid_to: "now"`** は no-op エイリアスとして受理され、Phase 9 の常時オン時間条件を
  ドキュメント化します。それ以外の値は `ValueError` を投げます —— time-travel クエリは
  スコープ外です。コレクションに残す履歴 chunk を制御するには `retention_days` を
  使ってください。

`where` の複数キーは AND、同一キー内のリスト値は OR(`MatchAny`)です。

| キー | 意味 |
|------|------|
| `room` | Phase 7 — coding-path 分類器のファセット(`backend`、`frontend`、`tests` など)。文字列またはリスト。`supamem index` が chunk 単位で書き込みます。 |
| `path_prefix` | Phase 11 — `payload.path_prefixes` に対する left-anchored の path-segment 完全一致。文字列またはリスト。`supamem index` が chunk 単位で書き込みます。 |
| `valid_to` | Phase 9 — 常時オンの temporal 句のエイリアスとして `"now"` のみ受理。それ以外の値は `ValueError`。 |
| `session_id` | **bench 専用** — LongMemEval ingestion(`supamem.eval.longmemeval_ingest`)が書き込む pass-through キー。**`supamem index` は書き込みません。** Phase 14 の scoped bench パスが専用コレクション `supamem_eval_longmemeval_s` に対して使用します。詳細は [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md) を参照。 |
| `repo` | **bench 専用**(v0.3.0a5+)— `coderag` ingestion(`supamem.eval.coderag.ingest`)が書き込む pass-through キー。値:`"supamem"`、`"fastapi"`。**`supamem index` は書き込みません。** Phase 15 の三列レポート(`supamem_only` / `fastapi_only` / `combined`)が `supamem_eval_coderag` コレクションに対して使用します。詳細は [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) を参照。 |
| `axis` | **bench 専用**(v0.3.0a5+)— `coderag` ingestion が書き込む pass-through キー。値:`"code_fact"`、`"decision_rationale"`。**`supamem index` は書き込みません。** 軸ごとのメトリクス集計に使用。詳細は [ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) を参照。 |

### coderag(コード検索;Phase 15 — 新しい Phase 13 リリースゲート、v0.3.0a5+)

`supamem eval --suite coderag [--full] [--out PATH] [--peer mem0]` は、
2 リポジトリの決定論的コード検索 haystack(supamem 自身 +
[fastapi](https://github.com/fastapi/fastapi) 外部、両方とも commit-SHA
にピン留め)を、PR 履歴由来のクエリ(`code_fact` 軸)と ADR の
Problem/Why 節由来のクエリ(`decision_rationale` 軸;v1 コーパスピンでは
**supamem 専用** — fastapi にはその SHA で `docs/adr/` が無いため、
この軸では三列レポートが縮退し `fastapi_only=null`、
`combined=supamem_only` となります)で実行します。

`Recall@k`、`MRR`、`nDCG@10`、p50/p95 レイテンシを軸ごとに
**三列**(`supamem_only` / `fastapi_only` / `combined`)で出力し、
自己参照による循環性を一目で監査可能にします。

**実測 3 ラン ベースライン(v0.3.0a6+)。** ADR-0002 §7 のしきい値は、
21,235 チャンクのライブコーパスに対して 3 回連続で variance-gated
で実行した結果から導出されるようになりました — Phase 15 のオフライン
`< 0.005 ms` レイテンシと `1.000` recall セル(6 問の smoke
フィクスチャがほぼ完璧に再現する副産物)は除去されました。レイテンシ
p95 のハード上限は **D-LAT-01** に基づき
**500 ms → 5000 ms** に**ワンショット**で前向きに調整されました —
スライディング・スケールでは**ありません**。後続フェーズは厳格化または
維持はあっても、決して再緩和しません。ライブのしきい値表と明示的な
理由段落は
[ADR-0002 §7](docs/adr/0002-coderag-eval-philosophy.md#7-locked-numerical-floors-live-three-run-baseline-phase-16-e)
を参照。

**`--full` の auto-queries(v0.3.0a6+)。** フルモードのレコードは、
`auto_queries.extract_pr_queries()` + `extract_adr_queries()` が
populated コーパス manifest(遅延ビルドは
`corpus.ensure_populated_manifest` 経由)に対して抽出した結果から
構築され、6 問の smoke フィクスチャからは **生成されません**。
各レコードは `query_origin` フィールド
(`pr_title` / `adr_problem` / `adr_why`)と
`training_leakage_suspected` ブール値を保持します。Smoke フィクスチャは
従来通りデフォルトのオフラインパスを駆動し、動作は変わりません。

**リリースゲート。** `supamem eval --suite coderag --full` がライブ
ベースラインに対してリグレッションなし(ランキング指標 ≥ baseline − ε;
レイテンシ p95 ≤ baseline + ε **かつ** ≤ 5000 ms ハード上限)を
報告したとき、Phase 13 はリリースされます。ε はメトリクスごとに導出:
`ε_ranking = max(stddev, 0.005)`、`ε_latency = max(0.05 × mean, 5ms)`。

**mem0 peer ベースライン + ヘッドツーヘッド(v0.3.0a6+)。**
[mem0](https://github.com/mem0ai/mem0) は単一の標準デフォルト設定で
並行行として実行(チューニング行列なし)、ソース文書を **自分自身の**
Qdrant コレクション `supamem_eval_coderag_mem0` に取り込みます
(`supamem_eval_coderag` とは分離 — mem0 が自前のスキーマを持つため)。
参照点として報告するだけで、ゲートには関与しません。v0.3.0a6 では
axis × カラム × メトリクスごとに paired-bootstrap delta + 95% CI を
追加(符号規約 `mem0_vs_supamem` — delta が正なら peer の勝利)、
[ADR-0002 §8](docs/adr/0002-coderag-eval-philosophy.md#8-mem0-peer-comparison-d-peer-04-live-phase-16-e-head-to-head)
を参照。`pip install supamem[peers-mem0]` でインストール。

**AST チャンカー + HyDE 検索(オプトインプラグイン; v0.3.0a7+)。**
Phase 17 では **オプトイン** の検索スタックプラグイン 2 種に加え、
チャンクレベル recall メトリクス、Phase 16 baseline-3 に対する
ペア・ブートストラップ・アップリフト比較である ADR-0002 §9 を提供。
**0.3.x 系のデフォルトは据え置き。デフォルト切替は D-LAT-01 に従い
v0.4 にゲート。**

- `tree_sitter_code` チャンカープラグイン — `supamem.chunker`
  エントリポイント配下に `markdown_header` / `transcript` と並列に
  登録。`pip install supamem[ast-chunker]` でオプトイン。v1 は Python
  のみ。パース失敗時は `chunk_markdown` にフォールバックし、
  `err_console` に警告を出力。recall リフトは控えめだが、D-LAT-01
  レイテンシ上限内に収まる。
- `tuned_hybrid_hyde` 検索プラグイン — `supamem.retrieval` に登録。
  継承ではなくコンポジションで `TunedHybridBackend` をラップ(後者は
  バイト一致を維持)。localhost 限定 Ollama リライター
  (`/api/generate`、`keep_alive=-1` のウォームプール保持、600 ms
  タイムアウト + 1 回リトライ、失敗時は元クエリへフォールバック)。
  判定:Track B の `decision_rationale.supamem_only.recall_at_1 ≥ 0.5`
  目標をぎりぎり達成するが、**5 セル中 4 セルで 5000 ms p95 ハード
  上限を違反(最大 6069 ms)** し、`code_fact` で −0.25 の MRR
  リグレッションを発生させる。オプトインのみ;デフォルト切替の
  経路なし。Phase 18 のフォローアップ = axis 単位のセレクティビティ
  ゲーティング。
- チャンクレベル recall メトリクス + bench 専用 `payload.chunk_id` —
  doc レベルキーに `recall_at_*_chunk` を兄弟キーとして追加。新規の
  `_build_run_chunk` は重複 doc_id を **重複排除しない**(Phase 16 の
  `_build_run` は同一ファイルのチャンクを 1 行に畳み込み、シグナルを
  覆い隠していた)。doc レベル経路はバイト一致を維持 —— Phase 16
  floors テストは引き続きグリーン。
- Ollama ウォームプール doctor パネル —
  `retrieval.backend = "tuned_hybrid_hyde"` が設定されているときのみ
  表示。1 秒タイムアウトで `/api/ps` を探査。読み取り専用 —
  例外を投げず、exit code を反転させない(D-DOCTOR-04)。

ペア・ブートストラップ差分(default vs ast_on / hyde_on /
ast_plus_hyde)は [ADR-0002 §9](docs/adr/0002-coderag-eval-philosophy.md#9-phase-17-uplift-comparison)
を参照。注意事項:§9 の CI は `[delta, delta]` に収束する。v1 LIVE
envelope スキーマがセルごとの平均しか保持しないため — delta 値は
正確だが、CI の上下限はクエリレベルの不確実性を反映しない(将来の
envelope スキーマのバンプで真の CI を解放可能)。

**LongMemEval の降格。** v0.3.0a5 以降、完全な LongMemEval_S は
オンデマンドのみとなり、5 問の `longmemeval_scoped_smoke` フィクスチャは
PR-CI に残ります。診断:LongMemEval は会話的長期記憶を測りますが、
supamem は AI コーディングエージェントが利用するコードチャンクを
インデックスしているため、ゲートは **ワークロードと不整合** でした
(ツール側の問題ではない)。詳細は
[ADR-0002](docs/adr/0002-coderag-eval-philosophy.md) を参照。

### マイグレーション

レガシー chunk(v0.3.0a3 より前にインデックスされたもの)は `path_prefixes` を持ちません。
アップグレード後最初の `supamem index` が一回限りの scroll-and-`set_payload` パスを実行し、
chunk ごとに `path_prefixes` をバックフィルします —— 純粋なメタデータ更新で
**re-embedding コストはゼロ**、以降の実行は冪等。`--force` 再インデックスは **不要**です。

### Doctor パネル

`supamem doctor` に「Filtered-dense backend」パネルが追加され、解決された
`preview_chars` 値と `[source: ...]` 出典行を表示します。構造上 read-only;doctor の
exit code を変えることはありません。

---

## 📊 Benchmarks(v0.3.0a4+)

**方法論の変更。** `supamem eval --suite longmemeval_s` は質問ごとに
**unscoped** と **scoped** の両方の検索パスを発行します。scoped パスは
LongMemEval の haystack session id から導出した質問ごとの `where` フィルタ
(`{"session_id": [...]}`)を使用し、Phase 7 / 9 / 11 / 14 で追加された
indexer 側のフィルタ payload(`room`、`path_prefix`、`valid_to`、
`session_id`)をエンドツーエンドで動かします。公開 gate の判定
(`tokens_per_correct_answer` の v0.1.5 baseline に対する delta)は **scoped**
パスを読み、unscoped は同じ envelope に透明性のために載りますが gate には
入りません。詳細は [ADR-0001](docs/adr/0001-scoped-only-bench-gate.md) を参照。

**再現性に関する注意。** scoped の数値は `dual_memory_search` /
`qdrant_find` のデフォルト unscoped 呼び出しでは再現しない可能性があります。
比較可能な数値が欲しいユーザーは、対応する payload を持つ chunk を含む
コレクションに対して明示的に `where={...}` を渡す必要があります —— これは
方法論の開示であり欠陥ではありません。

**Baseline コーパス。** v0.1.5 の baseline は専用 bench コレクション
(`supamem_eval_longmemeval_s`)上で **再キャプチャ**されました。Phase 14
以前の絶対値は Phase 14 以降の値と直接比較できません —— コーパスが変わった
ためです。元の devdocs collection 由来の数値は
`eval/baselines/v0.1.5.json` に `legacy_devdocs_unscoped_tpca` として歴史
参照のために保存されますが、**gate には入りません**。

**FUTURE-24(rerank composition rework)** は別途追跡される姉妹アンブロッカー
です。Phase 14 の scoped パスは rerank-OFF で走るため、計測される
scoped と unscoped の差分は scoping にきれいに帰属します。scoping の
ゲインに関する公開クレームは「rerank composition も直れば gate がさらに
X% 縮まる」とは **外挿しません**。

**Smoke fixture。** 内蔵された静的 fixture
`src/supamem/eval/datasets/longmemeval_scoped_smoke.json`(≤5 問、≤200 KB、
self-contained)が新スイート名 `longmemeval_scoped_smoke` として公開され、
~3 GB の遅延フェッチをトリガせずに CI で実行できます。

---

## 🚫 supamem が **やらないこと**

`supamem` は **エージェント呼び出しに identity / wake-up / prelude コンテキストを自動
注入しません** —— retrieval は常に明示的なクエリで明示的に要求されます。隠された
「エージェント身元」層、SessionStart 時にモデルへ暗黙コンテキストを押し込む wake-up
ペイロード、`query` が空のときに retrieval を発火させる MCP tool —— いずれも存在しません。

これは 2 重に固定されています:

1. **Schema レベル(v0.3.0a3+):** すべての retrieval tool の `query` 引数は
   `Field(..., min_length=1, max_length=...)` —— 必須・非空で、tool 登録時に schema
   レベルで強制されます。空の `query` は構造化された MCP バリデーションエラーで明示的に
   拒否され、既定コンテキストで暗黙置換されることはありません。
2. **テストレベル(FILT-02):** `tests/test_no_identity_tier.py` は CI で強制される
   回帰テストで、登録された MCP tool 名が
   `(?i)(wake[_-]?up|identity|prelude|inject)` に一致した場合、または retrieval tool の
   JSON Schema が `query` を `required` から外す / `minLength >= 1` を失った場合に
   ビルドを失敗させます。

セッション開始時に supamem コンテキストをロードしたい場合は、既存の SessionStart バナー
hook がサポート対象です —— 1 行のステータス(コレクション、chunk 数、audit ログパス)
を注入するだけで、retrieval 結果をモデルに暗黙に流し込むことは決してしません。
モデルがコーパスを読むには `dual_memory_search` を呼ぶ必要があります。

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
