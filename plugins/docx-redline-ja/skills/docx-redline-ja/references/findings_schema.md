# findings JSONLの仕様

`docx-redline-ja`が受け取るfindingsファイルの契約と、skip理由の一覧。findingsを生成する側はこれに合わせる。キーの一覧は[SKILL.md](../SKILL.md)にある。

## 形式

UTF-8のJSON Linesで、1行1レコード。先頭のBOMは無視する。空行と`#`で始まる行は無視する。JSONとして壊れた行はその行だけが`invalid_record`になり、他の行は処理を続ける。

`old_text`/`new_text`/`replace_all`/`scope`以外のキーは値ごと保持し、解釈しない。打ち間違えた`replace_al`も無視キーとして報告されるだけでフラグは効かないので、そのレコードは`ambiguous`になりうる。

## `old_text`

- 可視テキストと照合する。可視テキストは、文書中の変更履歴をすべて承諾したときの文字列である。既存の`<w:ins>`の中身は含み、`<w:del>`の中身は含まない。定義の細部は[ooxml_tracked_changes.md](ooxml_tracked_changes.md)の「可視テキスト」にある。
- 一意かどうかは重なり合う一致も数えて決める。`aaa`の中の`aa`は2箇所、`A   B`の中の半角スペース2個も2箇所になる。
- 改行（`\n`/`\r`）を含むと`crosses_paragraph`、空文字列は`invalid_record`になる。
- タブ（`\t`）は書けるが、本文のタブは`<w:tab/>`なので、一致は`spans_inline_object`になる。
- `<sup>`/`<sub>`/`<plain>`は`old_text`では解釈せず、ただの文字として照合する。

## `new_text`

タグとして解釈するのは`<sup>…</sup>`、`<sub>…</sub>`、`<plain>…</plain>`の3つだけで、それ以外の`<`は普通の文字。入れ子（`<sup><plain>x</plain></sup>`）、閉じ忘れ、対応しない閉じタグ、交差は`invalid_record`になる。

| 書き方 | 意味 |
|---|---|
| タグ無し | 原稿の上付き・下付きを引き継ぐ（解除はしない） |
| `<sup>…</sup>` | 上付きにする |
| `<sub>…</sub>` | 下付きにする |
| `<plain>…</plain>` | 上付き・下付きを解除する |

書式は明示したときだけ変わる。長めの`old_text`に文献番号の上付きが入っていても、タグを付け直さなくてよい。

### タグ無しの文字がどの書式を引き継ぐか

同じ場所に既にあった書式を使い、近くの文字の書式はもらわない。

| 差分の種類 | 引き継ぐ書式 |
|---|---|
| 変わらない文字 | その文字自身の書式 |
| 置換される文字 | 置換される範囲の書式。範囲内で書式が一様なときだけで、一様でなければ並字 |
| 挿入だけの文字 | 挿入点の前後の文字の書式が一致するときだけその書式。段落の先頭・末尾、書式の境目では並字 |

- `劣る1）。`→`劣る1)。`（`）`が上付き）：置換範囲が上付き1文字で一様なので、`)`も上付き。
- `1)`→`1,2)`（全体が上付き）：挿入点の前後が上付きなので、`,2`も上付き。
- `劣る1）`→`劣る1）。`（`）`が上付きで、後ろは段落末尾）：挿入点の後ろに文字が無いので、`。`は並字。
- `99mTc-ECDと99mTc-HMPAOの2つ`→`テクネチウム製剤2種の2つ`（先頭3文字だけ上付き）：置換範囲の書式が一様でないので並字。

フォントや太さなど、上付き・下付き以外の書式は隣の文字を引き継ぐ。

### 書式だけの違いも差分になる

差分は「文字と上付き・下付きの組」で比べる。文字が同じでも書式が違えば、削除と挿入の変更履歴になる。

- `{"old_text":"18F-FDG","new_text":"<sup>18</sup>F-FDG"}`：`18`が並字ならapplied。既に上付きなら`no_change`。
- `{"old_text":"1x1x1 mm3","new_text":"1×1×1 mm<sup>3</sup>"}`：`x`→`×`の2つに加え、`3`が並字なら書式の変更履歴も出る。
- `{"old_text":"mm3","new_text":"mm<plain>3</plain>"}`：`3`が上付きなら解除する。既に並字なら`no_change`。

