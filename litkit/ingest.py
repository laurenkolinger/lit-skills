"""Ingest PDFs into the VICAR Lit library.

Two entry points share one pipeline:

``run`` handles the everyday case. It reads every PDF in ``ingest/``, files the new ones into
``pdfs/`` and moves the originals to ``ingest/_processed/``.

``import_from`` handles the one-time bulk build. It reads PDFs from folders anywhere on the
machine, copies only the deduplicated winners into ``pdfs/``, and never touches the originals.

Command line:
    python -m litkit.ingest <library-root> [--dry-run] [--no-network] [--source LABEL]
    python -m litkit.ingest <library-root> --import FOLDER [FOLDER ...]
"""

import argparse
import datetime
import os
import shutil
import sys
import traceback

from . import enrich as enrich_module
from . import extract as extract_module
from . import index as index_module
from . import naming
from . import sheet as sheet_module

WORKBOOK_FILENAME = "lit_index.xlsx"
PLACEHOLDER_TAG = "needs review"
PROGRESS_EVERY = 25
# Passing this as the source label records each paper's own origin folder instead of one
# label for the whole batch, which is what a mixed bulk import needs.
AUTO_SOURCE = "auto"


def source_label(path, requested):
    """Decide the source label recorded on a row.

    Parameters:
        path (str): the PDF's original path.
        requested (str): the label asked for, or ``auto``.

    Returns:
        str: the requested label, or the name of the folder the PDF came from.
    """
    if requested != AUTO_SOURCE:
        return requested
    return os.path.basename(os.path.dirname(os.path.abspath(path))) or "unknown"


def collect_pdfs(folder, recursive=False):
    """List the PDFs in a folder, skipping hidden files and the processed archive.

    Parameters:
        folder (str): the folder to scan.
        recursive (bool): when True, descend into subfolders.

    Returns:
        list[str]: absolute paths, sorted, empty when the folder does not exist.
    """
    if not os.path.isdir(folder):
        return []
    found = []
    if recursive:
        for current, subdirs, files in os.walk(folder):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d != index_module.PROCESSED_DIRNAME]
            for entry in files:
                if entry.startswith(".") or not entry.lower().endswith(".pdf"):
                    continue
                found.append(os.path.abspath(os.path.join(current, entry)))
    else:
        for entry in os.listdir(folder):
            if entry.startswith(".") or not entry.lower().endswith(".pdf"):
                continue
            full = os.path.join(folder, entry)
            if os.path.isfile(full):
                found.append(os.path.abspath(full))
    return sorted(found)


def blank_row(record, source, added_on):
    """Turn an enriched record into an index row.

    Tag and summary columns start empty so a reader or an agent fills them. The topic column
    carries a review marker, which makes an unreviewed row obvious in the sheet.

    Parameters:
        record (dict): an enriched record.
        source (str): a label naming where the file came from.
        added_on (str): the ISO date the row was created.

    Returns:
        dict: a row carrying every index column.
    """
    authors = record.get("authors", "") or ""
    # A scan yields almost no text, so its extracted metadata is weak. Say so in the row
    # rather than letting a thin title pass as a confident one.
    notes = record.get("notes", "")
    if record.get("text_quality") == extract_module.TEXT_QUALITY_SCANNED:
        notes = (notes + " " if notes else "") + "low text (likely scanned), check metadata"
    if record.get("metadata_mismatch"):
        notes = (notes + " " if notes else "") + "metadata mismatch, check this row"
    row = {column: "" for column in index_module.COLUMNS}
    row.update(
        {
            "key": record.get("key", ""),
            "filename": record.get("filename", ""),
            "authors": authors,
            "first_author": record.get("first_author", "") or authors.split(";")[0].strip(),
            "year": record.get("year", ""),
            "title": record.get("title", ""),
            "journal": record.get("journal", ""),
            "doi": index_module.normalize_doi(record.get("doi")),
            "url": record.get("url", ""),
            "citations": record.get("citations", ""),
            "citations_retrieved": record.get("citations_retrieved", ""),
            "citations_per_year": record.get("citations_per_year", ""),
            "impact": record.get("impact", enrich_module.IMPACT_UNRATED),
            "topic_tags": record.get("topic_tags", "") or PLACEHOLDER_TAG,
            "method_tags": record.get("method_tags", ""),
            "region_tags": record.get("region_tags", ""),
            "taxa_tags": record.get("taxa_tags", ""),
            "vicar_relevance": record.get("vicar_relevance", ""),
            "summary": record.get("summary", "") or (record.get("abstract", "") or "")[:600],
            "key_findings": record.get("key_findings", ""),
            "source": source,
            "date_added": added_on,
            "notes": notes,
        }
    )
    return row


