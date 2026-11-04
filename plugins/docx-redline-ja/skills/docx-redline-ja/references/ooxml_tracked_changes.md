# 変更履歴（`w:ins`/`w:del`）の書き方と検証

このツールが変更履歴をOOXMLで組むときの規則と、出力を書く前の検証。

## 共通の属性

```xml
<w:ins w:id="41" w:author="AI" w:date="2026-08-17T06:26:03Z"> … </w:ins>
<w:del w:id="42" w:author="AI" w:date="2026-08-17T06:26:03Z"> … </w:del>
```

- `w:id`は文書内の既存の最大値+1から連番で採る。
- `w:date`は`YYYY-MM-DDThh:mm:ssZ`。`--date`で固定できる。
- `w:author`はWordの校閲ペインに出る名前。

## 挿入

独立した`<w:ins>`を該当位置に差し込むだけで、前後のrunの文字は1文字も書き換えない。挿入点がrunの内部なら、runをそこで2つに割って間に挟む。

```xml
<w:r><w:t>所見がみられ</w:t></w:r>
<w:ins w:id="41" w:author="AI" w:date="…">
  <w:r><w:rPr>…</w:rPr><w:t>る</w:t></w:r>
</w:ins>
<w:r><w:t>か？</w:t></w:r>
```

- 挿入runの`<w:rPr>`は、挿入点の直前のrun（段落先頭なら直後のrun）の複製。ただし書式の変更履歴（`w:rPrChange`）は複製しない。
- 先頭か末尾に空白・タブがある`<w:t>`には`xml:space="preserve"`を付ける。

## 上付き・下付き（`w:vertAlign`）

| 状態 | XML |
|---|---|
| 上付き | 複製した`rPr`に`<w:vertAlign w:val="superscript"/>` |
| 下付き | 複製した`rPr`に`<w:vertAlign w:val="subscript"/>` |
| 並字 | `w:vertAlign`を付けない（複製元にあれば取り除く） |

挿入runの`rPr`は隣のrunの複製なので、上付きの文献番号の隣に並字を挿入するときは、複製した`w:vertAlign`を取り除く必要がある。そのため挿入する全文字について、上付き・下付き・並字のどれかを決めて書く。どの書式を選ぶかは[findings_schema.md](findings_schema.md)の「タグ無しの文字がどの書式を引き継ぐか」による。

`w:vertAlign`は`CT_RPr`の並び順に従い、`w:rtl`/`w:cs`/`w:em`/`w:lang`/`w:eastAsianLayout`/`w:specVanish`/`w:oMath`/`w:rPrChange`より前に置く。

書式だけが変わる文字は、削除と挿入の組になる。

```xml
<w:del …><w:r><w:rPr>…</w:rPr><w:delText>3</w:delText></w:r></w:del>
<w:ins …><w:r><w:rPr>…<w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r></w:ins>
```

## 削除

削除範囲だけを含むrunを切り出し、`<w:del>`で包む。`<w:rPr>`は元のrunから複製する。

```xml
<w:del w:id="42" w:author="AI" w:date="…">
  <w:r><w:rPr>…</w:rPr><w:delText xml:space="preserve">) </w:delText></w:r>
</w:del>
```

- `<w:del>`の中の`<w:t>`は`<w:delText>`に、`<w:instrText>`は`<w:delInstrText>`に変える。`<w:del>`の中に`<w:t>`が残るとWordは文書が壊れていると判断する。
- 削除範囲が複数のrunに跨るときは、runごとに`<w:del>`を作る。

置換は文書順で「削除→挿入」に並べる。

```xml
<w:del …><w:r><w:delText>) </w:delText></w:r></w:del>
<w:ins …><w:r><w:t>）</w:t></w:r></w:ins>
```

## 既存の変更履歴の中を編集する

ここでいう既存の履歴は、著者を問わない。前回の実行が同じ著者名で残した履歴も、Word上で個別に却下されうるので他人の履歴と同じに扱う。

### 既存の`<w:ins>`の中を削除する

