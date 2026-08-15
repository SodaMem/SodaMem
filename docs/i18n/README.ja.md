<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**AI エージェントのための、自己進化するエージェント型メモリ層。**

多くのメモリシステムは「何を言ったか」を保存して終わりです——今日は正しくても、状況が変わった瞬間に黙って古くなります。SodaMem はエージェントと一緒に進化します。事実は上書きされず後継に置き換わり、エンティティプロファイルは静かに古びていく代わりに必要なときに再構築され、どの答えもそれを生んだ発話まで辿れます。想起にかかる LLM 呼び出しはゼロ——同じ質問には毎回同じ答えが返ります。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

[エージェント連携](#エージェント連携) · [ベンチマーク](#ベンチマーク) · [クイックスタート](#クイックスタート) · [なぜもう一つメモリ層が要るのか](#なぜもう一つメモリ層が要るのか) · [インストール](#インストール) · [どこからでも使える](#どこからでも使える) · [コーディングツール](#コーディングツール) · [セルフホスト](#セルフホスト)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*縦軸は正確度、横軸は 1 問あたりの推定 API コスト。意味があるのは左上の象限です。*

</div>

---

## エージェント連携

| ランタイム | 方式 | ガイド |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **汎用 / 任意の MCP クライアント** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | Python アダプタ | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | Python アダプタ | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | Python アダプタ | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | TS アダプタ | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code、Cursor などのコーディングクライアント** | CLI + フック | [コーディングツール](#コーディングツール)を参照 |

MCP ツールのスキーマやアダプタの詳細を含む完全な索引：[`integrations/README.md`](../../integrations/README.md)。

---

## ベンチマーク

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

LongMemEval **92.8%（464/500）**。

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 判定プロンプト | LongMemEval 公式 `evaluate_qa.py` のテンプレート、バイト単位で同一 |
| ストア | `longmemeval_s_500_Hobs_entitysubj`、500 ユーザー / 235,840 ファクト |

**回答も、取得した記憶も、すべて公開しています**
（[`benchmarking/artifacts/`](../../benchmarking/artifacts/)）——500 件の回答
全文と 8,427 件の証拠。任意の judge で採点し直すことも、取得コンテキストを
自前の reader に渡して数字がどう動くか確かめることもできます。どちらも当方の
サービスへのアクセスは不要です。

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

LoCoMo **86.88%（1338/1540）**。エンドツーエンドの QA 正解率、
判定は LLM-as-judge。

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 判定プロンプト | LongMemEval 公式のテンプレート、バイト単位で複製 |
| ストア | `locomo10_Hobs`、10 ユーザーストア / 2,905 ファクトイベント |
| コード | プレリリースビルド —— 公開履歴は v0.1.0 から始まります |

**LoCoMo については問題ごとの成果物を一切公開していません** —— 回答も、取得
コンテキストも、run ディレクトリもありません。公開しているのは
[`benchmarking/README.md` の LoCoMo セクション](../../benchmarking/README.md#locomo-cat-1-4)
です。カテゴリ別の内訳、会話ごとの分布、provenance と再現手順が載っています。

---

## クイックスタート

ここでは Python の道筋を説明します。エージェントフレームワークや MCP クライアントへの組み込みは[エージェント連携](#エージェント連携)へ、TypeScript/Node から呼び出す場合は[どこからでも使える](#どこからでも使える)へ、共有サービスとして動かす場合は[セルフホスト](#セルフホスト)へどうぞ。

### 例

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY など
from sodamem.memory.ingest.extractor import FactEventExtractorV2

mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "やっぱりカウアイ島からオアフ島に変更しました。"}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("どこに泊まる予定？", user_id="u1", token_budget=1000)
print(block.text)        # そのままプロンプトに使える —— LLM 呼び出しゼロ
print(block.citations)   # その一行一行の根拠
```

`SodaMem.open()` は `./data` が無ければ作ります。extractor が要るのは
`.ingest()` だけで、省略すれば読み取り専用ストアになり、`search` /
`build_context` はまったく同じように動きます。

**あなたのデータは端末から出ません。** テレメトリなし、解析なし、コール
バックなし——デフォルト構成が行う唯一の外向き通信は、90MB の MiniLM 埋め込み
モデルを `~/.cache/chroma/` へ一度だけ取得することだけで、以後は
ディスクとしか通信しません。このキャッシュを先に置けば完全オフラインで動きます。

---

## なぜもう一つメモリ層が要るのか

多くのメモリシステムが保存するのは「何を言ったか」です。実際に破綻を招くのは
**それがいつ真でなくなったか**と**どこから来たか**——この二つはデータモデルで
解く問題であり、ベクトルインデックスを大きくしても解決しません。


| 問い | よくある答え | SodaMem |
|---|---|---|
| この記憶はどこから来たのか？ | 類似度スコアと、いくつかのメタデータ | `FactEvent → SourceSpan → RawTurn` の外部キー連鎖が、まさにその発話まで辿れる |
| ユーザーが前言を撤回したら？ | 上書きし、古い値は消える | 追加のみ、加えて `SUPERSEDES` エッジ。旧版は `valid_until` で閉じ、読める状態で残る |
| 「去年シカゴに引っ越した」と「来年引っ越す」 | タイムスタンプ一つ | 四つの時間軸：発生 / 有効 / 発話 / 保存 |
| 検索 1 回のコストは？ | 検索のたびに LLM 呼び出し | `build_context` はモデル呼び出し **ゼロ**、引用つきの完成プロンプトを返す |
| 同じクエリを二度投げたら同じ答えか？ | モデルのサンプリング次第 | 決定的な融合：同じストア、同じクエリ、同じ結果 |
| なぜ X を忘れたのか？ | 答えようがない | `/v1/events` が追加・上書き・削除をすべて理由つきで記録 |

このうち二つだけ、もう少し詳しく見る価値があります——残りは表がすでに答えています。

### すべての記憶が証拠を携える

取得された記憶は宙に浮いた文字列ではありません。それを生んだ発話を指します。

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "オアフ島で混みすぎないビーチを教えてもらえますか？"   ← ユーザーの発話そのまま
predicate    = ユーザーはオアフ島の空いているビーチを求めている
entities     = location=オアフ島 | occasion=誕生日
source       = session_40 / turn_10          ← 「どこかの会話」ではなく、その発話
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` は類似度スコアではなく、実際の外部キー連鎖
です。「なぜそう判断したのか」と問われれば答えがあり、監査で「この事実の出所
は」と問われれば該当行があります。

### タイムスタンプ一つではなく、四つの時間軸

| フィールド | 答える問い |
|---|---|
| `occurred_start` / `occurred_end` | 出来事が**起きた**のはいつか |
| `valid_from` / `valid_until` | その事実が**成り立っていた**期間 |
| `document_time` | ユーザーが**言った**のはいつか |
| `created_at` | こちらが**保存した**のはいつか |

タイムスタンプが一つでは「去年シカゴに**引っ越した**」と「来年シカゴに
**引っ越す**」を区別できず、すでに真でなくなった事実も表現できません。

---

## インストール

| extra | 追加されるもの |
|---|---|
| *(base)* | データモデル、ストレージ、BM25 検索、取り込み —— **依存は 4 つ、重いものはゼロ** |
| `chroma` | ベクトル検索＋ローカル ONNX 埋め込み（`SodaMem.open()` に必要） |
| `llm` | OpenAI 互換プロバイダ（OpenAI / DeepSeek / Gemini は同じ通信形式） |
| `anthropic` | Anthropic（専用 SDK） |
| `answer` | プランナー＋リーダーの回答経路 |
| `server` | HTTP サービス（FastAPI＋uvicorn、意図して 3 パッケージのみ） |
| `mcp` | MCP サーバー |

base は `pydantic`、`numpy`、`rank-bm25`、`python-dateutil` だけを引きます。
このリストがうっかり伸びたらビルドが落ちる CI ゲートを置いています。



---

## どこからでも使える

**HTTP** —— `add` / `search` / `context` / `answer`、さらに一括書き込み、
置換、イベント、メトリクス、トークン使用量：

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"何を好む？","token_budget":1000}'
```

`/v1/context` と `/v1/search` はどちらも JSON ボディを受けます。
`/v1/context` は純粋な読み取りなので、クエリパラメータでの GET も通ります。

**SDK** —— TypeScript は HTTP 経由（[`sdk-ts/`](../../sdk-ts/)、実行時依存
ゼロ、ESM + CJS）。Python はライブラリを直接使います——`import sodamem` の
時点で、すでにネットワークの内側です。

**エージェントフレームワーク** —— LangGraph、CrewAI、OpenAI Agents SDK、
Vercel AI SDK。スコープはツール構築時に束縛し、**モデルが見る schema には
一切出しません**。モデルが選べる `user_id` は、モデルが幻覚しうる `user_id`
だからです。

**MCP** —— 8 つのツール。`entity_timeline`（あるエンティティの履歴を時系列で、
各項目は出所を指したまま）と `explore_memory`（グラフを外へ辿る）を含みます。
うち 6 つは読み取りで常に利用可能。
データを変える 2 つ（`add_memories` / `delete_memory`）は
`SODAMEM_MCP_ALLOW_WRITE=true` のときだけ登録され、その 1 行は
`sodamem install` が生成するクライアント設定に書き込まれます。

**Web コンソール** —— テナントごとに記憶を閲覧・検査できます。イメージに同梱。

---

## コーディングツール

**ステップ 1.** デーモンを起動する —— ストアを所有する唯一のプロセス：

```
sodamem daemon ensure
```

**ステップ 2.** クライアントをそこに接続する：

```
sodamem install claude-code
```

どのクライアントも MCP ツールサーフェスは使えます。うち 4 つはさらに**フック**
を備えていて、モデルがツール呼び出しを決断しなくても記憶の想起・保持が
行われます——コーディングセッション中はファイルを読むのに忙しく、モデルは
たいていそれを決断しないからです。

フックにできることは環境ごとに異なります——フックの仕組み自体が違うからです。
各クライアントが実際にサポートするものは次の通りで、`sodamem clients` も
同じ内容を表示します。

| クライアント | 想起 | 保持 |
|---|---|---|
| Claude Code | プロンプトのたび | すべてのターン + セッション終了時 |
| GitHub Copilot CLI | プロンプトのたび | すべてのターン |
| Cursor | セッション開始時（プロジェクトの要約） | — |
| Codex CLI | セッション開始時（プロジェクトの要約） | — |
| Claude Desktop、VS Code、Windsurf、Zed、OpenCode | MCP ツールのみ | MCP ツールのみ |

Cursor の `beforeSubmitPrompt` はプロンプトを読めても何かを注入することは
できません（公式ドキュメントが挙げるのはそれができる 3 つのイベントで、
これはそこに含まれません）。Cursor も Codex もフックにトランスクリプトの
パスを渡さないため、保持フックが読めるものがそもそもありません。この 2 つは
セッション開始時にプロジェクトの要約を受け取り、`add_memories` ツール経由で
書き込みます。何もできないフックはインストールしません。

実行前に知っておくべきことが 3 つあります。

**デーモンは 1 つ、エディタは複数。** ユーザーごとのストアは WAL なしの
SQLite なので、開けるプロセスは常に 1 つだけです（ADR 0001 §2）。そのため
`install` は既定で、各クライアントに自前のプロセスを立ち上げさせるのではなく
稼働中のサービスを指すよう設定します——意図的にローカルストア
（`--local-store`）を選んだ場合は、2 つ目のクライアントは黙ってデータを
壊す代わりに起動を拒否します。

**記憶はリポジトリ単位でスコープされます。** `install` は git のルートから
`project_id` を導出します（`git worktree` は親リポジトリに解決されるので、
タスクごとにブランチを切っても記憶バンクが分かれるわけではありません）。
これは分離ではなく絞り込みです——プロジェクト外で伝えた内容もすべての
プロジェクトから見え続け、キーを外せば「あの別リポジトリでどう直したっけ」
にも答えられます。

**保持には抽出用の認証情報が要ります。** 想起は LLM 呼び出しゼロで、
認証情報なしで動きます。事実の保存はそうはいきません。`sodamem daemon ensure`
はこれを最初に伝えます——すべての書き込みを受け付けてからジョブが失敗する、
という順序にはしません。

```
sodamem install claude-code --dry-run      # 変更内容を表示するだけ
sodamem install cursor vscode zed          # まとめて複数指定
sodamem daemon status                      # 実際に応答しているのはどれか
```

既存の設定はマージされ、置き換わりません——他の MCP サーバーや他の設定、
手書きの TOML コメントもそのまま残ります。初めて書き込むファイルには必ず
`.sodamem-backup` が添えられます。

---

## セルフホスト

コマンド一つ：

```
cp .env.example .env      # SODAMEM_API_KEY を設定
docker compose up -d
```

**認証は既定で有効。** `docker-compose.yml` は `SODAMEM_AUTH_DISABLED` を
一切設定しません——`SODAMEM_API_KEY` が未設定だとサーバーは起動を拒否するため
（`server/settings.py` 参照）、うっかり無防備なまま公開される事故は起きません。
最初の `docker compose up` の前に `.env` でキーを設定してください。

**ワーカーは必ず 1 つ。** `--workers 1` はスループットの設定ではなく、正しさ
の制約です。ユーザーごとのストアは WAL なしの SQLite データベースで、2 つの
プロセスが同じユーザーのストアに書き込むと壊れます。同梱の `CMD` も明示的に
これを指定しており、サーバーはデータルートに対して起動時に排他ロックを
取得します——同じディレクトリを指す 2 つ目のプロセスは、データを黙って壊す
代わりに `data_root_locked` で起動を拒否します。水平スケールにはまず外部の
ジョブストアが必要です（`docs/adr/0001-control-plane-db.md`）。

運用に関する完全なリファレンス——API の呼び出し方、管理者向けエンドポイント、
メトリクス、メンテナンス、バックアップ、アップグレード——は
[`docs/self-hosting.md`](../self-hosting.md) にまとまっています（現時点では
英語版のみです）。

---

## ドキュメント

| | |
|---|---|
| [ベンチマーク手法](../../benchmarking/README.md) | ベンチマークの数字をどう出したか |

---

## 謝辞

本プロジェクトの土台となった初期の作業に貢献してくださった [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) に感謝します。

## ライセンス

Apache-2.0。[LICENSE](../../LICENSE) と [NOTICE](../../NOTICE) を参照。
