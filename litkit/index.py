"""Read, write and deduplicate the VICAR Lit index.

The index is a CSV with one row per PDF in ``pdfs/``. It is the source of truth; the
spreadsheet is built from it and never the other way round.
"""

import csv
import datetime
import os
import re
import sys

from . import naming

INDEX_FILENAME = "lit_index.csv"
PDF_DIRNAME = "pdfs"
INGEST_DIRNAME = "ingest"
PROCESSED_DIRNAME = "_processed"
LOG_DIRNAME = "logs"

COLUMNS = [
    "link",
    "key",
    "filename",
    "authors",
    "first_author",
    "year",
    "title",
    "journal",
    "doi",
    "url",
    "citations",
    "citations_retrieved",
    "citations_per_year",
    "impact",
    "tags_all",
    "topic_tags",
    "method_tags",
    "region_tags",
    "taxa_tags",
    "vicar_relevance",
    "summary",
    "key_findings",
    "source",
    "date_added",
    "notes",
]

# A leading one of these turns a spreadsheet cell into a formula. Prefixing neutralizes it.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
CSV_FIELD_SIZE_LIMIT = 10_000_000


def _raise_field_size_limit():
    """Raise the csv module's field cap so long abstracts do not abort a read.

    Returns:
        None
    """
    limit = CSV_FIELD_SIZE_LIMIT
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 2


TAG_FACETS = ("topic_tags", "method_tags", "region_tags", "taxa_tags", "vicar_relevance")
# Pipes around every tag let a spreadsheet match a whole tag with SEARCH("|AUV|", ...),
# which a plain comma-separated list cannot do without also matching "AUV survey".
TAG_DELIMITER = "|"


def build_tags_all(row):
    """Join every tag on a row into one searchable, pipe-delimited cell.

    Parameters:
        row (dict): an index row.

    Returns:
        str: the tags wrapped in pipes, empty when the row carries none.
    """
    tags = []
    for facet in TAG_FACETS:
        for tag in str(row.get(facet) or "").split(","):
            tag = tag.strip()
            if tag and tag not in tags:
                tags.append(tag)
    if not tags:
        return ""
    return TAG_DELIMITER + TAG_DELIMITER.join(tags) + TAG_DELIMITER


def sanitize_cell(value):
    """Make a value safe and flat for a spreadsheet cell.

    Newlines and tabs collapse to spaces so a row stays one row, and a leading formula
    trigger is escaped with a single quote so a spreadsheet shows text rather than evaluating it.

    Parameters:
        value: any value destined for a cell.

    Returns:
        str: the cleaned string.
    """
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"[\r\n\t  ]+", " ", text)
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if text.startswith(FORMULA_TRIGGERS):
        text = "'" + text
    return text


def normalize_doi(doi):
    """Reduce a DOI to the bare lowercase form used for matching.

    Parameters:
        doi (str | None): a DOI, possibly prefixed with a resolver URL.

    Returns:
        str: the bare DOI, or an empty string when there is none.
    """
    if not doi:
        return ""
    clean = str(doi).strip().lower()
    clean = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)\s*", "", clean)
    clean = clean.rstrip(".,;:)]}")
    return clean if clean.startswith("10.") else ""


