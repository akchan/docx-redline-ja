---
name: docx-redline-ja
description: old_text/new_text形式のfindings JSONLを読み、Wordの変更履歴（tracked changes）としてdocxの別コピーに反映する。指摘の抽出（校正そのもの）は行わない。findings JSONLが既に手元にあるときだけ使う。
---

# docx-redline-ja

`old_text`/`new_text`の組を並べたfindings JSONLを読み、原稿docxの別コピーにWordの変更履歴として書き込む。差分は文字単位で取るので、変更履歴は実際に変わる文字だけになる。指摘そのものは作らない。findings JSONLが無ければこのスキルは使わず、別のスキルやエージェントで先に指摘を作る。

- 必要なのはPython 3.11以降と`lxml`だけ。外部のスキルやCLIは呼ばない。
- 入力のdocxとJSONLは書き換えない。出力は必ず別ファイルになる。
- `<skill_dir>`はこの`SKILL.md`があるディレクトリ。

## 入力

原稿docx、findings JSONL、出力docx（拡張子は`.docx`）、変更履歴の著者名（指定が無ければ`AI`）を確かめる。

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
{"old_text":"MRI検査","new_text":"MRI 検査","replace_all":true}
```

| キー | 意味 |
|---|---|
| `old_text` | 必須。変更履歴をすべて承諾した状態の本文と照合する。1段落内に収まり、（`scope`の範囲内で）一意であること |
| `new_text` | 必須。置換後の文字列で、空文字列なら削除。`<sup>`/`<sub>`/`<plain>`で上付き・下付きを指定する |
| `replace_all` | `true`なら一致した全箇所に適用する。既定は`false` |
| `scope` | 照合する段落の範囲。`"p12"`、`"p10-12"`、`"p3,p10-12"`、`"p120-"`（末尾まで）。既定は文書全体 |

これ以外のキー（`id`、`severity`など）があってもレコードは有効で、値は読まずに捨て、名前だけを`records[].ignored_keys`と`notes`に出す。キー名の打ち間違いはここで分かる。書式の継承、一意性の数え方、`scope`の細則は[references/findings_schema.md](references/findings_schema.md)にある。

## 実行

```bash
python3 <skill_dir>/scripts/apply_redline.py \
  --docx <原稿.docx> --findings <findings.jsonl> --out <出力.docx> --author "<モデル名>"
```

| オプション | 用途 |
|---|---|
| `--author <名前>` | 変更履歴の著者名。ユーザの指定が無ければ、自分のモデル名（例：`Claude Opus 5.5`）を渡す。省略すると`AI`になる |
| `--date <ISO8601>` | 変更履歴の日時。既定は現在時刻（UTC）。入力と著者名と日時が同じなら出力docxはバイト単位で同じになる |

レポートは`--out`の拡張子を`.result.json`に変えたパスに書く。終了コードは次のとおり。

- `2`：起動時のエラー。入力が無い、`.docx`でない、`--docx`/`--findings`/`--out`/レポートのうち2つが同じファイルを指す、など。何も書かず、理由をstderrに出す。
- `0`：`verification`の5項目がすべて`true`。docxを書く。
- `1`：1項目でも`false`。docxは書かず、原因は`notes`に出る。

## 位置を調べる

次の2つは原稿を読むだけで書き換えない。段落番号は0始まりで、`scope`、`locate`、`read`、`result.json`のどれでも同じ番号を使う。

```bash
python3 <skill_dir>/scripts/apply_redline.py locate --docx <原稿.docx> --query "FDG-PET" --no-matches
python3 <skill_dir>/scripts/apply_redline.py read --docx <原稿.docx> --paragraphs "p10-20"
```

- `locate`は`{"source":…,"queries":[…]}`を返す。`queries[]`の各要素は`count`、`paragraphs`（`["p28"]`）、`scope`（そのまま`scope`キーに書ける文字列）、`matches[]`（`locator`と前後文脈）を持つ。`--query`は繰り返し指定できる。`matches[]`は20件までで、超えた数は`matches_truncated`に出る。`count`/`paragraphs`/`scope`は常に全一致から数える。
- `read`は`paragraph_count`と`paragraphs[]`（`paragraph`、`text`）を返す。上付き・下付きを含む段落には`text_tagged`（`<sup>18</sup>F-FDG`）が付く。`--text`を付けると`p0<TAB>本文`の1行1段落で出る。

原稿全体をstdoutに出さないこと。`read`を`--paragraphs`なしで呼ぶと本文が丸ごと出る。

1. 語の位置や`scope`だけ知りたい → `locate --no-matches`
2. 段落を読む → `--paragraphs "p10-20"`で範囲を絞る。`paragraph_count`は文書全体の数なので、そこから範囲を広げ直せる。
3. 全文が要る → `> <out.json>`でファイルに書き出して、必要な所だけ読む

## 結果を読む

stdoutに出るのは要約で、報告にはこれで足りる。

```jsonc
{"out": "…", "result": "…/出力.result.json",
 "counts": {"records": 22, "applied": 13, "skipped": 9, "hunks": 19, "by_reason": {"ambiguous": 1, "conflict": 2}},
 "verification": {"reversible": true, "insertions_independent": true,
   "other_revisions_preserved": true, "revisions_wellformed": true, "media_preserved": true},
 "notes": ["…"],
 "records": [/* skipしたレコードだけ。reason / detail / occurrences */],
 "records_omitted": 13}
