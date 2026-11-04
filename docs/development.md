# 開発者向け

English version: [development_en.md](development_en.md)

コマンドはリポジトリのルートで実行します。

## テスト

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q
```

テストに使うdocxの多くは実行時に生成します。Wordで作った4つの文書だけは`tests/fixtures/`に置いてあり、作成者や日時などのメタデータは消してあります。
日本語の本文は夏目漱石『吾輩は猫である』（青空文庫）、英語はLewis Carroll『Alice's Adventures in Wonderland』（Project Gutenberg）から取っています。
出典と扱いは[TEST_DATA.md](../TEST_DATA.md)にあります。
CIはGitHub ActionsでUbuntuとmacOS、Python 3.11〜3.13の組み合わせで回します。

`tests/test_behavior_pins.py`は、上付き・下付き、表のセル、テキストボックス、他人の変更履歴、全skip理由を踏む文書を組んで、レポートと出力XMLをスナップショットと比べます。
意図して挙動を変えたときは`python3 tests/test_behavior_pins.py --regen`で更新し、差分を見ます。

## ベンチマークの回し方

`benchmarks/`に、同じ修正を他のツールで入れたときとの比較があります。
比較対象はdocx-cli（npmの`bun-docx`）と、Anthropicが配布している`docx`スキル（OOXMLを手で書く）です。
採点は`benchmarks/measure.py`が出力docxを直接読んで行い、どのツールの検証コードも使いません。

- `run_deterministic.py`：モデルを使わず、決まった修正を各ツールに入れて、変更履歴が正しいか（承諾すると意図した本文、却下すると原文に戻る）と、変更履歴の中の文字数が最小の何倍か、を測ります。
- `run_agentic.py`：Claude Codeに原稿と修正指示を渡して、ツールの操作まで含めた一仕事をさせ、トークン数と費用を測ります。各回は`--restricted`で1つの道具だけを見せ、sandboxの中で動かします。入力トークンはキャッシュの読み書きを含み、費用はClaude Codeが報告する額です。APIを呼ぶので費用がかかり、CIでは回しません。

`benchmarks/out/deterministic.md`の結果では、日本語20件の修正で変更履歴に入る文字数は、このスキルが45文字（最小と同じ）、docx-cliが1265文字（28.1倍）でした。
英語では140文字に対して1430文字（10.2倍）で、差は日本語のほうが大きくなります。
[READMEのベンチマーク](../README.md#ベンチマーク)の表は次のコマンドで取りました。

```bash
python3 benchmarks/run_agentic.py --model claude-sonnet-5-5 --lang both --runs 3 --tasks 20 --paragraphs 40 --max-turns 80
```

`benchmarks/out/`は`.gitignore`に入っていて、数字を更新するときは手元で回します。

## READMEの図

冒頭の比較図は`python3 tools/render_readme_example.py`で描き直します。
例文から1段落のdocxを組み、同じfindingsをこのスキルとdocx-cli（`bunx bun-docx@0.28.0`）に入れ、出力docxの`w:del`/`w:ins`を読んで描くので、手で描いた部分はありません。
Pillowと`bunx`と日本語フォントが必要です。
