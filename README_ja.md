# docx-review-tools

[English](README.md)

AIが出した校正の指摘を、Wordの変更履歴とコメントとして原稿の`.docx`に書き込むツールです。Claude Codeのプラグインとして使えるほか、Pythonのスクリプト単体でも動きます。

## 何に役立つか

Claudeに原稿を校正させると、指摘はふつうチャットの文章として返ってきます。これを原稿に反映するには、Wordを開いて該当箇所を1件ずつ探して直すことになり、件数が多いとかなりの手間です。どこを直したのかも後から分からなくなります。

このツールを使うと、指摘がWordの変更履歴とコメントとして原稿に入ります。あとはいつもの校閲画面で変更を1件ずつ承諾または却下し、コメントを読めば済みます。そのまま共著者や編集者に渡すこともできます。

このツール自体は校正をしません。担当するのは、指摘を原稿に書き込むところだけです。指摘を出すのはClaudeや、校正・査読のための別のスキルです。

## 2つのプラグイン

プラグインは2つあり、片方だけでも使えます。

- `docx-apply-redline`は、直す前と直した後の文字列の組（`old_text`と`new_text`）を、変更履歴として書き込みます。
- `docx-add-comment`は、指定した文字列（`anchor_text`）にコメントを付けます。本文は1文字も変えません。「検定手法を明記してください」のように、直すかどうかを著者が判断すべき指摘に向いています。

変更履歴とコメントの著者名は指定できます。既定は「AI校正」なので、人が入れた変更と見分けられます。

## 日本語でも、変えた文字だけに変更履歴が付く

`{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}`という指摘なら、変更履歴が付くのは挿入した「る」の1文字だけです。

当たり前に見えますが、単語単位で差分をとるツールではこうなりません。日本語の文には単語の間のスペースがないので、段落全体が1単語として扱われます。1文字直しただけで段落ごと削除と挿入になり、どこを直したのか読み取れなくなるのです。このツールは1文字単位で差分をとるので、変更履歴は必要な最小限に収まります。

`benchmarks/`で、同じ20件の修正を各ツールで入れ、変更履歴が付いた文字数を比べました。日本語では、このツールが45文字で、これは理論上の最小値と同じです。同じ修正で、[docx-cli](https://github.com/kklimuk/docx-cli)の`replace --batch`は1,265文字、`edit --batch`は12,975文字に変更履歴が付きました。

Anthropicの`docx`スキルには変更履歴を入れるコマンドがなく、Claudeが変更履歴のXMLを直接書きます。結果は悪くありませんが、そのぶん時間とトークンを多く使います。

## 安全のための決まりごと

- 元のファイルは書き換えず、別のファイルに書き出します。
- 処理はすべて手元のマシンで行い、文書の内容を外部のサービスに送りません。未発表の論文のように、外に出せない原稿にも使えます。
- 指定した文字列が原稿の中に複数あるときは、どれを直すべきか決められないので、既定では書き込まずにスキップします。スキップした指摘は理由とともに報告します。
- 書き出す前に、ファイルがOOXMLとして正しいかを確かめます。変更履歴の場合は、すべて却下すると元の文章に戻るかも確かめます。確かめられなければファイルを書き出しません。

## Claude Codeで使う

```text
/plugin marketplace add akchan/docx-review-tools
/plugin install docx-apply-redline@docx-review-tools
/plugin install docx-add-comment@docx-review-tools
```

必要なプラグインだけをインストールすれば十分です。動かすにはPython 3.11以降と、`requirements.txt`に書いてあるパッケージが必要です。

インストールしたら、たとえば次のように頼みます。

```text
manuscript.docxを校正して、指摘をfindings.jsonlにまとめてから、docx-apply-redlineで変更履歴として入れて
```

## Claude Codeなしで使う

```bash
python3 plugins/docx-apply-redline/skills/docx-apply-redline/scripts/apply_redline.py \
  --docx manuscript.docx --findings findings.jsonl --out reviewed.docx

python3 plugins/docx-add-comment/skills/docx-add-comment/scripts/apply_comment.py \
  --docx manuscript.docx --comments comments.jsonl --out commented.docx
```

入力するJSONLは、1行に1件の指摘を書きます。上が変更履歴、下がコメントの例です。

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
```

```jsonl
{"anchor_text":"主要評価項目","comment":"評価時点を明記してください。"}
```

入力の仕様、位置の指定方法、検証結果の項目、スキップされる理由の一覧は、各プラグインの`SKILL.md`と`references/`ディレクトリに書いてあります。

## 開発

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
claude plugin validate .
```

テストに使う日本語と英語のDOCXファイルは、テストを実行するたびに生成します。本文は夏目漱石『吾輩は猫である』とLewis Carroll『不思議の国のアリス』の短い抜粋で、どちらもパブリックドメインです。非公開の原稿や、生成済みの文書ファイルはリポジトリに置いていません。出典は`TEST_DATA.md`にあります。

## ライセンス

MITです。同梱しているOOXMLスキーマと補助コードについては`THIRD_PARTY_NOTICES.md`を見てください。