def _read_candidates(paths_to_read, log, progress=False):
    """Extract metadata from a list of PDF paths, logging every rejection.

    A file that cannot be read is recorded and skipped so one bad PDF never stops a batch.

    Parameters:
        paths_to_read (list[str]): PDF paths.
        log (list[str]): the run log, appended in place.
        progress (bool): when True, print a running count to stderr.

    Returns:
        tuple[list[dict], list[tuple[str, str]]]: the readable records, and (path, reason) pairs
        for everything rejected.
    """
    candidates, rejected = [], []
    for position, path in enumerate(paths_to_read, start=1):
        if progress and position % PROGRESS_EVERY == 0:
            print(f"  read {position}/{len(paths_to_read)}", file=sys.stderr, flush=True)
        try:
            candidates.append(extract_module.extract(path))
        except extract_module.UnreadablePdf as error:
            rejected.append((path, str(error)))
            log.append(f"REJECT {os.path.basename(path)}: {error}")
        except Exception as error:  # a malformed PDF must not stop the batch
            rejected.append((path, f"unexpected error: {error}"))
            log.append(f"REJECT {os.path.basename(path)}: unexpected error: {error}")
    return candidates, rejected


def _enrich_all(candidates, use_network, log, reference, progress=False):
    """Add OpenAlex metadata and impact labels to every candidate.

    Parameters:
        candidates (list[dict]): extracted records.
        use_network (bool): when False, skip OpenAlex entirely.
        log (list[str]): the run log, appended in place.
        reference (datetime.date): the date used for impact maths.
        progress (bool): when True, print a running count to stderr.

    Returns:
        list[dict]: the enriched records.
    """
    if not use_network:
        for record in candidates:
            record["impact"] = enrich_module.IMPACT_UNRATED
        log.append("network lookups skipped, citations left empty")
        return candidates

    enriched, matched = [], 0
    for position, record in enumerate(candidates, start=1):
        if progress and position % PROGRESS_EVERY == 0:
            print(f"  enriched {position}/{len(candidates)}", file=sys.stderr, flush=True)
        try:
            result = enrich_module.enrich(record, today=reference)
            matched += 1 if result.get("matched_openalex") else 0
            enriched.append(result)
        except Exception as error:
            log.append(f"WARN OpenAlex lookup failed for {os.path.basename(record['path'])}: {error}")
            record.setdefault("impact", enrich_module.IMPACT_UNRATED)
            enriched.append(record)
    log.append(f"OpenAlex matched {matched} of {len(candidates)}")
    return enriched


def _drop_known(candidates, by_doi, by_title, log):
    """Split candidates into papers new to the library and papers already indexed.

    Parameters:
        candidates (list[dict]): enriched records.
        by_doi (dict): the DOI lookup, extended in place so a batch cannot self-duplicate.
        by_title (dict): the title lookup, extended in place.
        log (list[str]): the run log, appended in place.

    Returns:
        tuple[list[dict], list[dict]]: the new records and the duplicate records.
    """
    fresh, duplicates = [], []
    for record in candidates:
        match, reason = index_module.find_duplicate(record, by_doi, by_title)
        if match:
            duplicates.append(record)
            log.append(
                f"DUP already indexed {os.path.basename(record['path'])} "
                f"matches {match.get('key')} ({reason})"
            )
            continue
        fresh.append(record)
        doi = index_module.normalize_doi(record.get("doi"))
        if doi:
            by_doi[doi] = {"key": "pending", "doi": doi}
        title = naming.normalize_title(record.get("title"))
        if title:
            by_title[title] = {"key": "pending", "title": record.get("title")}
    return fresh, duplicates


