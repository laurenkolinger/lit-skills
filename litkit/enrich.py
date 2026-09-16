"""Enrich a paper's metadata from OpenAlex and label its citation impact.

OpenAlex is free and needs no key. It supplies the canonical title, author list, year,
journal, DOI and, most importantly for this library, a live citation count.
"""

import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

OPENALEX_BASE = "https://api.openalex.org/works"
# OpenAlex asks callers to identify themselves; a mailto puts the request in the polite pool.
DEFAULT_CONTACT_EMAIL = "lit@vicar.invalid"
CONTACT_EMAIL_VAR = "VICAR_LIT_CONTACT_EMAIL"


def contact_email():
    """Return the address sent to OpenAlex to identify the caller.

    OpenAlex asks callers to identify themselves and gives a faster pool to those who do.
    The address is read from the environment so a shared library does not carry one person's
    personal address in its source.

    Returns:
        str: the configured address, or a neutral default.
    """
    return os.environ.get(CONTACT_EMAIL_VAR, "").strip() or DEFAULT_CONTACT_EMAIL


def user_agent():
    """Return the User-Agent header sent to OpenAlex.

    Returns:
        str: a header naming the tool and the contact address.
    """
    return f"VICAR-Lit/1.0 (mailto:{contact_email()})"
REQUEST_TIMEOUT_SECONDS = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
TITLE_MATCH_THRESHOLD = 0.82
# Below this, the record OpenAlex returned describes something other than the PDF we read.
# That happens when the DOI came off a data statement or a neighbouring article in a reprint.
# A match this far from our own title is rejected outright rather than merely noted, because a
# row naming the wrong paper is worse than a row with no citation count.
DOI_TITLE_REJECT_FLOOR = 0.34
# A title we extracted ourselves is only worth arguing with when it is substantial. Anything
# shorter is probably a running head, so a disagreement says nothing.
MIN_CONFIDENT_TITLE_WORDS = 5

SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "cited_by_count",
        "authorships",
        "primary_location",
        "type",
        "topics",
        "open_access",
    ]
)

# Citation thresholds. Totals catch the classics; per-year rates give recent work a fair read.
HIGH_IMPACT_TOTAL = 500
HIGH_IMPACT_PER_YEAR = 40
WELL_CITED_TOTAL = 100
WELL_CITED_PER_YEAR = 15
STANDARD_TOTAL = 10
EMERGING_MAX_AGE_YEARS = 3

IMPACT_HIGH = "high impact"
IMPACT_WELL_CITED = "well cited"
IMPACT_STANDARD = "standard"
IMPACT_EMERGING = "emerging"
IMPACT_LOW = "low"
IMPACT_UNRATED = "unrated"


def _normalized_words(text):
    """Reduce text to a set of lowercase alphanumeric words.

    Parameters:
        text (str | None): any text.

    Returns:
        set[str]: the distinct words, empty when there is no text.
    """
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def title_similarity(left, right):
    """Measure how much two titles overlap, as a Jaccard index over words.

    Parameters:
        left (str | None): one title.
        right (str | None): the other title.

    Returns:
        float: 0.0 when nothing overlaps, 1.0 when the word sets are identical.
    """
    a, b = _normalized_words(left), _normalized_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def looks_unreliable(title):
    """Decide whether an extracted title is too weak to contradict a DOI match.

    Parameters:
        title (str | None): the title read off the PDF.

    Returns:
        bool: True when the title is page furniture rather than a real title.
    """
    from . import extract

    return extract.looks_like_filename(title) or extract.looks_like_running_head(title)


def _request_json(url):
    """Fetch and decode a JSON document, retrying on transient failures.

    Parameters:
        url (str): the fully formed request URL.

    Returns:
        dict | None: the decoded body, or None when every attempt failed.
    """
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # A 404 is a definite answer: this work is not in OpenAlex. Do not retry it.
            if error.code == 404:
                return None
            if error.code in (429, 500, 502, 503, 504) and attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None
    return None


