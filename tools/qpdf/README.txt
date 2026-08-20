Optional qpdf bundle
====================

DocCipher Breaker handles PDFs with PyMuPDF, which is compiled into the
executable. You do not need qpdf.

If you would rather have qpdf used (it rewrites the PDF byte-faithfully and
preserves structure, forms and bookmarks exactly), download a Windows build
from https://github.com/qpdf/qpdf/releases and extract it here so that:

    tools/qpdf/bin/qpdf.exe

exists. The installer picks it up automatically, and the app prefers it over
PyMuPDF when present. If this directory has no qpdf.exe, nothing is bundled and
PyMuPDF is used.

qpdf is distributed under the Apache License 2.0. If you bundle it, include its
LICENSE file alongside the binary.
