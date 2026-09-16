"""Read a PDF and pull out the metadata the VICAR Lit index needs.

Extraction runs on the file itself. Nothing here reads a prior manifest or spreadsheet, so a
paper's row always describes the copy sitting in ``pdfs/``.
"""

import os
import re
import subprocess

MIN_USABLE_BYTES = 10_000
MIN_USABLE_CHARS = 200
DEFAULT_TEXT_PAGES = 3
PDFTOTEXT_TIMEOUT_SECONDS = 60
MAX_TITLE_CHARS = 400
MAX_ABSTRACT_CHARS = 4000

# How much usable text a PDF yielded, recorded so a scanned report is visible as one.
TEXT_QUALITY_TEXT = "text"
TEXT_QUALITY_SCANNED = "low text (likely scanned)"

# A DOI is "10." then a registrant code, a slash, and a suffix that stops at whitespace.
DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>,;]+)", re.IGNORECASE)
# Trailing punctuation that sentence-ends a DOI rather than belonging to it.
DOI_TRAILING_JUNK = ".,;:)]}>'\""

ABSTRACT_PATTERN = re.compile(
    r"\bA\s?B\s?S\s?T\s?R\s?A\s?C\s?T\b|\bAbstract\b|\bSUMMARY\b|\bSummary\b"
)
ABSTRACT_END_PATTERN = re.compile(
    r"\b(Keywords?|KEY\s?WORDS?|Introduction|INTRODUCTION|1\.\s*Introduction)\b"
)

# Lines that appear above a title on a publisher's first page and are never the title.
TITLE_NOISE = re.compile(
    r"^\s*(www\.|http|doi:|DOI|Downloaded|Vol\.|Volume|ISSN|©|Copyright|Received|Accepted"
    r"|Published|ORIGINAL|RESEARCH ARTICLE|REVIEW|Open Access|Frontiers in|MARINE ECOLOGY"
    r"|PLOS|Contents lists|journal homepage|Article|ARTICLE|Citation:|Editor:|page \d)",
    re.IGNORECASE,
)


class UnreadablePdf(Exception):
    """Raised when a file cannot be treated as a paper.

    The message states the path and the reason so a run log names the failure exactly.
    """


def opens_as_pdf(path):
    """Check that a PDF parses and holds at least one page.

    Parameters:
        path (str): filesystem path to the PDF.

    Returns:
        tuple[bool, str]: ``(True, "")`` when it parses, otherwise ``(False, reason)``.
    """
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(path).pages)
    except Exception as error:
        return False, f"cannot be parsed as a PDF: {error}"
    if pages < 1:
        return False, "holds no pages"
    return True, ""


def check_readable(path):
    """Decide whether a file is a usable PDF.

    A file passes when it exists, carries the PDF magic bytes, clears the minimum size, and
    either yields text or parses as a real PDF. Scanned reports with an image-only cover page
    pass on the second route, because a scan is still library content; its thin text is
    reported through :data:`TEXT_QUALITY_SCANNED` so the row can be filled in by hand.

    Parameters:
        path (str): filesystem path to the candidate file.

    Returns:
        tuple[bool, str]: ``(True, "")`` when usable, otherwise ``(False, reason)``.
    """
    usable, _, reason = inspect(path)
    return usable, reason


