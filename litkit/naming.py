"""Construct the standard filename and row key for a paper in the VICAR Lit library.

The standard is ``FirstAuthor_Year_ShortTitle.pdf``. The stem of that filename is also the
row key in ``lit_index.csv``, so naming and identity stay in one place.
"""

import re
import unicodedata

MAX_TITLE_WORDS = 5
MIN_WORD_LENGTH = 3
UNKNOWN_YEAR = "nd"
UNKNOWN_AUTHOR = "Anon"
UNKNOWN_TITLE = "Untitled"
EARLIEST_PLAUSIBLE_YEAR = 1700
LATEST_PLAUSIBLE_YEAR = 2100

# Dropped from the short title because they carry no search value.
STOPWORDS = frozenset(
    """a an the and or of for to in on at by with from into over under between among
    is are was were be been being as that this these those its their his her our your
    it they we you i not no nor but if then than so such can could may might will would
    shall should do does did done using use used via towards toward about across after
    before during through within without new novel study studies case report reports
    analysis approach based""".split()
)


# Letters that carry no combining mark, so NFKD leaves them intact and the ASCII filter
# would delete them outright. Folding them keeps the surname readable.
STANDALONE_LETTERS = {
    "Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae", "Œ": "OE", "œ": "oe",
    "Ð": "D", "ð": "d", "Þ": "TH", "þ": "th", "ß": "ss",
    "Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ħ": "H", "ħ": "h",
}


def _strip_accents(text):
    """Fold accented characters to their closest ASCII equivalent.

    Parameters:
        text (str): any unicode string.

    Returns:
        str: the string with combining marks removed and non-ASCII bytes dropped.
    """
    folded = "".join(STANDALONE_LETTERS.get(ch, ch) for ch in str(text))
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_surname(raw):
    """Reduce an author surname to bare ASCII letters, dropping affiliation digits.

    Hyphens, apostrophes, spaces and accents are removed so that ``Alvarez-Filip`` and
    ``Álvarez Filip`` both yield ``AlvarezFilip``.

    Parameters:
        raw (str): a surname, possibly with particles, punctuation or accents.

    Returns:
        str: the folded surname, or ``Anon`` when nothing usable remains.
    """
    if not raw:
        return UNKNOWN_AUTHOR
    folded = _strip_accents(str(raw))
    # Keep the capital letters the author used; only remove what cannot go in a filename.
    kept = re.sub(r"[^A-Za-z0-9]+", "", folded)
    # A trailing digit is an affiliation marker ("KOBARA1"), never part of a surname.
    trimmed = re.sub(r"\d+$", "", kept)
    kept = trimmed or kept
    return kept if kept else UNKNOWN_AUTHOR


def _surname_from_full_name(name):
    """Take the surname from a name written in western order.

    Parameters:
        name (str): a name such as "Tyler B. Smith".

    Returns:
        str: the folded surname, or ``Anon``.
    """
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return UNKNOWN_AUTHOR
    for token in reversed(parts):
        if re.fullmatch(r"[A-Za-z]\.?", token):
            continue  # a bare initial is never the surname
        return clean_surname(token)
    return clean_surname(parts[-1])


def first_surname(authors):
    """Take the first author's surname from an author string or list.

    Handles both orders. ``Smith, Tyler B.`` is surname first, so the surname precedes the
    comma. ``Tyler Smith, Bob Jones`` is a western-order list, so the comma separates authors
    and the surname is the last word of the first one. The two are told apart by how many
    words precede the comma: a lone word is a surname, several words are a whole name.

    Parameters:
        authors (str | list | None): the author field in any of the common shapes.

    Returns:
        str: the folded surname of the first author, or ``Anon``.
    """
    if not authors:
        return UNKNOWN_AUTHOR
    if isinstance(authors, (list, tuple)):
        first = str(authors[0]) if authors else ""
    else:
        # Semicolons and "and" separate authors unambiguously, so split on them first.
        first = re.split(r"\s*(?:;|\band\b|&|\|)\s*", str(authors).strip())[0]
    first = first.strip().strip(",")
    if not first:
        return UNKNOWN_AUTHOR

    if "," in first:
        head = first.split(",")[0].strip()
        if len(re.findall(r"\S+", head)) == 1:
            return clean_surname(head)      # "Smith, Tyler B."
        return _surname_from_full_name(head)  # "Tyler Smith, Bob Jones"
    return _surname_from_full_name(first)