def read_index(index_path):
    """Load the index CSV.

    Parameters:
        index_path (str): path to ``lit_index.csv``.

    Returns:
        list[dict]: the rows, empty when the file does not exist.
    """
    if not os.path.exists(index_path):
        return []
    _raise_field_size_limit()
    with open(index_path, newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_index(index_path, rows):
    """Write the index CSV atomically, sorted by first author then year.

    The file is written to a temporary neighbour and renamed, so an interrupted run never
    leaves a half-written index behind.

    Parameters:
        index_path (str): path to ``lit_index.csv``.
        rows (list[dict]): the rows to write.

    Returns:
        int: how many rows were written.
    """
    ordered = sorted(
        rows,
        key=lambda r: (
            str(r.get("first_author", "")).lower(),
            str(r.get("year", "")),
            str(r.get("key", "")),
        ),
    )
    temp_path = f"{index_path}.tmp"
    with open(temp_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in ordered:
            row = dict(row)
            row["tags_all"] = build_tags_all(row)
            writer.writerow({column: sanitize_cell(row.get(column, "")) for column in COLUMNS})
    os.replace(temp_path, index_path)
    return len(ordered)


def build_lookup(rows):
    """Build the DOI and title maps used to spot a paper already in the index.

    Parameters:
        rows (list[dict]): existing index rows.

    Returns:
        tuple[dict, dict]: a DOI-to-row map and a normalized-title-to-row map.
    """
    by_doi, by_title = {}, {}
    for row in rows:
        doi = normalize_doi(row.get("doi"))
        if doi:
            by_doi.setdefault(doi, row)
        title = naming.normalize_title(row.get("title"))
        if title:
            by_title.setdefault(title, row)
    return by_doi, by_title


def find_duplicate(record, by_doi, by_title):
    """Decide whether a record is already represented in the index.

    DOI is authoritative. A normalized title match is the fallback for papers with no DOI.

    Parameters:
        record (dict): a candidate record.
        by_doi (dict): the DOI map from :func:`build_lookup`.
        by_title (dict): the title map from :func:`build_lookup`.

    Returns:
        tuple[dict | None, str]: the matching row and the reason, or ``(None, "")``.
    """
    doi = normalize_doi(record.get("doi"))
    if doi and doi in by_doi:
        return by_doi[doi], f"doi {doi}"
    title = naming.normalize_title(record.get("title"))
    if title and title in by_title:
        return by_title[title], "title"
    return None, ""


def dedupe_candidates(candidates):
    """Collapse a list of candidate files to one per paper.

    Candidates are grouped by DOI, then by normalized title. Within a group the largest
    readable file wins, because a truncated copy is always the smaller one.

    Parameters:
        candidates (list[dict]): records carrying at least ``path``, ``doi`` and ``title``.

    Returns:
        tuple[list[dict], list[dict]]: the kept records, and the dropped ones each carrying a
        ``dropped_for`` key naming the winner's path.
    """
    groups = {}
    ungrouped = []
    for record in candidates:
        doi = normalize_doi(record.get("doi"))
        title = naming.normalize_title(record.get("title"))
        identity = f"doi:{doi}" if doi else (f"title:{title}" if title else "")
        if identity:
            groups.setdefault(identity, []).append(record)
        else:
            # No DOI and no title: keep it, it cannot be matched to anything.
            ungrouped.append(record)

    kept, dropped = list(ungrouped), []
    for members in groups.values():
        members.sort(key=lambda r: os.path.getsize(r["path"]) if os.path.exists(r["path"]) else 0, reverse=True)
        winner = members[0]
        kept.append(winner)
        for loser in members[1:]:
            loser = dict(loser)
            loser["dropped_for"] = winner["path"]
            dropped.append(loser)
    return kept, dropped


def assign_keys(records, taken=None):
    """Give every record a unique key and matching filename.

    Parameters:
        records (list[dict]): records carrying ``authors``, ``year`` and ``title``.
        taken (set[str] | None): keys already used elsewhere in the library.

    Returns:
        list[dict]: the same records, each with ``key`` and ``filename`` set.
    """
    used = set(taken or ())
    for record in records:
        key = naming.build_key(record.get("authors"), record.get("year"), record.get("title"))
        key = naming.disambiguate(key, used)
        used.add(key)
        record["key"] = key
        record["filename"] = naming.build_filename(key)
    return records


LIBRARY_HOME_VAR = "VICAR_LIT_HOME"


def library_root(explicit=None):
    """Resolve the library folder.

    Order of preference: an explicit path, then the ``VICAR_LIT_HOME`` environment variable,
    then the current directory. Keeping the location in the environment is what lets the same
    code serve a lab whose library sits somewhere else.

    Parameters:
        explicit (str | None): a path supplied directly, which always wins.

    Returns:
        str: the library folder path.
    """
    if explicit:
        return explicit
    return os.environ.get(LIBRARY_HOME_VAR, "").strip() or os.getcwd()


def library_paths(root):
    """Resolve the standard paths inside a library root.

    Parameters:
        root (str): the library folder, the one holding ``pdfs/``.

    Returns:
        dict: the keys ``root``, ``pdfs``, ``ingest``, ``processed``, ``logs`` and ``index``.
    """
    return {
        "root": root,
        "pdfs": os.path.join(root, PDF_DIRNAME),
        "ingest": os.path.join(root, INGEST_DIRNAME),
        "processed": os.path.join(root, INGEST_DIRNAME, PROCESSED_DIRNAME),
        "logs": os.path.join(root, LOG_DIRNAME),
        "index": os.path.join(root, INDEX_FILENAME),
    }


def ensure_layout(root):
    """Create any missing library folders.

    Parameters:
        root (str): the library folder.

    Returns:
        dict: the paths from :func:`library_paths`.
    """
    paths = library_paths(root)
    for key in ("pdfs", "ingest", "processed", "logs"):
        os.makedirs(paths[key], exist_ok=True)
    return paths


def audit(root):
    """Check that the index and the PDF folder agree.

    Parameters:
        root (str): the library folder.

    Returns:
        dict: the keys ``rows``, ``files``, ``missing_files`` (indexed but absent from disk)
        and ``unindexed_files`` (on disk but absent from the index).
    """
    paths = library_paths(root)
    rows = read_index(paths["index"])
    on_disk = set()
    if os.path.isdir(paths["pdfs"]):
        on_disk = {f for f in os.listdir(paths["pdfs"]) if f.lower().endswith(".pdf")}
    indexed = {row.get("filename", "") for row in rows}

    def repeated(values):
        """List the values that appear more than once."""
        seen, twice = set(), set()
        for value in values:
            if value in seen:
                twice.add(value)
            seen.add(value)
        return sorted(twice)

    return {
        "rows": len(rows),
        "files": len(on_disk),
        "missing_files": sorted(indexed - on_disk - {""}),
        "unindexed_files": sorted(on_disk - indexed),
        "duplicate_keys": repeated([r.get("key", "") for r in rows if r.get("key")]),
        "duplicate_filenames": repeated([r.get("filename", "") for r in rows if r.get("filename")]),
    }


def today_iso():
    """Return today's date as an ISO string.

    Returns:
        str: ``YYYY-MM-DD``.
    """
    return datetime.date.today().isoformat()


def main(argv=None):
    """Run an audit from the command line and print the result.

    Parameters:
        argv (list[str] | None): command line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 when the index and the folder agree, 1 otherwise.
    """
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: python -m litkit.index <library-root>", file=sys.stderr)
        return 2
    report = audit(args[0])
    print(f"rows: {report['rows']}  pdfs: {report['files']}")
    if report["missing_files"]:
        print(f"indexed but missing from pdfs/: {len(report['missing_files'])}")
        for name in report["missing_files"][:20]:
            print(f"  {name}")
    if report["unindexed_files"]:
        print(f"in pdfs/ but not indexed: {len(report['unindexed_files'])}")
        for name in report["unindexed_files"][:20]:
            print(f"  {name}")
    for label in ("duplicate_keys", "duplicate_filenames"):
        if report[label]:
            print(f"{label.replace('_', ' ')}: {len(report[label])}")
            for name in report[label][:20]:
                print(f"  {name}")
    problems = (
        report["missing_files"] or report["unindexed_files"]
        or report["duplicate_keys"] or report["duplicate_filenames"]
    )
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
