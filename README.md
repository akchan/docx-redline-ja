# docx-review-tools

[日本語](README_ja.md)

Write AI proofreading results into a Word manuscript as tracked changes and
comments. The tools run as Claude Code plugins or as standalone Python scripts.

## What it's for

When you ask Claude to proofread a manuscript, the suggestions usually come back
as chat text. Applying them means opening Word, finding each passage, and fixing
it by hand. With dozens of suggestions that takes a while, and afterward nobody
can tell what was changed.

These tools put the suggestions into the document as native Word tracked changes
and comments. You then work through them in Word's normal review pane, accepting
or rejecting each change and reading each comment, or pass the file straight to
a co-author or editor.

The tools do not proofread anything themselves. They only write findings into
the document. The findings come from Claude or from a separate proofreading or
peer-review skill.

## Two plugins

Each plugin installs on its own.

- `docx-apply-redline` takes before/after pairs (`old_text` and `new_text`) and
  writes them as tracked changes.
- `docx-add-comment` attaches a comment to a given passage (`anchor_text`)
  without changing a single character of the body. Use it for points the author
  has to decide on, such as "State which statistical test was used."

You can set the author name on revisions and comments. It defaults to `AI校正`
("AI proofreading"), so the changes are easy to tell apart from a human's.

## Minimal redlines, even in Japanese

Given `{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}`, the
tracked change covers only the one inserted character, `る`.

That sounds obvious, but word-level diff tools don't manage it for languages
written without spaces. A Japanese paragraph has no spaces, so it is treated as
one word, and changing a single character deletes and reinserts the whole
paragraph. This tool diffs character by character, so the redline stays as small
as the edit.

`benchmarks/` applies the same 20 edits with each tool and counts the characters
marked up. In Japanese this tool marks up 45, which is the theoretical minimum.
[docx-cli](https://github.com/kklimuk/docx-cli) marks up 1,265 with
`replace --batch` and 12,975 with `edit --batch`. In English the figures are 140
for this tool and 1,430 and 312 for docx-cli.

Anthropic's `docx` skill has no command for tracked changes, so Claude writes
the revision XML by hand. The results are reasonable, but it costs much more
time and many more tokens.

## Safety

- The input file is never modified. Output always goes to a separate file.
- Everything runs locally, and no document content is sent to any service. That
  makes the tools usable on unpublished or confidential manuscripts.
- If a passage occurs more than once, the tool cannot know which one is meant,
  so by default it skips that finding and reports why.
- Before writing the output, the tools check that the package is valid OOXML.
  For tracked changes they also check that rejecting every change restores the
  original text. If a check fails, no file is written.

## Use in Claude Code

```text
/plugin marketplace add akchan/docx-review-tools
/plugin install docx-apply-redline@docx-review-tools
/plugin install docx-add-comment@docx-review-tools
```

Install only the plugin you need. Python 3.11 or later and the packages in
`requirements.txt` are required.

Then ask Claude something like:

```text
Proofread manuscript.docx, collect the findings in findings.jsonl, and apply
them with docx-apply-redline as tracked changes.
```

## Use without Claude Code

```bash
python3 plugins/docx-apply-redline/skills/docx-apply-redline/scripts/apply_redline.py \
  --docx manuscript.docx --findings findings.jsonl --out reviewed.docx

python3 plugins/docx-add-comment/skills/docx-add-comment/scripts/apply_comment.py \
  --docx manuscript.docx --comments comments.jsonl --out commented.docx
```

The input is JSONL, one finding per line. The first example is a tracked change
and the second a comment:

```jsonl
{"old_text":"所見がみられか？","new_text":"所見がみられるか？"}
```

```jsonl
{"anchor_text":"主要評価項目","comment":"評価時点を明記してください。"}
```

See each plugin's `SKILL.md` and `references/` directory for the complete input
contract, location syntax, verification fields, and skip reasons.

## Development

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
claude plugin validate .
```

Japanese and English test DOCX files are generated during each test run from
short excerpts of Natsume Soseki's *I Am a Cat* and Lewis Carroll's *Alice's
Adventures in Wonderland*, both in the public domain. No private manuscripts or
pre-generated document fixtures are stored in this repository. See
`TEST_DATA.md` for sources.

## License

MIT. See `THIRD_PARTY_NOTICES.md` for bundled OOXML schemas and supporting code.