内側に`<w:del>`を入れ子にする。`<w:ins>`を消したり置き換えたりはしない。こうすると、元の挿入を承諾してこちらの削除を却下したときに元の文字へ戻る。

```xml
<w:ins w:id="103" w:author="Reviewer A" w:date="…">
  <w:r><w:t>増強効果</w:t></w:r>
  <w:del w:id="44" w:author="AI" w:date="…">
    <w:r><w:delText>が</w:delText></w:r>
  </w:del>
  <w:r><w:t>認め</w:t></w:r>
</w:ins>
```

### 既存の`<w:ins>`の中に挿入する

こちらの`<w:ins>`を既存の`<w:ins>`の子孫にはしない。元の挿入を却下すると、こちらの挿入まで一緒に消えるため。既存の`<w:ins>`を分割し、間にこちらの`<w:ins>`を置く。分割した後半には新しい`w:id`を採り、`w:author`/`w:date`は元のまま複製する。

```xml
<w:ins w:id="101" w:author="Reviewer A" …><w:r><w:t>明瞭に</w:t></w:r></w:ins>
<w:ins w:id="43"  w:author="AI"         …><w:r><w:t>良好に</w:t></w:r></w:ins>
<w:ins w:id="45"  w:author="Reviewer A" …><w:r><w:t>描出される</w:t></w:r></w:ins>
```

runと`<w:ins>`の間に`w:dir`/`w:bdo`/`w:smartTag`/`w:hyperlink`/`w:customXml`が挟まっているときは、その容器ごと2つに割る。容器の属性（`w:val`/`r:id`/`w:uri`など）と先頭の`*Pr`子要素は両側に複製する。

```xml
<!-- 入力 -->
<w:ins w:id="9001" w:author="Reviewer A" …>
  <w:dir w:val="ltr"><w:r><w:t>abcdef</w:t></w:r></w:dir>
</w:ins>

<!-- 出力 -->
<w:ins w:id="9001" w:author="Reviewer A" …><w:dir w:val="ltr"><w:r><w:t>abc</w:t></w:r></w:dir></w:ins>
<w:ins w:id="43"   w:author="AI"         …><w:r><w:t>X</w:t></w:r></w:ins>
<w:ins w:id="44"   w:author="Reviewer A" …><w:dir w:val="ltr"><w:r><w:t>def</w:t></w:r></w:dir></w:ins>
```

それ以外の容器（`w:sdt`/`w:sdtContent`、`w:fldSimple`など）は分割しない。`w:sdt`はタグのIDやデータバインドを複製することになり、文書の構造が変わるため。挿入点が既存の`<w:ins>`/`<w:moveTo>`の中で、間にこうした容器があるときは、入れ子にせずレコードごと`unsplittable_container`でskipする。削除は内側に入れ子にするのが正しい形なので、この制約を受けない。

### `<w:moveTo>`と`<w:moveFrom>`

移動を却下すると移動先`<w:moveTo>`の文字は消えるので、`<w:moveTo>`は`<w:ins>`と同じに扱う。挿入なら分割して外へ出し、削除なら内側に入れ子にする。

```xml
<!-- 入力 -->
<w:moveTo w:id="9100" w:author="Reviewer A" …><w:r><w:t>abcdef</w:t></w:r></w:moveTo>

<!-- 出力 -->
<w:moveTo w:id="9100" w:author="Reviewer A" …><w:r><w:t>abc</w:t></w:r></w:moveTo>
<w:ins    w:id="43"   w:author="AI"         …><w:r><w:t>X</w:t></w:r></w:ins>
<w:moveTo w:id="44"   w:author="Reviewer A" …><w:r><w:t>def</w:t></w:r></w:moveTo>
```

既存の`<w:del>`と`<w:moveFrom>`の中身は可視テキストに現れないので、編集の対象にならない。

## 可視テキスト

`old_text`と照合するのは、変更履歴をすべて承諾したときの文字列である。