def inspect(path):
    """Judge a candidate file and report how much text it yields.

    Parameters:
        path (str): filesystem path to the candidate file.

    Returns:
        tuple[bool, str, str]: whether the file is usable, its text quality
        (:data:`TEXT_QUALITY_TEXT` or :data:`TEXT_QUALITY_SCANNED`), and the rejection reason.
    """
    if not os.path.exists(path):
        return False, "", f"file does not exist: {path}"
    if os.path.isdir(path):
        return False, "", f"is a directory, not a file: {path}"
    size = os.path.getsize(path)
    if size == 0:
        return False, "", f"file is empty (0 bytes): {path}"
    try:
        with open(path, "rb") as handle:
            magic = handle.read(5)
    except OSError as error:
        return False, "", f"cannot read file: {path}: {error}"
    if magic[:4] != b"%PDF":
        return False, "", f"not a PDF, magic bytes were {magic!r}: {path}"
    text = read_text(path, pages=DEFAULT_TEXT_PAGES)
    if len(text.strip()) >= MIN_USABLE_CHARS:
        # A small file that still yields real text is a real paper, so size alone never rejects.
        return True, TEXT_QUALITY_TEXT, ""

    if size < MIN_USABLE_BYTES:
        return False, "", (
            f"file is only {size} bytes and yielded {len(text.strip())} characters of text: {path}"
        )

    parses, why_not = opens_as_pdf(path)
    if parses:
        return True, TEXT_QUALITY_SCANNED, ""
    return False, "", f"{why_not}, and yielded only {len(text.strip())} characters of text: {path}"


def read_text(path, pages=DEFAULT_TEXT_PAGES):
    """Extract text from the first pages of a PDF.

    ``pdftotext`` runs first because it is fast and layout-aware. When it is missing or fails,
    pypdf is used instead. A failure in both returns an empty string rather than raising, so a
    caller can decide whether empty text disqualifies the file.

    Parameters:
        path (str): filesystem path to the PDF.
        pages (int): how many leading pages to read.

    Returns:
        str: the extracted text, empty when neither extractor produced anything.
    """
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(pages), "-q", path, "-"],
            capture_output=True,
            timeout=PDFTOTEXT_TIMEOUT_SECONDS,
        )
        text = result.stdout.decode("utf-8", errors="replace")
        if text.strip():
            return text
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        chunks = []
        for page in reader.pages[:pages]:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks)
    except Exception:
        return ""


# Registrants that mint DOIs for datasets and preprint archives. A paper's data-availability
# statement names one of these, and taking it as the paper's own DOI matches the wrong record.
DATA_REPOSITORY_PREFIXES = ("10.5061/", "10.5281/", "10.6084/", "10.17632/", "10.25573/")
MIN_DOI_SUFFIX_LENGTH = 8


def plausible_doi(doi):
    """Decide whether a DOI looks complete enough to look up.

    A DOI broken across a line break yields a stub such as ``10.1371/journal``, which resolves
    to nothing but still matches loosely in a search, so it must be rejected here.

    Parameters:
        doi (str | None): a candidate DOI.

    Returns:
        bool: True when the DOI is worth looking up.
    """
    if not doi or "/" not in doi:
        return False
    suffix = doi.split("/", 1)[1]
    if not suffix:
        return False
    # A real suffix is either long or carries a number. "journal" is neither.
    return len(suffix) >= MIN_DOI_SUFFIX_LENGTH or any(c.isdigit() for c in suffix)


def find_doi(text, allow_data_repositories=False):
    """Find the paper's own DOI in a block of text.

    Every DOI on the page is considered, in order. Truncated stubs are skipped, and so are
    dataset DOIs unless nothing else is available.

    Parameters:
        text (str | None): text to scan.
        allow_data_repositories (bool): when True, accept a dataset DOI as a last resort.

    Returns:
        str: the lowercased DOI, or an empty string when none looked usable.
    """
    if not text:
        return ""
    fallback = ""
    for match in DOI_PATTERN.finditer(text):
        doi = match.group(1).rstrip(DOI_TRAILING_JUNK)
        # A DOI that ends in a stripped bracket may have swallowed an opening one too.
        if doi.count("(") < doi.count(")"):
            doi = doi.rsplit(")", 1)[0]
        doi = doi.lower()
        if not plausible_doi(doi):
            continue
        if doi.startswith(DATA_REPOSITORY_PREFIXES):
            fallback = fallback or doi
            continue
        return doi
    return fallback if allow_data_repositories else ""


# Values publishing software writes into /Title that are never a paper's title.
PLACEHOLDER_TITLES = frozenset(
    [
        "untitled",
        "overleaf example",
        "manuscript",
        "paper",
        "draft",
        "document",
        "article",
        "main",
        "template",
        "print",
        "final",
    ]
)
DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".indd", ".qxd", ".tex", ".rtf", ".odt")
MAX_DIGIT_SHARE = 0.30
MIN_TITLE_WORDS = 2


