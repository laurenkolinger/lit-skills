"""Shared fixtures: builders that produce real PDFs, and deliberately broken files.

The suite generates its own PDFs so it runs anywhere, with no fixture files to keep in sync
and no dependency on the library's real contents.
"""

import os

import pytest

FONT_SIZE = 11
LINE_HEIGHT = 15
PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT_MARGIN = 56
TOP_START = 720
PADDING_TARGET_BYTES = 12_000


def _escape(text):
    """Escape a string for a PDF literal string object."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_pdf(path, lines, pad_to=PADDING_TARGET_BYTES):
    """Write a valid single-page PDF containing the given lines of text.

    The file is padded with a trailing comment so it clears the library's minimum size
    floor, which real papers always clear and stub files never do.

    Parameters:
        path (str): where to write the PDF.
        lines (list[str]): the text lines to draw, top to bottom.
        pad_to (int): pad the file out to at least this many bytes.

    Returns:
        str: the path written.
    """
    drawn = []
    y = TOP_START
    for line in lines:
        drawn.append(f"BT /F1 {FONT_SIZE} Tf {LEFT_MARGIN} {y} Td ({_escape(line)}) Tj ET")
        y -= LINE_HEIGHT
    stream = "\n".join(drawn).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("latin-1"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n".encode()
    )
    out += b"%%EOF\n"

    if pad_to and len(out) < pad_to:
        out += b"%" + b"p" * (pad_to - len(out) - 2) + b"\n"

    with open(path, "wb") as handle:
        handle.write(bytes(out))
    return path


def make_paper(path, title, authors, year, doi, abstract, pad_to=PADDING_TARGET_BYTES):
    """Write a PDF laid out like a journal first page.

    Parameters:
        path (str): where to write the PDF.
        title (str): the paper title, drawn first.
        authors (str): the author line.
        year (str | int): the year, drawn in a citation-style parenthesis.
        doi (str): the DOI, drawn on its own line.
        abstract (str): the abstract body.
        pad_to (int): pad the file to at least this many bytes.

    Returns:
        str: the path written.
    """
    lines = [title, "", authors, f"Published ({year})", f"doi:{doi}", "", "Abstract"]
    words, current = abstract.split(), ""
    for word in words:
        if len(current) + len(word) + 1 > 90:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    lines += ["", "Keywords: coral reef, monitoring", "", "Introduction"]
    return make_pdf(path, lines, pad_to=pad_to)


@pytest.fixture
def paper_factory(tmp_path):
    """Return a function that writes a journal-style PDF into the test's temp folder."""

    def _factory(name, title="A study of coral reef resilience in the Caribbean",
                 authors="Smith, Tyler B.; Brandt, Marilyn E.", year=2016,
                 doi="10.1234/test.0001",
                 abstract="We surveyed coral reefs across the shelf and found that cover declined "
                          "by 30 percent over the study period, with the steepest losses at the "
                          "shallowest sites.",
                 pad_to=PADDING_TARGET_BYTES):
        return make_paper(
            str(tmp_path / name), title, authors, year, doi, abstract, pad_to=pad_to
        )

    return _factory


@pytest.fixture
def broken_files(tmp_path):
    """Create a folder of files that must never reach the index.

    Returns:
        dict: label to path for each deliberately broken file.
    """
    folder = tmp_path / "broken"
    folder.mkdir()
    made = {}

    empty = folder / "empty.pdf"
    empty.write_bytes(b"")
    made["empty"] = str(empty)

    stub = folder / "stub.pdf"
    stub.write_bytes(b"%PDF-1.4\n")
    made["stub"] = str(stub)

    not_a_pdf = folder / "actually_html.pdf"
    not_a_pdf.write_bytes(b"<html><body>Access denied</body></html>" + b" " * 20_000)
    made["not_a_pdf"] = str(not_a_pdf)

    truncated = folder / "truncated.pdf"
    good = make_pdf(str(folder / "_source.pdf"), ["Title line", "body text " * 200])
    with open(good, "rb") as handle:
        head = handle.read()
    truncated.write_bytes(head[: len(head) // 3])
    os.remove(good)
    made["truncated"] = str(truncated)

    made["missing"] = str(folder / "does_not_exist.pdf")
    return made
