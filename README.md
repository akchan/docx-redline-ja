# docx-redline-ja

Word文書（.docx）に、校正の結果をWordの変更履歴として書き込むスキル。
Claude Codeのプラグインとして配布している。

入力は「どの文字列を」「何に直すか」を1行1件で並べたJSONL（以下、findings）で、出力は変更履歴の入った別のdocxである。
差分は文字単位で取るので、変更履歴に残るのは実際に変わった文字だけになる。
これは日本語の文書で効く。
単語の境界を空白で見つける方式では、1文字直すだけで文節や文がまるごと削除と挿入になるのである。

英語版は[README_en.md](README_en.md)にある。

## できること

- findingsの`old_text`/`new_text`の組を、原稿docxの別コピーにWordの変更履歴（`w:ins`/`w:del`）として書き込む。原稿とfindingsは書き換えない。
- 差分を文字単位で最小化する。
- 変更履歴の著者名と日時を指定できる。日時を固定すれば、同じ入力から同じバイト列の出力が得られる。
- 上付き・下付きを`<sup>`/`<sub>`/`<plain>`で指定できる。文献番号や単位（mm<sup>3</sup>など）の修正に使う。
- 用語の統一のように一致した全箇所を直す指摘（`replace_all`）と、本文と文献一覧で方針が違う指摘（`scope`で段落範囲を限る）を扱える。
- 原稿に他人の変更履歴が入っていても、それを壊さず重ねて書く。
- 出力を書く前に、変更履歴をすべて却下すると原稿に戻ること（`reversible`）、他人の変更履歴が変わっていないこと（`other_revisions_preserved`）など5項目を検証し、1つでも落ちればdocxを書かない。
- 原稿を書き換えずに、語の位置を調べる`locate`と、段落の本文を読む`read`が付いている。
- 依存はPython 3.11以降と`lxml`だけで、スキルのディレクトリをどこに置いても動く。

## できないこと

- 校正そのものはしない。何をどう直すかを決めるのは、Claude Codeなどのエージェントか人の仕事で、このスキルはその結果をdocxに反映するだけである。
- 編集するのは本文（`word/document.xml`）の段落だけで、表のセルとテキストボックスの中は含む。コメント、ヘッダー・フッター、脚注は対象外。
- `old_text`は1段落内に収まるものだけを扱う。段落をまたぐ修正や、段落の結合や分割はできない。
- 書式のうち扱うのは上付き・下付きだけで、太字やフォントの変更は書けない。
- 一致範囲がタブ、画像、フィールド、脚注番号などに掛かる修正は適用しない（`spans_inline_object`としてskipする）。
- 指摘どうしの編集範囲が重なると、どちらも適用せずに`conflict`として返す。どちらかを優先することはしない。

## 導入

### Claude Code

このリポジトリはClaude Codeのマーケットプレイスの形になっている（`.claude-plugin/marketplace.json`）。
マーケットプレイス名とプラグイン名はどちらも`docx-redline-ja`である。

```
/plugin marketplace add akchan/docx-redline-ja
/plugin install docx-redline-ja@docx-redline-ja
```

インストールせずに試すなら、cloneしたディレクトリを`--plugin-dir`で渡す。

```bash
claude --plugin-dir /path/to/docx-redline-ja/plugins/docx-redline-ja
```

### そのほかのエージェント

スキルの実体は`plugins/docx-redline-ja/skills/docx-redline-ja/`の1ディレクトリで、`SKILL.md`、`references/`、`scripts/`からなる。
Codex、Claude Desktop（Cowork）など、`SKILL.md`形式のスキルを読めるエージェントなら、このディレクトリをそのスキル置き場にコピーすれば使える。
絶対パスや環境変数への依存が無いことはテスト（`tests/test_decoupling.py`）で確かめている。
各エージェントでのスキル置き場の指定方法は、それぞれのドキュメントを見てほしい。

Pythonから直接使うこともできる。