def looks_like_filename(value):
    """Decide whether a metadata string is a filename or a production job id.

    Producers routinely write things like ``ecol-90-02-19 506..516``, ``Olinger_thesis_0105``
    or ``se230101893p`` into /Title. Treating those as titles corrupts both the row key and
    the OpenAlex lookup, so they are rejected here.

    Parameters:
        value (str | None): the candidate string.

    Returns:
        bool: True when the string should not be trusted as a title.
    """
    if not value:
        return True
    text = str(value).strip()
    lowered = text.lower()
    if lowered in PLACEHOLDER_TITLES or lowered.endswith(DOCUMENT_EXTENSIONS):
        return True
    if lowered.startswith("microsoft word -"):
        return True
    if ".." in text:  # page-range artifacts such as "506..516"
        return True
    if re.fullmatch(r"[\w\-]{0,8}", text):
        return True
    letters = sum(1 for c in text if c.isalpha())
    digits = sum(1 for c in text if c.isdigit())
    if letters == 0:
        return True
    if digits / (letters + digits) > MAX_DIGIT_SHARE:
        return True
    if "_" in text and " " not in text:  # snake_case filenames
        return True
    words = [w for w in re.findall(r"[A-Za-z]{3,}", text)]
    return len(words) < MIN_TITLE_WORDS


# Journal running heads and front-matter banners read like long titles but are not titles.
RUNNING_HEAD_PATTERNS = [
    re.compile(r"\bpp\.?\s*\d+", re.IGNORECASE),            # "pp. 506-516"
    re.compile(r"\d{1,4}\s*\(\d{1,3}\)\s*,?\s*(19|20)\d{2}"),  # "90(2), 2009"
    re.compile(r"[\u00a9\u00d3]\s*(19|20)\d{2}"),           # a copyright mark and a year
    re.compile(r"\bby the\b.{0,40}\bSociety\b", re.IGNORECASE),
    re.compile(r"^AN ABSTRACT OF THE\b", re.IGNORECASE),
    re.compile(r"^PROJECT DESCRIPTION\b", re.IGNORECASE),
    re.compile(r"^(arXiv|preprint|submitted to)\b", re.IGNORECASE),
    re.compile(r"\bAll rights reserved\b", re.IGNORECASE),
    re.compile(r"\bdownloaded from\b", re.IGNORECASE),
    re.compile(r"NIH Public Access|Author Manuscript|available in PMC", re.IGNORECASE),
    re.compile(r"\bPublished in final edited form\b", re.IGNORECASE),
]


def looks_like_running_head(value):
    """Decide whether a string is a journal running head, banner or front-matter line.

    A first page often carries "Ecology, 90(2), 2009, pp. 506-516" above the real title. Those
    lines are long and word-rich, so a length test alone lets them through.

    Parameters:
        value (str | None): the candidate string.

    Returns:
        bool: True when the string is page furniture rather than a title.
    """
    if not value:
        return False
    text = str(value).strip()
    return any(pattern.search(text) for pattern in RUNNING_HEAD_PATTERNS)