def fetch_by_doi(doi):
    """Look a work up in OpenAlex by its DOI.

    Parameters:
        doi (str | None): a bare DOI such as ``10.1038/s41598-019-54681-2``.

    Returns:
        dict | None: the OpenAlex work record, or None when it was not found.
    """
    if not doi:
        return None
    clean = str(doi).strip().lower()
    clean = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", clean)
    if not clean.startswith("10."):
        return None
    url = f"{OPENALEX_BASE}/doi:{urllib.parse.quote(clean, safe='/')}?select={SELECT_FIELDS}&mailto={contact_email()}"
    return _request_json(url)


def fetch_by_title(title, year=None):
    """Search OpenAlex for a work by title, accepting only a confident match.

    A result is accepted when its title overlaps the query by at least
    ``TITLE_MATCH_THRESHOLD``, which keeps near-miss papers out of the index.

    Parameters:
        title (str | None): the paper title.
        year (str | int | None): the publication year, used only to break ties.

    Returns:
        dict | None: the matching work record, or None when nothing matched confidently.
    """
    if not title or len(str(title).strip()) < 12:
        return None
    query = urllib.parse.quote(str(title)[:250])
    url = (
        f"{OPENALEX_BASE}?filter=title.search:{query}"
        f"&select={SELECT_FIELDS}&per-page=5&mailto={contact_email()}"
    )
    payload = _request_json(url)
    if not payload or not payload.get("results"):
        return None
    best, best_score = None, 0.0
    for candidate in payload["results"]:
        score = title_similarity(title, candidate.get("display_name") or candidate.get("title"))
        if year and str(candidate.get("publication_year") or "") == str(year):
            score += 0.05
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= TITLE_MATCH_THRESHOLD else None


def parse_work(work):
    """Flatten an OpenAlex work record into the fields the index stores.

    Parameters:
        work (dict | None): an OpenAlex work record.

    Returns:
        dict: the keys ``title``, ``authors``, ``first_author``, ``year``, ``journal``,
        ``doi``, ``url``, ``citations`` and ``openalex_topics``. Empty when ``work`` is None.
    """
    blank = {
        "title": "",
        "authors": "",
        "first_author": "",
        "year": "",
        "journal": "",
        "doi": "",
        "url": "",
        "citations": "",
        "openalex_topics": "",
    }
    if not work:
        return blank

    authorships = work.get("authorships") or []
    names = [a.get("author", {}).get("display_name", "") for a in authorships]
    names = [n for n in names if n]

    location = work.get("primary_location") or {}
    source = location.get("source") or {}

    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    open_access = work.get("open_access") or {}
    url = location.get("landing_page_url") or open_access.get("oa_url") or ""
    if not url and doi:
        url = f"https://doi.org/{doi}"

    topics = [t.get("display_name", "") for t in (work.get("topics") or [])[:3]]

    return {
        "title": work.get("display_name") or work.get("title") or "",
        "authors": "; ".join(names),
        "first_author": names[0] if names else "",
        "year": str(work.get("publication_year") or ""),
        "journal": source.get("display_name") or "",
        "doi": doi,
        "url": url,
        "citations": work.get("cited_by_count"),
        "openalex_topics": "; ".join(t for t in topics if t),
    }


def citations_per_year(citations, year, today=None):
    """Compute a citation rate, using a one-year floor so new papers are not inflated.

    Parameters:
        citations (int | str | None): the total citation count.
        year (int | str | None): the publication year.
        today (datetime.date | None): the reference date, defaulting to today.

    Returns:
        float | None: citations per year rounded to one decimal, or None when it cannot
        be computed.
    """
    if citations in (None, "") or year in (None, ""):
        return None
    try:
        total = int(citations)
        published = int(str(year)[:4])
    except (TypeError, ValueError):
        return None
    reference = today or datetime.date.today()
    age = max(1, reference.year - published + 1)
    return round(total / age, 1)


