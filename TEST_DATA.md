# Test data

Most DOCX inputs are created at runtime. The exception is the four documents in
`tests/fixtures/`, made by hand in Microsoft Word so the tests see markup only
Word writes; their author names are placeholders and their personal metadata
(creator, editing time, dates, document IDs) has been removed with
`tools/strip_docx_metadata.py`. No manuscript files are stored in the
repository.

The Japanese excerpts are from Natsume Soseki's *Wagahai wa Neko de Aru* (*I Am
a Cat*), first published in 1905–1906. Aozora Bunko identifies the work and its
source transcription. Its policy permits redistribution of files for works whose
copyright has expired.

- https://www.aozora.gr.jp/cards/000148/card789.html
- https://www.aozora.gr.jp/guide/kijyunn.html

The English excerpts are from Lewis Carroll's *Alice's Adventures in
Wonderland*, first published in 1865. Project Gutenberg identifies the work as
public domain in the USA.

- https://www.gutenberg.org/ebooks/11

Test changes and comments are deliberately artificial and are not presented as
corrections to either work. Users redistributing the test data should check the
law in their jurisdiction.
