<div align="center">

# SodaMem

**AI エージェントのための、証拠を辿れる時間軸つきメモリ層。**

すべての記憶が「どの発話から生まれたか」を示し、「いつ真でなくなったか」を知っています。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval--S-93.6%25-brightgreen.svg)](../../benchmarking/protocol_v1.0/)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

</div>

---

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY
from sodamem.memory.ingest.extractor import FactEventExtractorV2

# 書き込みには事実を抽出するモデルが要る。読み出しには一切不要。
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

訂正は **ADD-only** です。新しいバージョンと `SUPERSEDES` エッジを足すだけで、
その場で書き換えません。`PATCH /v1/memories/{id}` は旧バージョンに
`valid_until` を付けて閉じ、**読める状態のまま残します**——ここが `DELETE`
との決定的な違いです。

### 二段の検索、しかも安い方が本当に無料

| 段 | LLM 呼び出し | 用途 |
|---|---|---|
| `search` / `build_context` | **ゼロ** | 既定の経路：BM25＋ベクトル＋エンティティの決定的融合 |
| `answer` | プランナーループ | トークンを払う価値のある多段推論 |

`build_context` は**引用つきで、そのままプロンプトに入るテキスト**を返し、
モデルを一度も呼びません。多くのシステムはレコードの一覧を返すだけで、
組み立てもトークン配分も重複除去も利用者任せです。

中間の第三の段もあります：`build_context(organizer=...)` は検索結果の上で
LLM オーガナイザ（value-board / enumeration-sweep）を走らせ、「知っている X を
全部挙げて」のような問いに答えます。意図的に Python 側だけの機能です——
`/v1/context` は organizer を決して受け取らないので、あのルートのゼロ LLM
保証をリクエストパラメータでひっくり返すことはできません。

### 検索結果は監査できる

同じクエリ、同じストアなら結果は毎回同じです。`/v1/events` が追加・置換・削除
とその理由をすべて記録するので、「なぜエージェントは X を忘れたのか」は後から
辿れます。

---

## ベンチマーク

LongMemEval-S **93.6%（468/500）** — **Typed Answer Schema（TAS）** headline。公開 artifact は **92.8%（464/500）**。

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


PyPI にはまだありません。最初のタグ公開まではソースから：

```bash
pip install "git+https://github.com/xlows1206/SodaMem#egg=sodamem[chroma,llm]"
```

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

## セルフホスト

```bash
cp .env.example .env      # SODAMEM_API_KEY を設定
docker compose up -d
```

認証は既定で有効。テナント分離は**物理的**です——`user_id` ごとに独立した
SQLite ファイルとベクトルコレクションを持つため、「このユーザーを削除する」は
ディレクトリを一つ消すことです。

`/v1/admin/*` は本来コンテナに入らないと見えない情報を返します：実効設定
（秘密情報は「設定済み／未設定」のみで、値は決して出力しない）、名前付き
API キー、直近のリクエストログ、ディスクと負荷の状況。

可観測性：`/v1/metrics`（レイテンシ分位）、`/v1/usage`（取り込みと回答に分けた
トークン消費）、`/metrics`（Prometheus 形式）、`/v1/events`（記憶の全変更）、
および送信 webhook（上限つきキュー、HMAC 署名、URL 未設定なら完全に無効）。

エンティティプロファイルの再構築はオンデマンドで、タイマーではありません：
`POST /v1/maintenance/dream`（冪等・再開可能・同時呼び出しは
`already_running` を返す）。そのトークンをいつ使うかは配備側の判断なので、
SodaMem 自身はスケジューラを持ちません。

詳細は英語版 [Self-hosting](../../README.md#self-hosting) を参照してください。

---

## ドキュメント

| | |
|---|---|
| [コーディングツール連携](../../README.md#coding-tools) | Claude Code、Cursor などの MCP クライアント |
| [ベンチマーク手法](../../benchmarking/README.md) | LongMemEval の数字をどう出したか |

---

## ライセンス

Apache-2.0。[LICENSE](../../LICENSE) と [NOTICE](../../NOTICE) を参照。
