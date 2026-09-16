"""Re-derive metadata for index rows whose title or authors look wrong.

A row goes bad when the PDF's embedded metadata held a production filename or a producer
name instead of the real title and authors. This pass finds those rows, reads the file again
with the current extractor, asks OpenAlex once more, and rewrites the row and the filename
when the answer improves. It never touches a row it cannot improve.

Command line:
    python -m litkit.repair <library-root> [--dry-run] [--no-network]
"""

import argparse
import datetime
import os
import sys
import traceback

from . import enrich as enrich_module
from . import extract as extract_module
from . import index as index_module
from . import naming
from . import sheet as sheet_module

MIN_GOOD_TITLE_WORDS = 4
WORKBOOK_FILENAME = "lit_index.xlsx"


def is_suspect(row):
    """Decide whether a row's metadata looks like extraction went wrong.

    Parameters:
        row (dict): an index row.

    Returns:
        tuple[bool, str]: whether the row is suspect, and the reason.
    """
    title = (row.get("title") or "").strip()
    if not title:
        return True, "no title"
    if extract_module.looks_like_filename(title):
        return True, "title looks like a filename or job id"
    if len(title.split()) < MIN_GOOD_TITLE_WORDS:
        return True, f"title is only {len(title.split())} words"
    if title.isupper():
        return True, "title is all capitals"
    authors = (row.get("authors") or "").strip()
    if not authors:
        return True, "no authors"
    if extract_module.looks_like_producer(authors):
        return True, "authors look like software or an organization"
    return False, ""


def suspect_rows(rows):
    """List the rows worth re-deriving.

    Parameters:
        rows (list[dict]): index rows.

    Returns:
        list[tuple[dict, str]]: each suspect row with the reason it was flagged.
    """
    flagged = []
    for row in rows:
        suspect, reason = is_suspect(row)
        if suspect:
            flagged.append((row, reason))
    return flagged


def better_title(old, new):
    """Decide whether a freshly derived title improves on the one already stored.

    Parameters:
        old (str | None): the title currently in the row.
        new (str | None): the newly derived title.

    Returns:
        bool: True when the new title should replace the old one.
    """
    new = (new or "").strip()
    if not new or extract_module.looks_like_filename(new):
        return False
    if extract_module.looks_like_running_head(new):
        return False
    old = (old or "").strip()
    if not old:
        return True
    if extract_module.looks_like_filename(old):
        return True
    if new.isupper() and not old.isupper():
        return False
    return len(new.split()) > len(old.split())


def tidy_caps(title):
    """Convert an all-capitals title to title case, leaving anything else alone.

    Parameters:
        title (str | None): the title.

    Returns:
        str: the tidied title.
    """
    text = (title or "").strip()
    if not text or not text.isupper():
        return text
    small = {"of", "the", "and", "in", "on", "for", "to", "a", "an", "with", "from", "at", "by"}
    words = text.split()
    out = []
    for position, word in enumerate(words):
        lowered = word.lower()
        out.append(lowered if position and lowered in small else lowered.capitalize())
    return " ".join(out)