- `<w:del>`/`<w:moveFrom>`の中身は含まない。
- `<w:ins>`/`<w:moveTo>`の中身は著者を問わず含む。
- `<w:hyperlink>`/`<w:smartTag>`/`<w:sdtContent>`/`<w:fldSimple>`などの容器は、中身をそのまま読む。
- `<w:bookmarkStart>`/`<w:commentRangeStart>`/`<w:commentReference>`/`<w:proofErr>`/`<w:lastRenderedPageBreak>`などは0文字。
- `<w:tab>`/`<w:ptab>`は`\t`、`<w:noBreakHyphen>`は`-`として数える。それ以外のインライン要素（`<w:br>`、`<w:drawing>`、`<w:pict>`、`<w:object>`、`<w:fldChar>`、`<w:instrText>`、`<w:sym>`、`<w:footnoteReference>`、`<m:oMath>`など）はU+0000の1文字として数える。U+0000はXML 1.0に現れないので、`old_text`がここに一致することはない。
- `mc:AlternateContent`は`mc:Choice`側だけを読む。編集を`mc:Fallback`にも入れる条件は[SKILL.md](../SKILL.md)の「適用範囲」にある。

一致範囲がこれらのインライン要素に少しでも掛かると、`spans_inline_object`で適用しない。

`old_text`は1段落内に収まるので、段落記号は削除しない（`<w:pPr><w:rPr><w:del/></w:rPr></w:pPr>`は生成しない）。段落の全文字を削除しても段落は残り、隣の段落とは結合されない。

## 検証

5項目がすべて`true`のときだけ出力docxを書き、終了コード0で終える。1つでも`false`なら何も書かず、終了コード1で終える。出力docxは同じフォルダの一時ファイルに書いてから置き換えるので、書きかけのファイルは残らない。原本は読むだけで、一度も書き込み用に開かない。

| 項目 | 確かめること |
|---|---|
| `reversible` | 原本と出力の両方から自著者の履歴を却下すると、段落の本文が一致する |
| `insertions_independent` | この実行が書いた`w:ins`以外の履歴をすべて却下しても、それらの`w:ins`が残る。却下する履歴には、同じ著者名の既存の履歴も含む |
| `other_revisions_preserved` | 自著者以外の`w:ins`/`w:del`/`w:moveFrom`/`w:moveTo`が持つ文字列が、実行の前後で変わらない |
| `revisions_wellformed` | `w:id`の重複、`w:author`/`w:date`の欠落、削除の中の`w:t`、削除の外の`w:delText`が原本より増えていない |
| `media_preserved` | 出力パッケージのエントリ名の一覧が原本と同じ（ディレクトリのエントリは除く） |

`reversible`は原稿に同じ著者名の履歴が既にあっても正しく判定できる。ただし自著者の履歴だけを本文の文字列で比べるので、自著者の`w:ins`が既存の`w:ins`/`w:moveTo`の内側に入れ子になる破綻と、他人の履歴の中身が書き換わる破綻は見えない。前者を`insertions_independent`が、後者を`other_revisions_preserved`が受け持つ。`insertions_independent`が調べるのはこの実行が書いた`w:ins`だけなので、原本に元からあった同著者名の入れ子では落ちない。`revisions_wellformed`も、原本に元からあった不備では落ちない。

## ファイルパスの衝突

`--docx`/`--findings`/`--out`/レポート（`--out`の拡張子を`.result.json`に変えたパス）のどれか2つが同じファイルを指したら、何も書かずに終了コード2で終える。同じかどうかは`os.path.samefile`で判定するので、大小文字を区別しないファイルシステム（macOSのAPFSの既定）での`sample.docx`と`Sample.docx`や、シンボリックリンク・ハードリンク経由の同一も拒否する。

まだ存在しない2つのパス（これから作る出力どうし）は、解決したパスを大小文字を無視して比べる。大小文字を区別するファイルシステムでは別ファイルになるはずの名前も拒否するので、そのときは名前を変える。

`--out`の拡張子は`.docx`に限る。