def _finalize(root, paths, existing, added_rows, log):
    """Write the index, rebuild the workbook, and save the run log.

    Parameters:
        root (str): the library folder.
        paths (dict): the paths from :func:`litkit.index.library_paths`.
        existing (list[dict]): rows already in the index.
        added_rows (list[dict]): rows created by this run.
        log (list[str]): the run log, appended in place.

    Returns:
        tuple[list[dict], str]: every row now in the index, and the log file path.
    """
    all_rows = existing + added_rows
    index_module.write_index(paths["index"], all_rows)
    try:
        sheet_module.build_workbook(
            index_module.read_index(paths["index"]), os.path.join(root, WORKBOOK_FILENAME)
        )
        log.append(f"workbook rebuilt: {WORKBOOK_FILENAME}")
    except Exception as error:
        log.append(f"WARN workbook rebuild failed: {error}")

    log.append(f"index rows after: {len(all_rows)}")
    log_path = os.path.join(
        paths["logs"], f"ingest_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.log"
    )
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(log) + "\n")
    return all_rows, log_path


def _pipeline(root, paths_to_read, source, dry_run, use_network, reference, archive_to=None,
              progress=False):
    """Run the shared ingest pipeline over a list of PDF paths.

    Parameters:
        root (str): the library folder.
        paths_to_read (list[str]): PDF paths to consider.
        source (str): a label recording where this batch came from.
        dry_run (bool): when True, report without changing anything.
        use_network (bool): when False, skip OpenAlex.
        reference (datetime.date): the date used for impact maths and row stamps.
        archive_to (str | None): when set, move each accepted original into this folder.
        progress (bool): when True, print progress to stderr.

    Returns:
        dict: the run result.
    """
    paths = index_module.ensure_layout(root)
    existing = index_module.read_index(paths["index"])
    by_doi, by_title = index_module.build_lookup(existing)
    taken_keys = {row.get("key", "") for row in existing}

    log = [
        f"VICAR Lit ingest, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"source label: {source}",
        f"index rows before: {len(existing)}",
        f"PDFs offered: {len(paths_to_read)}",
    ]

    candidates, rejected = _read_candidates(paths_to_read, log, progress=progress)
    log.append(f"readable PDFs: {len(candidates)}")

    candidates = _enrich_all(candidates, use_network, log, reference, progress=progress)

    candidates, batch_dupes = index_module.dedupe_candidates(candidates)
    for dropped in batch_dupes:
        log.append(
            f"DUP in batch {os.path.basename(dropped['path'])} "
            f"-> kept {os.path.basename(dropped['dropped_for'])}"
        )
    log.append(f"after in-batch dedup: {len(candidates)}")

    fresh, duplicates = _drop_known(candidates, by_doi, by_title, log)
    index_module.assign_keys(fresh, taken=taken_keys)

    if dry_run:
        log.append(f"DRY RUN: would add {len(fresh)} rows, skip {len(duplicates)} duplicates")
        return {
            "added": 0,
            "would_add": len(fresh),
            "duplicates": len(duplicates) + len(batch_dupes),
            "rejected": len(rejected),
            "rows_total": len(existing),
            "log": "\n".join(log),
        }

    added_rows = []
    for record in fresh:
        destination = os.path.join(paths["pdfs"], record["filename"])
        try:
            shutil.copy2(record["path"], destination)
        except OSError as error:
            log.append(f"REJECT {os.path.basename(record['path'])}: could not file it: {error}")
            rejected.append((record["path"], str(error)))
            continue
        added_rows.append(blank_row(record, source_label(record["path"], source), reference.isoformat()))
        log.append(f"ADD {record['key']}  <- {record['path']}")
        if archive_to:
            try:
                shutil.move(record["path"], os.path.join(archive_to, os.path.basename(record["path"])))
            except OSError as error:
                log.append(f"WARN filed {record['key']} but could not archive the original: {error}")

    all_rows, log_path = _finalize(root, paths, existing, added_rows, log)
    return {
        "added": len(added_rows),
        "duplicates": len(duplicates) + len(batch_dupes),
        "rejected": len(rejected),
        "rows_total": len(all_rows),
        "log": "\n".join(log),
        "log_path": log_path,
    }


