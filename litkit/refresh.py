"""Refresh citation counts on rows whose DOI is known.

Citation counts go stale, and a corrected DOI leaves the old paper's count behind. This pass
looks each row up by DOI and rewrites the count, the retrieval date, the per-year rate and the
impact label. It only ever trusts a DOI: it never searches by title, because a title search
that matches the wrong paper is how wrong counts got into the index in the first place.

Command line:
    python -m litkit.refresh <library-root> [--all] [--keys KEY ...] [--dry-run]
"""

import argparse
import datetime
import os
import sys
import traceback

from . import enrich as enrich_module
from . import index as index_module
from . import sheet as sheet_module

WORKBOOK_FILENAME = "lit_index.xlsx"
PROGRESS_EVERY = 25


def rows_needing_refresh(rows, refresh_all=False, keys=None):
    """Choose the rows to look up.

    Parameters:
        rows (list[dict]): index rows.
        refresh_all (bool): when True, every row with a DOI is refreshed.
        keys (set[str] | None): when given, only these row keys are considered.

    Returns:
        list[dict]: the rows to refresh.
    """
    chosen = []
    for row in rows:
        if keys is not None and row.get("key") not in keys:
            continue
        if not index_module.normalize_doi(row.get("doi")):
            continue
        if refresh_all or keys is not None or not str(row.get("citations", "")).strip():
            chosen.append(row)
    return chosen


def refresh(root, refresh_all=False, keys=None, dry_run=False, today=None, progress=False):
    """Look up citation counts by DOI and write them back.

    Parameters:
        root (str): the library folder.
        refresh_all (bool): refresh every row with a DOI, not just the empty ones.
        keys (list[str] | None): restrict the pass to these row keys.
        dry_run (bool): report without changing anything.
        today (datetime.date | None): the reference date, defaulting to today.
        progress (bool): print progress to stderr.

    Returns:
        dict: the keys ``considered``, ``updated``, ``unresolved`` and ``log``.
    """
    reference = today or datetime.date.today()
    paths = index_module.library_paths(root)
    rows = index_module.read_index(paths["index"])
    wanted = set(keys) if keys else None
    targets = rows_needing_refresh(rows, refresh_all=refresh_all, keys=wanted)

    log = [
        f"VICAR Lit refresh, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"rows: {len(rows)}  to refresh: {len(targets)}",
    ]
    updated, unresolved = 0, []

    for position, row in enumerate(targets, start=1):
        if progress and position % PROGRESS_EVERY == 0:
            print(f"  refreshed {position}/{len(targets)}", file=sys.stderr, flush=True)
        doi = index_module.normalize_doi(row.get("doi"))
        try:
            work = enrich_module.fetch_by_doi(doi)
        except Exception as error:
            log.append(f"WARN {row.get('key')}: lookup failed: {error}")
            unresolved.append(row.get("key"))
            continue
        if not work:
            log.append(f"UNRESOLVED {row.get('key')}: OpenAlex has no record for {doi}")
            unresolved.append(row.get("key"))
            continue

        parsed = enrich_module.parse_work(work)
        count = parsed["citations"]
        if count in (None, ""):
            log.append(f"UNRESOLVED {row.get('key')}: record has no citation count")
            unresolved.append(row.get("key"))
            continue

        before = row.get("citations", "")
        row["citations"] = count
        row["citations_retrieved"] = reference.isoformat()
        rate = enrich_module.citations_per_year(count, row.get("year"), today=reference)
        row["citations_per_year"] = "" if rate is None else rate
        row["impact"] = enrich_module.impact_label(count, row.get("year"), today=reference)
        if not (row.get("journal") or "").strip() and parsed.get("journal"):
            row["journal"] = parsed["journal"]
        if not (row.get("url") or "").strip() and parsed.get("url"):
            row["url"] = parsed["url"]
        updated += 1
        log.append(f"REFRESHED {row.get('key')}: {before or 'blank'} -> {count} ({row['impact']})")

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
            paths["logs"], f"refresh_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(log) + "\n")

    return {
        "considered": len(targets),
        "updated": updated,
        "unresolved": unresolved,
        "log": "\n".join(log),
    }


def main(argv=None):
    """Refresh citation counts from the command line.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Refresh citation counts by DOI.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("--all", action="store_true", dest="refresh_all",
                        help="refresh every row with a DOI, not just rows with no count")
    parser.add_argument("--keys", nargs="+", default=None, help="refresh only these row keys")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    parser.add_argument("--progress", action="store_true", help="print progress to stderr")
    args = parser.parse_args(argv)

    try:
        result = refresh(
            args.root,
            refresh_all=args.refresh_all,
            keys=args.keys,
            dry_run=args.dry_run,
            progress=args.progress,
        )
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(
        f"\nconsidered {result['considered']}, updated {result['updated']}, "
        f"unresolved {len(result['unresolved'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
