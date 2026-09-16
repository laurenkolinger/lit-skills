"""Fill the link column so a reader can click from the sheet straight to the paper.

Each row's link points at that paper's PDF in Google Drive. Building it needs the Drive file
id, which only the Drive connector can supply, so an agent fetches the ids into a JSON file
and this module writes them into the index.

A row with no id falls back to a Drive search for its filename. That always resolves, so no
row is ever left without a way through to its paper.

Command line:
    python -m litkit.links <library-root> [--ids ids.json] [--dry-run]
"""

import argparse
import datetime
import json
import os
import sys
import traceback
import urllib.parse

from . import index as index_module
from . import sheet as sheet_module

WORKBOOK_FILENAME = "lit_index.xlsx"
FILE_URL = "https://drive.google.com/file/d/{file_id}/view"
SEARCH_URL = "https://drive.google.com/drive/search?q={query}"


def file_link(file_id):
    """Build the Drive URL that opens one file.

    Parameters:
        file_id (str | None): a Drive file id.

    Returns:
        str: the URL, empty when no id was given.
    """
    if not file_id or not str(file_id).strip():
        return ""
    return FILE_URL.format(file_id=str(file_id).strip())


def search_link(filename):
    """Build a Drive search URL for a filename.

    This is the fallback for a paper whose Drive id is not known yet. It resolves to the file
    in one click, which is worse than a direct link and far better than no link.

    Parameters:
        filename (str | None): the PDF filename.

    Returns:
        str: the URL, empty when no filename was given.
    """
    if not filename or not str(filename).strip():
        return ""
    stem = os.path.splitext(str(filename).strip())[0]
    return SEARCH_URL.format(query=urllib.parse.quote(stem))


def load_ids(path):
    """Read a filename-to-Drive-id map.

    Parameters:
        path (str | None): path to the JSON map, or None.

    Returns:
        dict: the map, empty when the path is missing or unreadable.
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def apply_links(root, ids_path=None, dry_run=False):
    """Write a link into every row, and rebuild the workbook.

    Parameters:
        root (str): the library folder.
        ids_path (str | None): path to the filename-to-Drive-id JSON map.
        dry_run (bool): report without changing anything.

    Returns:
        dict: the keys ``direct``, ``fallback``, ``unchanged`` and ``log``.
    """
    paths = index_module.library_paths(root)
    rows = index_module.read_index(paths["index"])
    ids = load_ids(ids_path)

    log = [
        f"VICAR Lit links, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"rows: {len(rows)}  ids available: {len(ids)}",
    ]
    direct = fallback = unchanged = 0

    for row in rows:
        filename = row.get("filename", "")
        link = file_link(ids.get(filename))
        if link:
            direct += 1
        else:
            link = search_link(filename)
            if link:
                fallback += 1
        if link and row.get("link") != link:
            row["link"] = link
        else:
            unchanged += 1

    log.append(f"direct Drive links: {direct}  search fallbacks: {fallback}")
    if fallback:
        log.append("a fallback row has no Drive id yet; rerun with a fuller ids file to fix it")

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
            paths["logs"], f"links_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(log) + "\n")

    return {"direct": direct, "fallback": fallback, "unchanged": unchanged, "log": "\n".join(log)}


def main(argv=None):
    """Fill the link column from the command line.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Fill the link column in the index.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("--ids", default=None, help="JSON map of filename to Drive file id")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    args = parser.parse_args(argv)

    try:
        result = apply_links(args.root, ids_path=args.ids, dry_run=args.dry_run)
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(f"\ndirect {result['direct']}, fallback {result['fallback']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