```bash
pip install -r requirements.txt   # lxml>=5.0
```

## 使い方

Claude Codeからは、原稿docxとfindingsを渡して「変更履歴として反映して」と頼めばスキルが呼ばれる。
以下は、スキルの中で何が起きているかと、手で実行するときの手順である。
`<skill_dir>`は`SKILL.md`があるディレクトリを指す。

### findingsを書く

UTF-8のJSON Linesで、1行に1件の指摘を書く。
空行と`#`で始まる行は無視する。

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
{"old_text":"FDG-PET/CTでは","new_text":"<sup>18</sup>F-FDG-PET/CTでは"}
{"old_text":"MRI検査","new_text":"MRI 検査","replace_all":true}
{"old_text":"不要な語句","new_text":""}
{"old_text":"、","new_text":"，","replace_all":true,"scope":"p0-119"}
```

- `old_text`：必須。原稿の変更履歴をすべて承諾した状態の本文と照合する。1段落内に収まり、（`scope`の範囲内で）一意であること。
- `new_text`：必須。置換後の文字列。空文字列なら削除になる。`<sup>`、`<sub>`、`<plain>`だけをタグとして解釈し、タグの無い文字は原稿の上付き・下付きを引き継ぐ。
- `replace_all`：`true`なら一致した全箇所に適用する。既定は`false`。
- `scope`：照合する段落の範囲。`"p12"`、`"p10-12"`、`"p3,p10-12"`、`"p120-"`のように書く。段落番号は0始まり。

これ以外のキー（`id`、`severity`など）があってもレコードは有効で、解釈されないまま結果に名前だけが出る。
キー名の打ち間違いはここで分かる。
細則は[findings_schema.md](plugins/docx-redline-ja/skills/docx-redline-ja/references/findings_schema.md)にある。

### 適用する

```bash
python3 <skill_dir>/scripts/apply_redline.py \
  --docx 原稿.docx --findings findings.jsonl --out 校正済み.docx --author "Claude Opus 5.5"
```

- `--author`：変更履歴の著者名。Wordの校閲ペインに出る。スキルから呼ぶときは、エージェントが自分のモデル名を渡す。省略すると`AI`になる。
- `--date`：変更履歴の日時（ISO 8601）。省略すると現在時刻（UTC）になる。固定すると出力が再現可能になる。

`--out`は`.docx`で終わる必要があり、`--docx`、`--findings`、`--out`、レポートのうち2つが同じファイルを指すと何も書かずに終わる。
macOSのように大小文字を区別しないファイルシステムでの`a.docx`と`A.docx`も同じファイルとみなす。

終了コードは3つで、`0`は検証が通ってdocxを書いた、`1`は検証が落ちてdocxを書かなかった、`2`は入力の不備で起動時に止まった、を表す。
skipされたレコードがあっても、検証が通れば`0`で終わる。
何が落ちたかは次のレポートで見る。

### 結果を読む

レポートは`--out`の拡張子を`.result.json`に変えたパスに書かれ、stdoutにはその要約が出る。
要約には件数、検証結果、注記と、skipされたレコードだけが含まれる。

```jsonc
{"out": "…", "result": "…/校正済み.result.json",
 "counts": {"records": 22, "applied": 13, "skipped": 9, "hunks": 19, "by_reason": {"ambiguous": 1, "conflict": 2}},
 "verification": {"reversible": true, "insertions_independent": true,
   "other_revisions_preserved": true, "revisions_wellformed": true, "media_preserved": true},
 "notes": ["…"],
 "records": [/* skipしたレコードだけ。reason / detail / occurrences */]}