def impact_label(citations, year, today=None):
    """Assign a citation impact label, adjusted for how long a paper has been out.

    Parameters:
        citations (int | str | None): the total citation count.
        year (int | str | None): the publication year.
        today (datetime.date | None): the reference date, defaulting to today.

    Returns:
        str: one of ``high impact``, ``well cited``, ``standard``, ``emerging``, ``low``
        or ``unrated``.
    """
    if citations in (None, ""):
        return IMPACT_UNRATED
    try:
        total = int(citations)
    except (TypeError, ValueError):
        return IMPACT_UNRATED
    if total < 0:
        return IMPACT_UNRATED

    rate = citations_per_year(total, year, today=today)
    if total >= HIGH_IMPACT_TOTAL or (rate is not None and rate >= HIGH_IMPACT_PER_YEAR):
        return IMPACT_HIGH
    if total >= WELL_CITED_TOTAL or (rate is not None and rate >= WELL_CITED_PER_YEAR):
        return IMPACT_WELL_CITED
    if total >= STANDARD_TOTAL:
        return IMPACT_STANDARD

    reference = today or datetime.date.today()
    try:
        age = reference.year - int(str(year)[:4])
    except (TypeError, ValueError):
        return IMPACT_LOW
    if 0 <= age <= EMERGING_MAX_AGE_YEARS:
        return IMPACT_EMERGING
    return IMPACT_LOW


def enrich(record, today=None):
    """Fill a extracted record with OpenAlex metadata and an impact label.

    OpenAlex values overwrite extracted guesses because they are canonical. Extracted values
    survive wherever OpenAlex has nothing, so a paper it does not know still keeps a usable row.

    Parameters:
        record (dict): the output of :func:`litkit.extract.extract`.
        today (datetime.date | None): the reference date, defaulting to today.

    Returns:
        dict: a new record carrying the merged metadata, the citation count, the retrieval
        date and the impact label.
    """
    merged = dict(record)
    work = fetch_by_doi(record.get("doi"))
    from_doi = work is not None
    if work is None:
        work = fetch_by_title(record.get("title"), record.get("year"))
    if work is None and record.get("title_alt") and record["title_alt"] != record.get("title"):
        # The first title candidate failed. Try the other one before giving up.
        work = fetch_by_title(record["title_alt"], record.get("year"))

    # Test the match BEFORE anything is committed. A DOI lookup is the risky one: the DOI may
    # have been scraped off a data-availability statement or a neighbouring article, and
    # OpenAlex will happily return that other paper's record.
    merged["metadata_mismatch"] = False
    if from_doi and work:
        candidate_title = (work.get("display_name") or work.get("title") or "")
        own_title = record.get("title") or ""
        confident = len(own_title.split()) >= MIN_CONFIDENT_TITLE_WORDS and not looks_unreliable(own_title)
        if candidate_title and confident:
            agreement = max(
                title_similarity(own_title, candidate_title),
                title_similarity(record.get("title_alt"), candidate_title),
            )
            if agreement < DOI_TITLE_REJECT_FLOOR:
                merged["metadata_mismatch"] = True
                merged["metadata_mismatch_detail"] = (
                    f"the DOI {record.get('doi')} resolves to {candidate_title[:80]!r}, "
                    f"but the PDF reads {own_title[:80]!r}; the match was rejected"
                )
                # The DOI belongs to another paper, so it must not stay on this row either.
                merged["doi"] = ""
                work = None
    parsed = parse_work(work)

    for field in ("title", "authors", "first_author", "year", "journal", "doi", "url"):
        if parsed.get(field):
            merged[field] = parsed[field]
        elif field == "doi" and merged.get("metadata_mismatch"):
            merged["doi"] = ""  # the rejected DOI stays gone
        else:
            merged.setdefault(field, record.get(field, "") or "")

    merged["citations"] = parsed["citations"] if parsed["citations"] is not None else ""
    merged["openalex_topics"] = parsed["openalex_topics"]
    merged["citations_retrieved"] = (
        (today or datetime.date.today()).isoformat() if merged["citations"] != "" else ""
    )
    rate = citations_per_year(merged["citations"], merged.get("year"), today=today)
    merged["citations_per_year"] = "" if rate is None else rate
    merged["impact"] = impact_label(merged["citations"], merged.get("year"), today=today)
    merged["matched_openalex"] = bool(work)
    return merged