def looks_like_producer(value):
    """Decide whether a metadata /Author string is software or an organization, not a person.

    Parameters:
        value (str | None): the candidate author string.

    Returns:
        bool: True when the string should not be trusted as an author list.
    """
    if not value:
        return True
    text = str(value).strip()
    # Word boundaries matter here: without them "Toscano" and "Francescangelli" matched
    # "scan" and good author lists were thrown away.
    if re.search(
        r"\b(microsoft|acrobat|adobe|pdf|latex|writer|scanner|scanned|copyright)\b"
        r"|\b(word|press)\s+(processor|inc\.?|ltd\.?)\b"
        r"|\bUniv(ersity)?\.?\s+of\s+[\w\s]{3,30}\bPress\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # An affiliation block is not an author list, however many commas it carries.
    if text.startswith(("{", "(", "[")) or "@" in text:
        return True
    if re.search(
        r"\b(Department|Institute|Institution|School|Faculty|Laborator(y|ies)|Cent(er|re)\s+for"
        r"|Program\b|Administration|Ministry|Survey\b)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # A real author field names at least two words, or one surname with a comma.
    if " " not in text and "," not in text:
        return True
    if text.isupper() and "," not in text:
        return True
    return False


def read_pdf_info(path):
    """Read the embedded document information dictionary of a PDF, discarding junk values.

    Parameters:
        path (str): filesystem path to the PDF.

    Returns:
        dict: the keys ``title``, ``author`` and ``year``, each possibly empty.
    """
    info = {"title": "", "author": "", "year": ""}
    try:
        from pypdf import PdfReader

        meta = PdfReader(path).metadata or {}
        info["title"] = str(meta.get("/Title") or "").strip()
        info["author"] = str(meta.get("/Author") or "").strip()
        raw_date = str(meta.get("/CreationDate") or "")
        year_match = re.search(r"(19|20)\d{2}", raw_date)
        info["year"] = year_match.group(0) if year_match else ""
    except Exception:
        pass
    if looks_like_filename(info["title"]):
        info["title"] = ""
    if looks_like_producer(info["author"]):
        info["author"] = ""
    return info


def guess_title_from_text(text):
    """Pick the most likely title from the top of a paper's first page.

    The first run of non-noise lines is treated as the title, which matches how nearly every
    journal sets a first page.

    Parameters:
        text (str | None): first-page text.

    Returns:
        str: the guessed title, empty when no plausible line was found.
    """
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    collected = []
    for line in lines[:40]:
        if not line:
            if collected:
                break
            continue
        if TITLE_NOISE.match(line):
            if collected:
                break
            continue
        if len(line) < 6:
            continue
        # An author line is mostly initials, commas and superscript digits.
        if collected and re.fullmatch(r"[A-Z][a-zA-Z.\-\s,;*0-9†‡§]+", line) and line.count(",") >= 2:
            break
        collected.append(line)
        if len(" ".join(collected)) > 180:
            break
    title = " ".join(collected).strip()
    title = re.sub(r"\s+", " ", title)
    return title[:MAX_TITLE_CHARS]


def guess_authors_from_text(text, title=""):
    """Find the author line that sits under the title on a paper's first page.

    An author line is short, carries several capitalized names separated by commas, semicolons
    or "and", and holds no sentence punctuation. When OpenAlex knows the paper its author list
    replaces this guess, so this only has to be good enough for papers OpenAlex does not know.

    Parameters:
        text (str | None): first-page text.
        title (str): the already-guessed title, so its own lines are skipped.

    Returns:
        str: the guessed author line, empty when none was found.
    """
    if not text:
        return ""
    title_words = set(re.findall(r"[a-z0-9]+", str(title).lower()))
    for line in [ln.strip() for ln in text.splitlines()[:30]]:
        if not line or len(line) > 300 or len(line) < 5:
            continue
        if TITLE_NOISE.match(line):
            continue
        line_words = set(re.findall(r"[a-z0-9]+", line.lower()))
        # Skip any line that is mostly the title repeated.
        if title_words and len(line_words & title_words) > len(line_words) / 2:
            continue
        if not re.search(r"[,;]|\band\b|&", line):
            continue
        # A sentence-shaped line is prose, not an author list. Initials ("Smith, T. B.") look
        # like sentence breaks, so only a long line is judged on that basis.
        sentence_like = bool(re.search(r"[.!?]\s+[A-Z][a-z]{2,}", line))
        if len(line) > 120 and (sentence_like or line.endswith((".", "!", "?"))):
            continue
        # Strip affiliation superscripts and footnote marks before judging the tokens.
        cleaned = re.sub(r"[0-9*†‡§¶]+", "", line).strip(" ,;")
        tokens = [t for t in re.split(r"[,;]|\band\b|&", cleaned) if t.strip()]
        if len(tokens) < 2:
            continue
        capitalized = sum(1 for t in tokens if re.match(r"\s*[A-Z]", t))
        if capitalized >= max(2, int(len(tokens) * 0.6)):
            return re.sub(r"\s+", " ", cleaned).strip()
    return ""


def guess_abstract(text):
    """Pull the abstract out of a paper's opening pages.

    Parameters:
        text (str | None): text from the leading pages.

    Returns:
        str: the abstract, trimmed to a sane length, empty when no abstract was located.
    """
    if not text:
        return ""
    start = ABSTRACT_PATTERN.search(text)
    if not start:
        return ""
    body = text[start.end():]
    end = ABSTRACT_END_PATTERN.search(body)
    if end:
        body = body[: end.start()]
    body = re.sub(r"\s+", " ", body).strip(" :.-—")
    return body[:MAX_ABSTRACT_CHARS]


def parse_filename_hints(path):
    """Read author and year hints out of an existing filename.

    Many source folders already name files ``Author_Year_Topic.pdf`` or
    ``Author et al. - 2019 - Title.pdf``. Those hints fill gaps the text leaves.

    Parameters:
        path (str): filesystem path to the PDF.

    Returns:
        dict: the keys ``author``, ``year`` and ``title``, each possibly empty.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    hints = {"author": "", "year": "", "title": ""}
    year_match = re.search(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)", stem)
    if year_match:
        hints["year"] = year_match.group(1)
    author_match = re.match(r"\s*([A-Z][A-Za-z'\-]+)", stem)
    if author_match:
        hints["author"] = author_match.group(1)
    # "Author et al. - 2019 - Title" keeps the title in the third dash-separated field.
    dash_parts = [p.strip() for p in stem.split(" - ")]
    if len(dash_parts) >= 3:
        hints["title"] = dash_parts[2]
    return hints


def extract(path):
    """Build the best metadata guess for one PDF, from the file alone.

    Parameters:
        path (str): filesystem path to the PDF.

    Returns:
        dict: the keys ``path``, ``title``, ``authors``, ``year``, ``doi``, ``abstract``
        and ``text_chars``.

    Raises:
        UnreadablePdf: when the file is not a usable PDF.
    """
    usable, text_quality, reason = inspect(path)
    if not usable:
        raise UnreadablePdf(reason)

    text = read_text(path, pages=DEFAULT_TEXT_PAGES)
    info = read_pdf_info(path)
    hints = parse_filename_hints(path)

    # Two independent title candidates. The metadata one is cleaner when it is real; the
    # text one rescues files whose metadata was junk. Both are handed to the enricher, so a
    # weak first guess still gets a second chance at matching a record.
    from_text = guess_title_from_text(text)
    # Every candidate passes the junk filter here as well as at its source, so a title can
    # never reach a row just because it arrived by a different route.
    candidates = [
        c for c in (info["title"], from_text, hints["title"])
        if not looks_like_filename(c) and not looks_like_running_head(c)
    ]
    title = candidates[0] if candidates else (from_text or hints["title"] or info["title"])
    title_alt = next((c for c in candidates[1:] if c != title), "")
    author_candidates = [
        c for c in (info["author"], guess_authors_from_text(text, title), hints["author"])
        if c and not looks_like_producer(c)
    ]
    authors = author_candidates[0] if author_candidates else ""
    year = info["year"] or hints["year"]
    # A creation-date year can post-date publication, so trust the text when it names one.
    text_year = ""
    year_in_text = re.search(r"\((19|20)\d{2}\)", text[:3000])
    if year_in_text:
        text_year = year_in_text.group(0).strip("()")

    return {
        "path": path,
        "title": re.sub(r"\s+", " ", str(title)).strip()[:MAX_TITLE_CHARS],
        "authors": authors,
        "year": text_year or year,
        "doi": find_doi(text),
        "abstract": guess_abstract(text),
        "title_alt": re.sub(r"\s+", " ", str(title_alt or "")).strip()[:MAX_TITLE_CHARS],
        "text_chars": len(text),
        "text_quality": text_quality,
    }