```

- `records_omitted`は要約から省いたappliedのレコード数。全レコードは`result`が指す`result.json`にある。skipレコードの`occurrences`は先頭5件までで、残りの数は`occurrences_truncated`に出る。
- `applied`はレコード数。1レコードが複数の変更履歴になりうるので、変更履歴の数は`counts.hunks`を見る。
- 位置は`locator`（`p{段落}:{開始}-{終了}`、0始まりの半開区間）で出る。出力専用で、入力で位置を指定できるのは`scope`の段落範囲だけ。変更を入れると文字位置がずれるので、applyの後は古いlocatorを使わず`locate`をやり直す。
- 検証項目の意味は[references/ooxml_tracked_changes.md](references/ooxml_tracked_changes.md)の「検証」にある。

報告には次を必ず含める。

1. applied/skippedの件数とskip理由の内訳。どの指摘が落ちたかも返す。黙って落とすと、指摘が全部入ったと誤解される。
2. `verification`がすべて`true`であること。
3. `ambiguous`（2箇所以上に一致）は`occurrences`の前後文脈を添えて返す。対処は、`old_text`を伸ばして一意にする、`scope`で段落を絞る（範囲は`locate`で調べる）、全箇所を同じに直す指摘なら`"replace_all": true`を付ける、のどれか。
4. `conflict`（編集範囲が重なる）は、重なる指摘を1レコードにまとめてから再実行する。
5. `unsplittable_container`は原稿の構造が原因で、`old_text`を変えて再実行しても同じ所でskipされる。`locator`と前後文脈を添えて「人手で直す箇所」として返し、再実行の対象に入れない。

skip理由は8つで、各レコードの`detail`にも理由が出る。一覧と対処は[references/findings_schema.md](references/findings_schema.md)の「skip理由」にある。

## 適用範囲

編集するのは`word/document.xml`の段落だけで、表のセル内とテキストボックス内の段落も含む。コメント、ヘッダー・フッター、脚注は対象外。`old_text`は1段落内に収まるものだけを扱う。

テキストボックスはWordが`mc:Choice`と`mc:Fallback`に二重に書くことがある。照合と段落番号は`mc:Choice`側で決め、本文が一致する`mc:Fallback`の段落にも同じ変更履歴を入れる。本文が一致しない`mc:Fallback`と、挿入が既存の変更履歴の中に閉じ込められる`mc:Fallback`は触らず、`notes`に段落が出る。
