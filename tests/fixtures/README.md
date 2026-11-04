# Word-authored fixtures

Four documents made by hand in Microsoft Word for Mac. They are committed (see
the exception in `.gitignore`) because they carry markup only Word writes:
`mc:Ignorable` over about ten extension namespaces, real `w14:paraId` values,
revision-save IDs, the styles/settings/theme parts and, in the annotated pair,
`comments.xml` with `commentsExtended`, `commentsIds`, `commentsExtensible` and
`people.xml`. `tests/test_word_fixtures.py` runs the tool over them.

| File | Contents |
| --- | --- |
| `ja-plain.docx` | Three paragraphs under a Heading 1, one bold phrase, one italic phrase |
| `ja-plain-annotated.docx` | The same, plus a tracked insertion, a tracked deletion and a comment |
| `en-plain.docx` | As above, in English |
| `en-plain-annotated.docx` | As above, in English |

The text is from 夏目漱石『吾輩は猫である』 and Lewis Carroll, *Alice's
Adventures in Wonderland*; see `TEST_DATA.md`.

The annotated pair was made with Track Changes on and left unaccepted. Its
revisions and comment are by `Reviewer A`, standing in for another reviewer, so
the tests can check that a run leaves someone else's work alone.

## Remaking one

1. In Word → Settings → User Information, set the name to `Reviewer A`. Word
   writes it into every tracked change, comment and `people.xml`.
2. Type the text, apply Heading 1 and the bold/italic runs, and for the
   annotated pair turn on Track Changes before editing. Save as `.docx`.
3. Remove the remaining metadata (creator, editing time, dates, document IDs):

```sh
python3 tools/strip_docx_metadata.py tests/fixtures/*.docx
```

The test suite runs the same tool with `--check`, so a fixture that still
carries metadata or a real name fails the build.