def repair(root, dry_run=False, use_network=True, today=None):
    """Re-derive metadata for every suspect row and rewrite what improves.

    Parameters:
        root (str): the library folder.
        dry_run (bool): when True, report without changing anything.
        use_network (bool): when False, skip OpenAlex and rely on the file alone.
        today (datetime.date | None): the reference date, defaulting to today.

    Returns:
        dict: the keys ``flagged``, ``repaired``, ``renamed``, ``unchanged`` and ``log``.
    """
    reference = today or datetime.date.today()
    paths = index_module.library_paths(root)
    rows = index_module.read_index(paths["index"])
    flagged = suspect_rows(rows)

    log = [
        f"VICAR Lit repair, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"rows: {len(rows)}  flagged: {len(flagged)}",
    ]

    taken_keys = {row.get("key", "") for row in rows}
    repaired = renamed = 0

    for row, reason in flagged:
        pdf_path = os.path.join(paths["pdfs"], row.get("filename", ""))
        if not os.path.exists(pdf_path):
            log.append(f"SKIP {row.get('key')}: its PDF is missing ({reason})")
            continue
        try:
            fresh = extract_module.extract(pdf_path)
        except Exception as error:
            log.append(f"SKIP {row.get('key')}: could not re-read it: {error}")
            continue

        if use_network:
            try:
                fresh = enrich_module.enrich(fresh, today=reference)
            except Exception as error:
                log.append(f"WARN {row.get('key')}: OpenAlex lookup failed: {error}")

        new_title = tidy_caps(fresh.get("title"))
        changed = []

        if better_title(row.get("title"), new_title):
            log.append(f"  title: {row.get('title')!r} -> {new_title!r}")
            row["title"] = new_title
            changed.append("title")
        elif row.get("title", "").isupper():
            tidied = tidy_caps(row["title"])
            if tidied != row["title"]:
                row["title"] = tidied
                changed.append("title case")

        # Authors are only replaced on the authority of an OpenAlex match. The text-derived
        # author guess is good enough to name a new file, and not good enough to overwrite a
        # row: on 2026-09-15 it offered a title in capitals as the author list.
        if fresh.get("matched_openalex"):
            for field in ("authors", "first_author"):
                value = fresh.get(field)
                current = (row.get(field) or "").strip()
                if value and (not current or extract_module.looks_like_producer(current)):
                    row[field] = value
                    changed.append(field)

        for field in ("journal", "doi", "url", "year"):
            value = fresh.get(field)
            if value and not (row.get(field) or "").strip():
                row[field] = value
                changed.append(field)

        if fresh.get("citations") not in (None, "") and not row.get("citations"):
            row["citations"] = fresh["citations"]
            row["citations_retrieved"] = fresh.get("citations_retrieved", "")
            row["citations_per_year"] = fresh.get("citations_per_year", "")
            row["impact"] = fresh.get("impact", row.get("impact", ""))
            changed.append("citations")

        if not changed:
            log.append(f"UNCHANGED {row.get('key')} ({reason}): nothing better was found")
            continue

        repaired += 1
        log.append(f"REPAIRED {row.get('key')} ({reason}): {', '.join(changed)}")

        # The key encodes the metadata, so a corrected title means a corrected filename.
        desired = naming.build_key(row.get("authors"), row.get("year"), row.get("title"))
        if desired != row.get("key"):
            taken_keys.discard(row.get("key"))
            desired = naming.disambiguate(desired, taken_keys)
            taken_keys.add(desired)
            new_name = naming.build_filename(desired)
            if not dry_run:
                try:
                    os.rename(pdf_path, os.path.join(paths["pdfs"], new_name))
                except OSError as error:
                    log.append(f"WARN {row.get('key')}: could not rename its PDF: {error}")
                    continue
            log.append(f"  renamed: {row.get('filename')} -> {new_name}")
            row["key"], row["filename"] = desired, new_name
            renamed += 1

    if dry_run:
        log.append("DRY RUN: nothing was written")
    else:
        index_module.write_index(paths["index"], rows)
        try:
            sheet_module.build_workbook(
                index_module.read_index(paths["index"]), os.path.join(root, WORKBOOK_FILENAME)
            )
            log.append(f"workbook rebuilt: {WORKBOOK_FILENAME}")
        except Exception as error:
            log.append(f"WARN workbook rebuild failed: {error}")
        os.makedirs(paths["logs"], exist_ok=True)
        log_path = os.path.join(
            paths["logs"], f"repair_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(log) + "\n")

    return {
        "flagged": len(flagged),
        "repaired": repaired,
        "renamed": renamed,
        "unchanged": len(flagged) - repaired,
        "log": "\n".join(log),
    }


def main(argv=None):
    """Run a repair pass from the command line.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Re-derive metadata for suspect index rows.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    parser.add_argument("--no-network", action="store_true", help="skip OpenAlex lookups")
    args = parser.parse_args(argv)

    try:
        result = repair(args.root, dry_run=args.dry_run, use_network=not args.no_network)
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(
        f"\nflagged {result['flagged']}, repaired {result['repaired']}, "
        f"renamed {result['renamed']}, unchanged {result['unchanged']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
