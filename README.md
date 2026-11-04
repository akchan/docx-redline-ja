# docx-redline-ja

[English](README_en.md)

AIが出した校正の指摘を、Wordの変更履歴として原稿の`.docx`に書き込むツールです。日本語の文章でも、変更履歴が付くのは実際に変えた文字だけです。Claude Codeのプラグインとして使えるほか、Pythonのスクリプト単体でも動きます。

## 何に役立つか

Claudeに原稿を校正させると、指摘はふつうチャットの文章として返ってきます。これを原稿に反映するには、Wordを開いて該当箇所を1件ずつ探して直すことになり、件数が多いとかなりの手間です。どこを直したのかも後から分からなくなります。

このツールを使うと、指摘がWordの変更履歴として原稿に入ります。あとはいつもの校閲画面で、変更を1件ずつ承諾または却下すれば済みます。そのまま共著者や編集者に渡すこともできます。

このツール自体は校正をしません。担当するのは、直す前と直した後の文字列の組（`old_text`と`new_text`）を、変更履歴として原稿に書き込むところだけです。指摘を出すのはClaudeや、校正・査読のための別のスキルです。

変更履歴の著者名は指定できます。既定は「AI校正」なので、人が入れた変更と見分けられます。

## 日本語でも、変えた文字だけに変更履歴が付く

`{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}`という指摘なら、変更履歴が付くのは挿入した「る」の1文字だけです。

当たり前に見えますが、単語単位で差分をとるツールではこうなりません。日本語の文には単語の間のスペースがないので、句読点までの一続き、ときには段落全体が1単語として扱われます。1文字直しただけでその全体が削除と挿入になり、どこを直したのか読み取れなくなるのです。このツールは1文字単位で差分をとるので、変更履歴は必要な最小限に収まります。

`benchmarks/`で、同じ20件の修正を各ツールで入れ、変更履歴が付いた文字数を比べました。日本語では、このツールが45文字で、これは理論上の最小値と同じです。同じ修正で、[docx-cli](https://github.com/kklimuk/docx-cli)の`replace --batch`は1,265文字、`edit --batch`は12,975文字に変更履歴が付きました。

Anthropicの`docx`スキルには変更履歴を入れるコマンドがなく、Claudeが変更履歴のXMLを直接書きます。結果は悪くありませんが、そのぶん時間とトークンを多く使います。

コメントを付ける機能はありません。直すかどうかを著者が判断すべき指摘をWordのコメントとして入れたいときは、[Adeu](https://github.com/dealfluence/adeu)や`docx`スキルのコメント機能を使ってください。

## 安全のための決まりごと

- 元のファイルは書き換えず、別のファイルに書き出します。
- 変更履歴を書き込む処理は手元のマシンだけで行い、このツールが文書の内容を外部に送ることはありません。ただし、ClaudeやCodexに校正させる段階では、原稿がそれぞれのサービスに送られます。
- 指定した文字列が原稿の中に複数あるときは、どれを直すべきか決められないので、既定では書き込まずにスキップします。スキップした指摘は理由とともに報告します。
- 書き出す前に、変更履歴をすべて却下すると元の文章に戻るか、ほかの人が入れた変更履歴を壊していないか、XMLと変更履歴の構造が正しいかを確かめます。1つでも確かめられなければファイルを書き出しません。
- `.docx`の中身のうち、本文（`word/document.xml`）以外の部品は元のファイルと1バイトも変えません。

## Claude Codeで使う

```text
/plugin marketplace add akchan/docx-redline-ja
/plugin install docx-redline-ja@docx-redline-ja
```

動かすにはPython 3.11以降と`lxml`が必要です。

インストールしたら、たとえば次のように頼みます。

```text
manuscript.docxを校正して、指摘をfindings.jsonlにまとめてから、docx-redline-jaで変更履歴として入れて
```

## Codexで使う

Codex CLIでは次のコマンドでインストールします。

```sh
codex plugin marketplace add akchan/docx-redline-ja
codex plugin add docx-redline-ja@docx-redline-ja
```

必要なものと頼み方はClaude Codeと同じです。

## Claude Desktop（Cowork）で使う

GitHubのリポジトリをマーケットプレイスとして追加します。

1. CustomizeのPluginsで「+」を押し、Add marketplaceを選びます。
2. `akchan/docx-redline-ja`を入力します。
3. 一覧に出てくるdocx-redline-jaをインストールします。

頼み方はClaude Codeと同じです。

## スクリプトを直接使う

```bash
python3 -m pip install -r requirements.txt
python3 plugins/docx-redline-ja/skills/docx-redline-ja/scripts/apply_redline.py \
  --docx manuscript.docx --findings findings.jsonl --out reviewed.docx
```

入力するJSONLは、1行に1件の指摘を書きます。

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
{"old_text":"MRI検査","new_text":"MRI 検査","replace_all":true}
```

入力の仕様、位置の指定方法、検証結果の項目、スキップされる理由の一覧は、`plugins/docx-redline-ja/skills/docx-redline-ja/`の`SKILL.md`と`references/`ディレクトリに書いてあります。

## 開発

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
claude plugin validate .
```

テストに使う日本語と英語のDOCXファイルは、テストを実行するたびに生成します。本文は夏目漱石『吾輩は猫である』とLewis Carroll『不思議の国のアリス』の短い抜粋で、どちらもパブリックドメインです。出典は`TEST_DATA.md`にあります。

## ライセンス

MITです。
