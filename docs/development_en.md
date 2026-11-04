# Development

Japanese version: [development.md](development.md)

Run the commands from the repository root.

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
claude plugin validate .
```

Most test DOCX files are generated during each test run; four documents made in
Word, with their personal metadata removed, live in `tests/fixtures/`. The text
comes from short excerpts of Natsume Soseki's *I Am a Cat* and Lewis Carroll's *Alice's
Adventures in Wonderland*, both in the public domain. See [TEST_DATA.md](../TEST_DATA.md) for
sources.

The figure at the top is regenerated with `python3 tools/render_readme_example.py`.
It builds a one-paragraph DOCX from the example sentence, applies the same
findings with this skill and with docx-cli (`bunx bun-docx@0.28.0`), and draws
the `w:del`/`w:ins` markup it reads back from both outputs. It needs Pillow,
`bunx` and a Japanese font.
