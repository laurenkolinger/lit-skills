"""Apply hand-checked metadata corrections to the index.

Automated extraction gets a small number of papers wrong, usually because the PDF carried a
production filename in place of a title. Reading those papers and writing the answer down is
faster and more reliable than tuning heuristics at them. This module applies those hand-written
corrections from a CSV, so the fix is reviewable, repeatable, and never a silent hand edit.

Command line:
    python -m litkit.corrections <library-root> <corrections.csv> [--dry-run]
"""

import argparse
import csv
import datetime
import os
import sys
import traceback

from . import index as index_module
from . import naming
from . import sheet as sheet_module

WORKBOOK_FILENAME = "lit_index.xlsx"
CORRECTABLE_FIELDS = ("title", "authors", "first_author", "year", "journal", "doi")
# A blank cell means "leave this field alone". This token means "the stored value is wrong and
# I have no replacement", which is the honest answer for a DOI that points at another paper.
CLEAR_TOKEN = "<clear>"


def load_corrections(path):
    """Read a corrections CSV.

    The file needs an ``old_key`` column naming the row to fix, any of the correctable
    fields, and an optional ``new_key``.

    Parameters:
        path (str): path to the corrections CSV.

    Returns:
        list[dict]: the correction records.

    Raises:
        FileNotFoundError: when the file is missing.
        ValueError: when the file has no ``old_key`` column.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"corrections file not found: {path}")
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "old_key" not in reader.fieldnames:
            raise ValueError(f"corrections file needs an old_key column: {path}")
        return [row for row in reader if (row.get("old_key") or "").strip()]


def apply_corrections(root, corrections_path, dry_run=False):
    """Apply every correction to the index, renaming PDFs whose key changes.

    Parameters:
        root (str): the library folder.
        corrections_path (str): path to the corrections CSV.
        dry_run (bool): when True, report without changing anything.

    Returns:
        dict: the keys ``applied``, ``renamed``, ``missing`` and ``log``.
    """
    paths = index_module.library_paths(root)
    rows = index_module.read_index(paths["index"])
    by_key = {row.get("key", ""): row for row in rows}
    taken = set(by_key)
    corrections = load_corrections(corrections_path)

    log = [
        f"VICAR Lit corrections, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"corrections offered: {len(corrections)}",
    ]
    applied = renamed = 0
    missing = []

    for correction in corrections:
        old_key = correction["old_key"].strip()
        row = by_key.get(old_key)
        if row is None:
            missing.append(old_key)
            log.append(f"MISSING {old_key}: no such row in the index")
            continue

        changed = []
        for field in CORRECTABLE_FIELDS:
            value = (correction.get(field) or "").strip()
            if value == CLEAR_TOKEN:
                if row.get(field):
                    row[field] = ""
                    changed.append(f"{field} cleared")
                continue
            if value and value != row.get(field):
                row[field] = value
                changed.append(field)
        if changed:
            applied += 1
            log.append(f"CORRECTED {old_key}: {', '.join(changed)}")

        new_key = (correction.get("new_key") or "").strip()
        if not new_key or new_key == old_key:
            continue

        # The key becomes a filename, so it never leaves the pdfs folder.
        safe_key = naming.sanitize_key(new_key)
        if safe_key != new_key:
            log.append(f"WARN {old_key}: new_key was sanitized to {safe_key!r}")
        new_key = safe_key
        if not new_key or new_key == old_key:
            continue
        taken.discard(old_key)
        new_key = naming.disambiguate(new_key, taken)
        taken.add(new_key)
        new_name = naming.build_filename(new_key)
        old_path = os.path.join(paths["pdfs"], row.get("filename", ""))
        if not dry_run:
            if not os.path.exists(old_path):
                log.append(f"WARN {old_key}: its PDF is missing, the row was renamed anyway")
            else:
                try:
                    os.rename(old_path, os.path.join(paths["pdfs"], new_name))
                except OSError as error:
                    log.append(f"WARN {old_key}: could not rename its PDF: {error}")
                    continue
        log.append(f"  renamed: {row.get('filename')} -> {new_name}")
        by_key.pop(old_key, None)
        row["key"], row["filename"] = new_key, new_name
        by_key[new_key] = row
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
            paths["logs"], f"corrections_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(log) + "\n")

    return {"applied": applied, "renamed": renamed, "missing": missing, "log": "\n".join(log)}


def find_duplicates(root):
    """Report rows that now share a DOI or a normalized title.

    Correcting a garbled title often reveals a duplicate that deduplication could not see at
    ingest time, because the garbled row matched nothing.

    Parameters:
        root (str): the library folder.

    Returns:
        list[list[dict]]: each group of two or more rows that describe the same paper.
    """
    rows = index_module.read_index(index_module.library_paths(root)["index"])
    groups = {}
    for row in rows:
        doi = index_module.normalize_doi(row.get("doi"))
        title = naming.normalize_title(row.get("title"))
        identity = f"doi:{doi}" if doi else (f"title:{title}" if title else "")
        if identity:
            groups.setdefault(identity, []).append(row)
    return [members for members in groups.values() if len(members) > 1]


def main(argv=None):
    """Apply corrections from the command line and report duplicates afterwards.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Apply hand-checked metadata corrections.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("corrections", help="path to the corrections CSV")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    args = parser.parse_args(argv)

    try:
        result = apply_corrections(args.root, args.corrections, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(f"\napplied {result['applied']}, renamed {result['renamed']}, missing {len(result['missing'])}")

    duplicates = find_duplicates(args.root)
    if duplicates:
        print(f"\n{len(duplicates)} duplicate groups are now visible:")
        for group in duplicates:
            print("  " + " | ".join(f"{r['key']} ({r['source']})" for r in group))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