def run(root, source="ingest", dry_run=False, use_network=True, today=None, progress=False):
    """Ingest everything waiting in the library's ingest folder.

    Parameters:
        root (str): the library folder.
        source (str): a label recording where this batch came from.
        dry_run (bool): when True, report what would happen and change nothing.
        use_network (bool): when False, skip OpenAlex and leave citations empty.
        today (datetime.date | None): the reference date, defaulting to today.
        progress (bool): when True, print progress to stderr.

    Returns:
        dict: the keys ``added``, ``duplicates``, ``rejected``, ``rows_total`` and ``log``.
    """
    paths = index_module.ensure_layout(root)
    return _pipeline(
        root,
        collect_pdfs(paths["ingest"]),
        source,
        dry_run,
        use_network,
        today or datetime.date.today(),
        archive_to=paths["processed"],
        progress=progress,
    )


def import_from(root, folders, source="bulk import", dry_run=False, use_network=True, today=None,
                recursive=False, progress=False):
    """Import PDFs from folders elsewhere on the machine, leaving the originals untouched.

    This is the one-time bulk build. Only the deduplicated winners are copied into ``pdfs/``.

    Parameters:
        root (str): the library folder.
        folders (list[str]): folders to read.
        source (str): a label recording where this batch came from.
        dry_run (bool): when True, report what would happen and change nothing.
        use_network (bool): when False, skip OpenAlex.
        today (datetime.date | None): the reference date, defaulting to today.
        recursive (bool): when True, descend into subfolders.
        progress (bool): when True, print progress to stderr.

    Returns:
        dict: the run result.
    """
    offered = []
    for folder in folders:
        offered.extend(collect_pdfs(folder, recursive=recursive))
    return _pipeline(
        root,
        offered,
        source,
        dry_run,
        use_network,
        today or datetime.date.today(),
        archive_to=None,
        progress=progress,
    )


def main(argv=None):
    """Run an ingest or a bulk import from the command line.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Ingest PDFs into the VICAR Lit library.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    parser.add_argument("--no-network", action="store_true", help="skip OpenAlex lookups")
    parser.add_argument("--source", default=None, help="label recording where the batch came from")
    parser.add_argument("--import", dest="import_folders", nargs="+", default=None,
                        help="bulk import from these folders instead of reading ingest/")
    parser.add_argument("--recursive", action="store_true", help="descend into subfolders on import")
    parser.add_argument("--progress", action="store_true", help="print progress to stderr")
    args = parser.parse_args(argv)

    try:
        if args.import_folders:
            result = import_from(
                args.root,
                args.import_folders,
                source=args.source or "bulk import",
                dry_run=args.dry_run,
                use_network=not args.no_network,
                recursive=args.recursive,
                progress=args.progress,
            )
        else:
            result = run(
                args.root,
                source=args.source or "ingest",
                dry_run=args.dry_run,
                use_network=not args.no_network,
                progress=args.progress,
            )
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(
        f"\nadded {result['added']}, duplicates skipped {result['duplicates']}, "
        f"rejected {result['rejected']}, index now {result['rows_total']} rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
