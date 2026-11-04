# docx-review-tools

[日本語](README_ja.md)

Claude Code plugins and standalone Python scripts for applying review results to
Microsoft Word `.docx` files. Processing is local; the tools do not send document
content to a service.

The repository contains two independently installable plugins:

- `docx-apply-redline` applies `old_text` / `new_text` JSONL records as native
  tracked changes. Its character-level diff produces small revisions in text that
  has no spaces between words, including Japanese.
- `docx-add-comment` attaches comments to exact `anchor_text` matches without
  changing the document's visible text.

Both tools preserve the input file, write a separate output, refuse ambiguous
matches by default, and verify the resulting OOXML package before publishing it.

## Install in Claude Code

```text
/plugin marketplace add akchan/docx-review-tools
/plugin install docx-apply-redline@docx-review-tools
/plugin install docx-add-comment@docx-review-tools
```

Install only the plugin you need. Python 3.11 or later and the packages in
`requirements.txt` are required.

## Use without Claude Code

```bash
python3 plugins/docx-apply-redline/skills/docx-apply-redline/scripts/apply_redline.py \
  --docx manuscript.docx --findings findings.jsonl --out reviewed.docx

python3 plugins/docx-add-comment/skills/docx-add-comment/scripts/apply_comment.py \
  --docx manuscript.docx --comments comments.jsonl --out commented.docx
```

Examples of the JSONL input:

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
short public-domain literary excerpts. No private manuscripts or pre-generated
document fixtures are stored in this repository. See `TEST_DATA.md` for sources.

## License

MIT. See `THIRD_PARTY_NOTICES.md` for bundled OOXML schemas and supporting code.