def clean_year(raw):
    """Normalize a publication year to four digits.

    Parameters:
        raw (str | int | None): a year, or text containing one.

    Returns:
        str: a four-digit year inside the plausible range, otherwise ``nd``.
    """
    if raw is None:
        return UNKNOWN_YEAR
    match = re.search(r"(1[6-9]\d{2}|20\d{2}|21\d{2})", str(raw))
    if not match:
        return UNKNOWN_YEAR
    year = int(match.group(1))
    if EARLIEST_PLAUSIBLE_YEAR <= year <= LATEST_PLAUSIBLE_YEAR:
        return str(year)
    return UNKNOWN_YEAR


def short_title(title, max_words=MAX_TITLE_WORDS):
    """Compress a title into CamelCase words suitable for a filename.

    Stopwords and words shorter than three characters are dropped, then the first
    ``max_words`` survivors are capitalized and joined.

    Parameters:
        title (str | None): the paper title.
        max_words (int): how many words to keep.

    Returns:
        str: the CamelCase short title, or ``Untitled`` when nothing usable remains.
    """
    if not title:
        return UNKNOWN_TITLE
    folded = _strip_accents(str(title)).lower()
    words = re.findall(r"[a-z0-9]+", folded)
    # Short words survive when they carry a digit, so "3D" and "pH" are not discarded.
    kept = [
        w
        for w in words
        if w not in STOPWORDS and (len(w) >= MIN_WORD_LENGTH or any(c.isdigit() for c in w))
    ]
    if not kept:
        # A title made entirely of stopwords still deserves a name.
        kept = words
    if not kept:
        return UNKNOWN_TITLE
    return "".join(w.capitalize() for w in kept[:max_words])


def build_key(authors, year, title):
    """Build the stable row key for a paper.

    Parameters:
        authors (str | list | None): the author field.
        year (str | int | None): the publication year.
        title (str | None): the paper title.

    Returns:
        str: ``FirstAuthor_Year_ShortTitle``.
    """
    return f"{first_surname(authors)}_{clean_year(year)}_{short_title(title)}"


def disambiguate(key, taken):
    """Append a letter suffix until the key is unique.

    The first collision becomes ``_b``, the next ``_c``, continuing to ``_z`` and then
    ``_aa`` style pairs, so the function never loops forever.

    Parameters:
        key (str): the desired key.
        taken (collections.abc.Container): keys already in use.

    Returns:
        str: a key not present in ``taken``.
    """
    if key not in taken:
        return key
    for ordinal in range(ord("b"), ord("z") + 1):
        candidate = f"{key}_{chr(ordinal)}"
        if candidate not in taken:
            return candidate
    counter = 2
    while True:
        candidate = f"{key}_{counter}"
        if candidate not in taken:
            return candidate
        counter += 1


def sanitize_key(key):
    """Strip anything from a key that could escape the PDF folder or break a filename.

    Keys built by :func:`build_key` are already safe. This guards keys that arrive from a
    hand-written corrections file, where a stray path would otherwise reach ``os.rename``.

    Parameters:
        key (str | None): a proposed key.

    Returns:
        str: the key reduced to letters, digits and underscores, empty when nothing survives.
    """
    if not key:
        return ""
    folded = _strip_accents(str(key))
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", folded).strip("_")
    return re.sub(r"_{2,}", "_", cleaned)


def build_filename(key):
    """Turn a row key into the PDF filename that carries it.

    Parameters:
        key (str): a row key from :func:`build_key`.

    Returns:
        str: the key with a ``.pdf`` extension.
    """
    return f"{key}.pdf"


def normalize_title(title):
    """Reduce a title to a comparison form used for deduplication.

    Parameters:
        title (str | None): the paper title.

    Returns:
        str: lowercase alphanumeric words joined by single spaces, empty when no title.
    """
    if not title:
        return ""
    folded = _strip_accents(str(title)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", folded))