上付き・下付きは`w:rPr/w:vertAlign`の直接指定だけを見る。文字スタイルや段落スタイル経由の上付きは並字として扱う。

## `replace_all`と`scope`

`replace_all`は一意であることの要求を外すフラグ。用語や記号の統一のように、出る所は全部同じに直すと決まっている指摘にだけ付ける。一致どうしが重なる`old_text`（`aaa`中の`aa`など）は、`replace_all`を付けても`conflict`になる。`old_text`を伸ばして一致が重ならないようにする。

`scope`は照合する段落を限るキーで、範囲外の一致は数えない。一意かどうかも範囲の中で決まる。

| 書き方 | 意味 |
|---|---|
| `"p12"` | 段落12だけ |
| `"p10-12"` | 段落10〜12（両端を含む） |
| `"p3,p10-12"` | 段落3と10〜12 |
| `"p120-"` | 段落120から文書の末尾まで |

本文と文献一覧で統一先が逆になる表記（読点・括弧など）は、範囲ごとに別のレコードにする。

```jsonl
{"old_text":"、","new_text":"，","replace_all":true,"scope":"p0-119"}
{"old_text":"，","new_text":"、","replace_all":true,"scope":"p120-"}
```

- 範囲は`locate --query "、" --no-matches`で調べる。返る`scope`の値をそのまま書ける。
- 記法エラー（`"12"`、`"p9-3"`など）は`invalid_record`になる。
- 文書の末尾を越える範囲は、文書にある段落だけを指す。このツールは段落を増やしも減らしもしないので、出力docxに対して再実行しても段落番号は変わらない。
- 範囲外にだけ一致があるレコードは`not_found`になり、`detail`に範囲外の件数が出る。

## skip理由

`records[].reason`に入る値はこの8つだけ。

| 理由 | 意味 | 対処 |
|---|---|---|
| `invalid_record` | 必須キーの欠落・型違い、`old_text`が空、制御文字やXMLに書けない文字（サロゲート、U+FFFE/U+FFFF）、タグの誤り、`scope`の記法エラー | レコードを直す。契約外のキーがあるだけでは無効にならない |
| `crosses_paragraph` | `old_text`か`new_text`に改行がある | 段落ごとのレコードに分ける |
| `no_change` | 文字も書式も既に`new_text`のとおり | 不要 |
| `not_found` | 可視テキストに一致が無い（`scope`があれば範囲内に無い） | 原稿の実際の文字列（全角・半角、約物、空白）と`scope`を確かめる |
| `ambiguous` | （`scope`の範囲内で）2箇所以上に一致し、`replace_all`が無い | `old_text`を伸ばす、`scope`で絞る、または`"replace_all": true` |
| `spans_inline_object` | 一致範囲がタブ・改行・画像・フィールド・脚注番号に掛かる | 範囲を狭める |
| `conflict` | 他のレコードと編集範囲が重なる（下記）、または`replace_all`の一致どうしが重なる | 重なる指摘を1レコードにまとめるか、`old_text`を伸ばす |
| `unsplittable_container` | 挿入点が既存の`w:ins`/`w:moveTo`（著者を問わない）の中で、間に分割できない容器（`w:sdt`など）がある | Wordで人手で直す。`old_text`を変えても直らない。理由は[ooxml_tracked_changes.md](ooxml_tracked_changes.md)の「既存の`<w:ins>`の中に挿入する」 |

## 競合

編集範囲が1文字でも重なるレコードどうしは`conflict`になり、関わったレコードはすべてskipする。どれかを優先することはしない。

接しているだけの置換（`[4,5)`と`[5,6)`）は競合しない。全角数字や読点のような1文字単位の指摘は隣り合うことが多いため。ただし挿入だけの編集が接する場合は、前後どちらに入るかが順序で変わるので`conflict`にする。

## 再実行

同じfindingsを出力docxに対して再実行すると、文字を変えるレコードは`not_found`（可視テキストが既に修正後なので）、書式だけを変えるレコードは`no_change`になる。

