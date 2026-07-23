# Webwright

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

<p align="center">
  <img src="assets/webwright_logo.svg" alt="Webwright logo" width="320">
</p>

<p align="center"><b>コーディングモデルを最先端のブラウザーエージェントへ</b></p>

<p align="center">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/playwright-chromium-green" alt="Playwright">
  <img src="https://img.shields.io/badge/backends-OpenAI%20%7C%20Anthropic%20%7C%20OpenRouter-orange" alt="Backends">
  <img src="https://img.shields.io/badge/footprint-%E2%89%A4~1.5k%20LoC-brightgreen" alt="Footprint">
</p>

- 📝 **ブログ：** [Webwright：Web エージェントに必要なのはターミナルだけ](https://www.microsoft.com/en-us/research/articles/webwright-a-terminal-is-all-you-need-for-web-agents/)
- 🌐 **プロジェクトページ：** [microsoft.github.io/Webwright](https://microsoft.github.io/Webwright/)

Webwright は LLM にターミナルを提供し、複数のブラウザーセッションを起動してページを調査し、Web タスクを完了できるようにします。ページのスクリーンショットや状態は、必要なときにだけ取得して調査します。各 Web タスクは再実行可能な 1 つの Python スクリプト内でエンドツーエンドに完了するよう強制されます。つまり、Web エージェントの閲覧履歴は 1 つのコードファイルになります。マルチエージェントシステムも、グラフエンジンも、プラグイン層も、隠れたオーケストレーションもありません——あるのはターミナル、ブラウザー、モデルだけです。

お気に入りのエージェントをすでに使っていて、Claude Code、Codex、Hermes、OpenClaw のブラウザータスク能力をさらに高めたい場合は、[Webwright のプラグイン／スキル](#-プラグインとして使用)を追加してみてください。

---

## 📰 最新情報

- **2026-05-11** — Task2UI モードをサポート：Webwright がタスクを完了し、その結果を閲覧・再利用しやすい HTML ベースの Web アプリとしてレンダリングします。
- **2026-05-06** — Codex と Claude Code のプラグインマニフェストを追加。`/plugin install webwright@webwright` でインストールできます。OpenClaw と Hermes Agent の統合も公開され、同じ `skills/webwright/` フォルダーを Claude Code、Codex、OpenClaw、Hermes で読み込めるようになりました。
- **2026-05-04** — 初回公開リリース：約 1.5k LoC、OpenAI / Anthropic / OpenRouter バックエンド、Playwright 環境。

---

<details>
<summary><strong>💡 動機：ステートフルなブラウザーでの逐次的な Web 操作を超えて</strong></summary>

現在の Web エージェントの多くは、ブラウザーセッション自体をワークスペースとして扱います。各ステップでモデルが現在のページ状態を受け取り、クリック、入力、DOM セレクター、短いツール呼び出しといった次の 1 操作を予測します。形式を問わず、エージェントは事前定義された操作ループの中で、Web 操作を 1 つずつ予測することに縛られます。この仕組みは LLM がまだ弱かった時期には有用でした。しかし、モデルのコード作成・デバッグ能力が向上するにつれ、同じ仕組みがボトルネックになります。

Webwright は異なる立場を取ります。**エージェントをブラウザーから分離**し、ブラウザーを、プログラムを開発するエージェントが起動・調査・破棄できるものとして扱います。永続的な成果物はブラウザーセッションではなく、**ローカルワークスペース内のコードとログ**です。

- 🧱 **Web 環境との堅牢で再利用可能な操作** — ターミナルを持つコーディングエージェントは、壊れやすいピクセル単位の操作ではなく、要素の照会、条件待ち、遅延読み込みや再レンダリングなどの動的な挙動への対応を行います。生成されたスクリプトは、毎回一から操作を発見し直すことなく、再実行、調整、タスク間での共有が可能です。
- ⚡ **複雑なワークフローの効率的な合成** — 日付の選択やフォーム入力のような複数ステップの操作を、コンパクトなプログラムにできます。ループ、関数、抽象化により、同じ低レベルの手順を繰り返し予測せず、異なる日付などの類似タスクへ一般化できます。操作ラウンドが減り、実行が速くなり、長い処理でのエラー蓄積も抑えられます。
- 🧪 **ブラウザーではなくワークスペースを状態にする** — エージェントは探索用スクリプトを書き、新しいブラウザーセッションを起動し、スクリーンショットを取得して障害を調査するタイミングを自ら判断できます。人間のエンジニアが RPA スクリプトを反復改善するのと同じです。
- 🪄 **最小構成でも驚くほど効果的** — この簡素な構成は、複雑なタスク、とりわけ長時間にわたる Web タスクを適切に処理します（[パフォーマンス](#-パフォーマンス)を参照）。

</details>

---

<details>
<summary><strong>🌟 Webwright を選ぶ理由</strong></summary>

多くの Web エージェントフレームワークは、実際のエージェントループを何層もの抽象化の下に隠します。Webwright は逆の方針を取ります。

- 🪶 **軽量な設計** — コアのエージェントループは約 450 行の単一ファイル、Playwright 環境は約 570 行、CLI は約 150 行です。
- 🧩 **交換可能なモデルバックエンド** — OpenAI、Anthropic、OpenRouter はそれぞれ約 150～200 行です。
- 🔍 **隠れたフレームワークなし** — `httpx`、`pydantic`、`playwright`、`typer` だけを使用します。
- 🔁 **フラットなプロンプト → 観察 → スクリプト実行ループ** — エンドツーエンドで読みやすく、デバッグやフォークが容易です。
- 🧪 **実行成果物を重視** — すべての実行で軌跡とスクリーンショットをディスクに保存し、調査できるようにします。

重量級プラットフォームではなく、最小限でデバッグしやすいブラウザー利用エージェントの出発点を求めるなら、Webwright が適しています。

</details>

---

<details>
<summary><strong>🆚 Webwright と他のブラウザーエージェントリポジトリの違い</strong></summary>

アーキテクチャレベルでの違いは次のとおりです。

|                     | **Stagehand (Browserbase)**                                  | **agent-browser (Vercel)**                                                | **browser-use**                                       | **Webwright**                                                       |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------- |
| **パラダイム**        | ハイブリッド：コード + 自然言語プリミティブ（`act` / `extract` / `agent`）   | *別の*エージェント（Claude Code、Codex など）が呼び出す CLI ツール            | DOM/AX スナップショット上の自律 LLM エージェントループ       | **ターミナルを持つコーディングエージェント**。ブラウザーは起動する環境にすぎない |
| **操作空間**    | Playwright コード、または自然言語 → LLM が変換した Playwright           | 個別のサブコマンド（`open`、`click @e2`、`snapshot`、`eval`）            | LLM が選択するインデックス付きクリック／入力操作        | **自由形式の Python（Playwright スクリプトを自ら作成）**                       |
| **「状態」とは？**| ブラウザーセッション                                          | ブラウザーセッション（CLI 呼び出し間はデーモンが保持）                     | ブラウザーセッション                                   | **ローカルワークスペース——コード、スクリーンショット、ログ。** ブラウザーは破棄可能 |
| **ループの形**      | 命令型。必要に応じて `agent()` が複数ステップを実行            | マイクロステップごとに 1 回の CLI 呼び出し                                         | 観察 → 次の操作を予測 → 実行 → 繰り返し      | コード作成 → 実行 → スクリーンショット調査 → 修正（コードが操作）      |
</details>


---

## 🎥 デモ
https://github.com/user-attachments/assets/4ed94cd5-11be-4daa-b2d7-1260a803baca

---

## 📊 パフォーマンス

100 ステップの予算で、2 つの実在 Web サイトベンチマークにおいて最先端の結果を達成しています。詳細は[ブログ記事](https://www.microsoft.com/en-us/research/articles/webwright-a-terminal-is-all-you-need-for-web-agents/)を参照してください。

- 🏆 **Online-Mind2Web（300 タスク）：** GPT-5.4 で **86.7%**——AutoEval カテゴリのオープンソースハーネスで最高値です。Claude Opus 4.7 は **84.7%** で、ハード分割ではより高い性能を示します（N=100 で **80.5%**、GPT-5.4 は 76.6%）。
- 🚀 **Odysseys（200 の長時間タスク）：** GPT-5.4 で **60.1%**（平均 76.1 ステップ）——従来の SOTA（Opus 4.6、44.5%、視覚ベースの手法と永続ブラウザーを使用）を **15.6 ポイント**、ベースの GPT-5.4（xy 座標予測と永続ブラウザーを使用して 33.5%）を **26.6 ポイント**上回ります。
- 🧠 **コードによる操作は座標予測を上回る：** Webwright は、再現した GPT-5.4 のスクリーンショット + xy 座標ベースラインを、すべての難易度分割で大幅に上回ります。
- 🧰 **小規模モデル + 再利用可能なツール：** 生成されたスクリプトはパラメーター化 CLI ツールとしてパッケージ化できます。5 個以上のツールを利用できる Online-Mind2Web サイトでも、**Qwen-3.5-9B** がタスクを適切に完了します。

<p align="center">
  <img src="assets/odysseys_eval_step100.png" alt="Odysseys long-horizon eval @ 100 steps" width="49%">
  <img src="assets/om2w_autoeval_step100.png" alt="Online-Mind2Web AutoEval @ 100 steps" width="49%">
</p>

---

## 🗺️ プロジェクト構成

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

## 📰 タスクショーケース（ダッシュボードとしての再現可能な実行）

[`assets/task_showcase/`](assets/task_showcase/README.md) にある小さな Flask アプリは、**再現可能な** Odyssey タスク（セール、在庫、商品一覧、求人サイト、天気など）の Webwright 実行を 1 つのダッシュボードにまとめます。各タスクに含まれるのは、`task.json`（メタデータ）と `report.json`（整理された構造化出力：情報源と、表・リスト・要約などの結果セクション）の 2 ファイルだけです。テンプレートがこれらを汎用的にレンダリングするため、新しいタスクは `assets/task_showcase/tasks/` にフォルダーを追加するだけで作成できます。

```bash
pip install flask
python assets/task_showcase/app.py    # http://127.0.0.1:5005
```

実行時に Webwright からレンダリング可能なタスクフォルダーを生成するには、Task Showcase オーバーレイを重ねます。

```bash
python -m webwright.run.cli \
    -c base.yaml -c model_openai.yaml -c task_showcase.yaml \
    -t "<repeatable web task>" \
    --task-id my_repeatable_task \
    -o outputs/default
```

> **注：** `report.json` は `-c task_showcase.yaml` を指定した場合にのみ生成されます。`base.yaml` だけの実行では `trajectory.json` とデバッグ成果物が生成されますが、`report.json` は生成されません。

実行すると、出力ワークスペース内の `task_showcase/tasks/<short_id>/task.json` と `report.json` に書き込まれます。生成されたファイルをリポジトリへコピーし直すことなくレンダリングできます。

```bash
python assets/task_showcase/app.py \
    --tasks-dir outputs/default/<run>/task_showcase/tasks
```

---

## 🚀 クイックスタート

### 前提条件

- Python 3.10+
- Playwright 経由でインストールした Chromium
- 選択したバックエンド（OpenAI、Anthropic、OpenRouter）の API キー

### インストール

```bash
pip install -e .
playwright install chromium
```

### 実行

設定したバックエンドの認証情報をエクスポートします（例：`model_openai.yaml` では `OPENAI_API_KEY`、`model_claude.yaml` では `ANTHROPIC_API_KEY`）。`image_qa` と `self_reflection` ツールは、デフォルトで同じ設定済みモデルを使用するため、Anthropic での実行に OpenAI キーは不要です。その後、次を実行します。

```bash
python -m webwright.run.cli \
    -c base.yaml -c model_openai.yaml \
    -t "Search for flights from SEA to JFK on 2026-08-15 to 2026-08-20" \
    --start-url https://www.google.com/flights \
    --task-id demo_openai \
    -o outputs/default
```

### 🚩 フラグ

| フラグ | 説明 |
|------|-------------|
| `-c` | `src/webwright/config/` の設定ファイル（複数指定可）。 |
| `-t` | タスク指示。 |
| `--start-url` | 開始ページ。 |
| `--task-id` | 出力サブフォルダー名。 |
| `-o` | 出力ディレクトリ。 |

---

## 🔌 プラグインとして使用

Webwright には、[Claude Code](https://docs.claude.com/en/docs/claude-code/plugins)（[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)）と [OpenAI Codex](https://developers.openai.com/codex/plugins)（[`.codex-plugin/plugin.json`](.codex-plugin/plugin.json)）の両方に対応するプラグインマニフェストが含まれています。共通スキルは [`skills/webwright/`](skills/webwright/)、スラッシュコマンドは [`skills/webwright/commands/`](skills/webwright/commands/) にあります。ホストエージェントが Webwright ループをネイティブに駆動するため、ホストのサブスクリプション以外に追加の LLM API キーや費用は必要ありません。PNG スクリーンショットをネイティブに読み取れるホストは、`image_qa` / `self_reflection` ツールを省略します。

共通のランタイム依存関係（どちらの方法でも一度だけインストール）：

```bash
pip install -e .
playwright install chromium
```

<details>
<summary><b>Claude Code</b></summary>

### インストール

Claude Code の組み込み marketplace からインストールします。

```text
# 1. Add this repo as a Claude Code plugin marketplace
/plugin marketplace add microsoft/Webwright

# 2. Install the plugin from that marketplace
/plugin install webwright@webwright
```

ローカルチェックアウトを使う場合は、marketplace コマンドにクローンしたリポジトリを指定します。

```text
/plugin marketplace add /absolute/path/to/Webwright
/plugin install webwright@webwright
```

### 使用方法

インストール後は**新しい Claude Code セッションを開始**してください。プラグインはセッション開始時に読み込まれるため、再起動するまで表示されません。

Claude Code に自然言語で依頼する（説明に基づいてスキルが自動有効化される）か、次のスラッシュコマンドのいずれかを使用できます。

```
/webwright:run search Google Flights for flights from SEA to JFK on 2026-08-15 to 2026-08-20
/webwright:craft search a ticket on Google Flights from LAX to SFO depart June 7 return June 14
```

- `/webwright:run`（または通常のプロンプト）は、タスクの具体的な値を使った 1 回限りの `final_script.py` を生成します。
- `/webwright:craft` は再利用可能な CLI ツールを生成します。`final_script.py` は、Google スタイルの `Args:` docstring と `argparse` ラッパーを備えたパラメーター化関数になり、各フラグのデフォルト値にはタスクの具体的な値が使われます。そのため、後で別の引数を指定して再実行できます。例：`python final_script.py --origin JFK --destination LAX --depart-date 2026-07-01`。

どちらのモードでも、Claude Code は `plan.md` を含むワークスペースを作成し、`final_runs/run_<id>/` で計測付き Playwright スクリプトを実行し、保存したスクリーンショットを使って各重要ポイントを視覚的に自己検証します。

</details>

<details>
<summary><b>OpenAI Codex</b></summary>

### インストール

Codex は Claude 形式の marketplace を読み込めるため、同じリポジトリを Codex プラグイン marketplace として利用できます。Codex CLI から次を実行します。

```bash
# 1. Add this repo as a Codex plugin marketplace
codex plugin marketplace add microsoft/Webwright

# 2. Open the plugin browser and install Webwright
codex
/plugins
```

ローカルチェックアウトを使う場合：

```bash
codex plugin marketplace add /absolute/path/to/Webwright
```

その後 Codex を再起動し、新しい marketplace とプラグインを読み込ませます。

### 使用方法

新しい Codex タスクでは、自然言語で依頼する（説明に基づいてスキルが自動有効化される）か、同梱スキルを `@webwright` で明示的に呼び出します。

```
@webwright search Google Flights for flights from SEA to JFK on 2026-08-15 to 2026-08-20
```

Codex は `plan.md` を含むワークスペースを作成し、`final_runs/run_<id>/` で計測付き Playwright スクリプトを実行し、保存したスクリーンショットを使って各重要ポイントを視覚的に自己検証します。

アンインストールせずにプラグインを無効化するには、`~/.codex/config.toml` 内のエントリーを `enabled = false` に設定して Codex を再起動します。

</details>

<details>
<summary><b>🦞 OpenClaw</b></summary>

### インストール

ローカルチェックアウトから直接インストールします（パス、アーカイブ、npm spec、git リポジトリ、`clawhub:` spec に対応）。

```bash
openclaw plugins install /absolute/path/to/Webwright
openclaw gateway restart   # reload so the plugin and skill are picked up
```

確認：

```bash
openclaw plugins list | grep webwright
openclaw skills  list | grep webwright   # should show "✓ ready"
```

### 使用方法

これで、どの OpenClaw エージェント画面（CLI、Telegram など）からでも `webwright` スキルを利用できます。自然言語でエージェントに依頼するか、[`skills/webwright/commands/`](skills/webwright/commands/) に含まれるスラッシュコマンド（例：`/webwright run <task>`）を使用します。

アンインストール：`openclaw plugins uninstall webwright`。

</details>

<details>
<summary><b>Hermes Agent</b></summary>

### インストール

[Hermes Agent](https://github.com/NousResearch/hermes-agent) は [skills 互換クライアント](https://agentskills.io)であるため、同じ `skills/webwright/` フォルダーを Hermes スキルとして読み込めます。Hermes のユーザースキルディレクトリへシンボリックリンクを作成します。

```bash
mkdir -p ~/.hermes/skills
ln -sfn /absolute/path/to/Webwright/skills/webwright ~/.hermes/skills/webwright
```

Hermes 専用のマニフェストは不要で、`SKILL.md` だけが読み込まれます。

### 使用方法

Hermes（`hermes`）を起動し、自然言語で Web タスクの操作を依頼します。説明に基づいてスキルが自動有効化されます。`/webwright` で明示的に呼び出すこともできます。

[`skills/webwright/commands/`](skills/webwright/commands/) にある名前付きサブコマンド（`/webwright:run`、`/webwright:craft`）は Claude Code / Codex の規約であり、Hermes では機能しません。スキル自体は引き続きエンドツーエンドで動作します。

</details>

## 📃 軌跡の比較とビューアー

Webwright ハーネスと、その Codex / GitHub Copilot スキル版で同じタスクを実行し、異なるハーネス間で token 使用量と軌跡を比較できます。軌跡ビューアーは Codex、GitHub Copilot、Webwright ハーネスのトレースに対応しています。

![Trajectory comparison](assets/trajectory-compare.png)

### 使用方法

```bash
cd assets/compare_trajectory/
python3 -m http.server
```

ブラウザーで Web ページを開き、Webwright の `raw_responses.jsonl` をアップロードし、`trajectory.json` を添付して表示します。反対側には Codex または GitHub Copilot のトレースをアップロードできます。

### Codex トレースの取得：

```
ls ~/.codex/sessions/2026/MONTH/DAY/SESSION_ID.jsonl
```

### GitHub Copilot トレースの取得：

```
/export file session
-> session.md is the uploadable trace
```

### 簡易比較

#### 「2005～2015 年製、価格 25,000～50,000 ドル、走行距離 50,000 マイル以下の中古 8 気筒 BMW の中から、最も安いものを探す。」

| Token | Webwright ハーネス（ローカルブラウザーモード） | Codex Webwright スキル |
| --- | ---: | ---: |
| 入力 | 420,433 | 3,271,143 |
| 出力 | 3,593 | 20,040 |
| 推論 | 0 | 4,410 |
| キャッシュ | 217,216 | 3,081,3440 |
| 合計 | 424,026 | 3,291,183 |

個々の実行と結果は異なる場合があります。

---

## クレジット

- [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent/tree/main) — 最小限のエージェントループの設計に着想を得ました。
- [Playwright](https://playwright.dev/) — ブラウザー自動化。

## 引用

研究で Webwright を使用する場合、または Webwright を基に開発する場合は、このリポジトリを引用してください。

```bibtex
@misc{webwright2026,
  title        = {Webwright: A terminal is all you need for web agents},
  author       = {Lu, Yadong and Xu, Lingrui and Huang, Chao and Awadallah, Ahmed},
  year         = {2026},
  howpublished = {\url{https://github.com/microsoft/Webwright}},
  note         = {GitHub repository}
}
```