```

skip理由は8つ（`invalid_record`、`crosses_paragraph`、`no_change`、`not_found`、`ambiguous`、`spans_inline_object`、`conflict`、`unsplittable_container`）で、各レコードの`detail`に説明が付く。
よく出るのは次の3つである。

- `ambiguous`：2箇所以上に一致した。`occurrences`に前後の文脈が付くので、`old_text`を伸ばすか、`scope`で段落を絞るか、全箇所を同じに直す指摘なら`replace_all`を付ける。
- `conflict`：他のレコードと編集範囲が重なった。重なる指摘を1レコードにまとめる。
- `not_found`：一致が無い。全角・半角、約物、空白の違いを疑う。

### 位置を調べる

`locate`と`read`は原稿を読むだけで、書き換えない。
段落番号は`scope`、`locate`、`read`、レポートのすべてで共通である。

```bash
python3 <skill_dir>/scripts/apply_redline.py locate --docx 原稿.docx --query "FDG-PET" --no-matches
python3 <skill_dir>/scripts/apply_redline.py read --docx 原稿.docx --paragraphs "p10-20" --text
```

`locate`は一致の数、一致した段落の一覧、そのまま`scope`に書ける文字列を返す。
`--no-matches`を外すと各一致の前後の文脈も出る。
`read`は段落ごとの本文を返し、上付き・下付きを含む段落には`<sup>`/`<sub>`付きの本文も付く。
`--paragraphs`を付けずに`read`を呼ぶと原稿が丸ごと出るので、エージェントに読ませるときは範囲を絞る。

変更履歴の組み方（既存の変更履歴の中に挿入するときの分割など）と検証項目の定義は[ooxml_tracked_changes.md](plugins/docx-redline-ja/skills/docx-redline-ja/references/ooxml_tracked_changes.md)にある。

## 開発者向け

### テスト

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q
```

テストに使うdocxの多くは実行時に生成する。Wordで作った4つの文書だけは`tests/fixtures/`に置いてあり、作成者や日時などのメタデータは消してある。
日本語の本文は夏目漱石『吾輩は猫である』（青空文庫）、英語はLewis Carroll『Alice's Adventures in Wonderland』（Project Gutenberg）から取っている。
出典と扱いは[TEST_DATA.md](TEST_DATA.md)にある。
CIはGitHub ActionsでUbuntuとmacOS、Python 3.11〜3.13の組み合わせで回す。

`tests/test_behavior_pins.py`は、上付き・下付き、表のセル、テキストボックス、他人の変更履歴、全skip理由を踏む文書を組んで、レポートと出力XMLをスナップショットと比べる。
意図して挙動を変えたときは`python3 tests/test_behavior_pins.py --regen`で更新し、差分を見る。

### ベンチマーク

`benchmarks/`に、同じ修正を他のツールで入れたときとの比較がある。
比較対象はdocx-cli（npmの`bun-docx`）と、Anthropicが配布している`docx`スキル（OOXMLを手で書く）である。
採点は`benchmarks/measure.py`が出力docxを直接読んで行い、どのツールの検証コードも使わない。

- `run_deterministic.py`：モデルを使わず、決まった修正を各ツールに入れて、変更履歴が正しいか（承諾すると意図した本文、却下すると原文に戻る）と、変更履歴の中の文字数が最小の何倍か、を測る。
- `run_agentic.py`：Claude Codeに原稿と修正指示を渡して、ツールの操作まで含めた一仕事をさせ、トークン数と費用を測る。APIを呼ぶので費用がかかり、CIでは回さない。

`benchmarks/out/deterministic.md`の結果では、日本語20件の修正で変更履歴に入る文字数は、このスキルが45文字（最小と同じ）、docx-cliが1265文字（28.1倍）だった。
英語では140文字に対して1430文字（10.2倍）で、差は日本語のほうが大きい。
`benchmarks/out/agentic-opus-5-5/agentic.md`（claude-opus-5-5、各3回）でも、日本語6件の修正でこのスキルは12文字（最小と同じ）、docx-cliは249文字（20.8倍）、`docx`スキルは12文字だが入力トークンはこのスキルの2.6倍だった。
`benchmarks/out/`は`.gitignore`に入っていて、数字を更新するときは手元で回す。

## ライセンス

MIT。[LICENSE](LICENSE)を見てほしい。