## 例

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
{"old_text":"FDG-PET/CTでは","new_text":"<sup>18</sup>F-FDG-PET/CTでは"}
{"old_text":"MRI検査","new_text":"MRI 検査","replace_all":true}
{"old_text":"側頭葉てんかん(TLE) では","new_text":"側頭葉てんかん（TLE）では"}
{"old_text":"不要な語句","new_text":""}
{"old_text":"見出しの語","new_text":"<plain>見出しの語</plain>"}
```

## `result.json`

`result.json`は全レコードを省略なしで持つ。stdoutの要約との違いは[SKILL.md](../SKILL.md)の「結果を読む」にある。

```jsonc
{
  "schema_version": 7,
  "tool": "docx-redline-ja",
  "source": "...", "findings": "...", "out": "...",
  "author": "AI",
  "date": "2026-08-17T06:26:03Z",
  "counts": {"records": 17, "applied": 12, "skipped": 5, "hunks": 20,
             "by_reason": {"ambiguous": 2, "no_change": 1, "not_found": 2}},
  "verification": {/* SKILL.mdと同じ5項目 */},
  "notes": ["key(s) not in the contract were carried through unread: 'id' on record(s) 3"],
  "records": [
    {"index": 3, "old_text": "、", "new_text": "，", "status": "applied",
     "replace_all": true, "scope": "p0-119", "out_of_scope": 12,
     "ignored_keys": ["id"], "matches": 8, "hunks": [/* 8箇所ぶん */]},
    {"index": 9, "old_text": "劣る1）。", "new_text": "劣る1)。", "status": "applied",
     "matches": 1,
     "hunks": [{"paragraph": 16, "locator": "p16:99-100",
                "start": 99, "end": 100, "delete": "）", "insert": ")",
                "insert_format": [{"start": 0, "end": 1, "kind": "superscript"}]}]},
    {"index": 14, "old_text": "FDG-PET", "new_text": "...", "status": "skipped",
     "reason": "ambiguous", "detail": "6 occurrences; ...", "matches": 6,
     "occurrences": [{"paragraph": 6, "locator": "p6:41-48", "start": 41, "end": 48,
                      "before": "…", "match": "FDG-PET", "after": "…"}]}
  ]
}
```

- `replace_all`はtrueのときだけ出る。`scope`は指定したときだけ正規形で出る（`"p4,p3"`は`"p3-4"`）。
- `out_of_scope`は範囲外のために数えなかった一致の数で、0なら出ない。`matches`（範囲内の一致数）と並べると、語が範囲外にあるのか文書に無いのかが分かる。
- `ignored_keys`は契約外のキーの名前（ソート済み）で、無ければ出ない。
- `hunks`は1レコードが生む変更履歴の一覧で、差分を最小にした結果なので複数になりうる。テキストボックスの`mc:Fallback`に入れた同じ変更は数えない。
- `insert_format`は挿入される全文字を覆い、並字（`baseline`）も明示する。
- `paragraph`/`locator`/`start`/`end`は可視テキスト上の位置で、文字はコードポイント単位で数える。hunk、`occurrences`、`locate`の`matches[]`で同じ形をとる。
- `occurrences`の前後文脈（`before`/`after`）は各20文字。

## locator

```
p{段落}:{start}-{end}      例: p28:17-24
```

- appliedの各hunkと、`occurrences`を持つskip（`ambiguous`、`spans_inline_object`、`unsplittable_container`、`no_change`、`replace_all`の一致どうしが重なった`conflict`）に付く。レコード間の`conflict`には付かず、`detail`に相手のindexが出る。
- 段落番号は0始まり。`word/document.xml`の`w:p`を文書順に数え、表のセル内とテキストボックス内の段落を含む。`mc:Fallback`内の複製は数えない。
- `start`は含み、`end`は含まない。その段落の可視テキストで`text[start:end]`が該当部分になる。
- applyは位置ではなく`old_text`の文字列で照合し、1回の実行の中では右から左へ適用する。locatorがずれて困るのは、locateの結果を次の実行まで持ち越したときだけ。
