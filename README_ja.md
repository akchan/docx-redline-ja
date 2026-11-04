# docx-review-tools

[English](README.md)

校閲の結果をMicrosoft Wordの`.docx`ファイルに反映するためのClaude Codeプラグインと、単体で動くPythonスクリプトである。処理はすべて手元のマシンで行い、文書の内容を外部のサービスに送らない。

このリポジトリには、別々にインストールできるプラグインが2つ入っている。

- `docx-apply-redline`は、`old_text`と`new_text`の組を並べたJSONLを、Wordの変更履歴として文書に書き込む。差分は1文字単位でとる。日本語のように単語の間にスペースのない文章でも、変更履歴が付くのは実際に変えた文字だけである。
- `docx-add-comment`は、`anchor_text`と完全に一致する箇所にコメントを付ける。本文の文字は変えない。

どちらも入力ファイルは書き換えず、別のファイルに出力する。一致する箇所が複数あるときは、既定ではその指示を適用せずにスキップする。出力ファイルは、書き出す前にOOXMLパッケージとして検証している。

## Claude Codeにインストールする

```text
/plugin marketplace add akchan/docx-review-tools
/plugin install docx-apply-redline@docx-review-tools
/plugin install docx-add-comment@docx-review-tools
```

必要なプラグインだけをインストールすればよい。動かすにはPython 3.11以降と、`requirements.txt`に書いてあるパッケージが必要である。

## Claude Codeなしで使う

```bash
python3 plugins/docx-apply-redline/skills/docx-apply-redline/scripts/apply_redline.py \
  --docx manuscript.docx --findings findings.jsonl --out reviewed.docx

python3 plugins/docx-add-comment/skills/docx-add-comment/scripts/apply_comment.py \
  --docx manuscript.docx --comments comments.jsonl --out commented.docx
```

入力するJSONLの例を示す。上が変更履歴、下がコメントの指示である。

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
```

```jsonl
{"anchor_text":"主要評価項目","comment":"評価時点を明記してください。"}
```

入力の仕様、位置の指定方法、検証結果の項目、スキップされる理由の一覧は、各プラグインの`SKILL.md`と`references/`ディレクトリに書いてある。

## 開発

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
claude plugin validate .
```

テストに使う日本語と英語のDOCXファイルは、テストを実行するたびに生成する。本文は夏目漱石『吾輩は猫である』とLewis Carroll『不思議の国のアリス』の短い抜粋で、どちらもパブリックドメインである。非公開の原稿や、生成済みの文書ファイルはリポジトリに置いていない。出典は`TEST_DATA.md`にある。

## ライセンス

MIT。同梱しているOOXMLスキーマと補助コードについては`THIRD_PARTY_NOTICES.md`に書いてある。　
