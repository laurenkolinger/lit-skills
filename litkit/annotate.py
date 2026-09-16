"""Merge summaries, key findings and tags into the index.

The ingest run fills everything a machine can read off a paper. The judgement columns, the
summary and the four tag facets, are written separately, either by a reader or by an agent
following the skill. This module merges those annotations in by key and validates the tags
against the controlled vocabulary, so the sheet stays filterable.

Command line:
    python -m litkit.annotate <library-root> <annotations.csv> [...] [--dry-run] [--strict]
"""

import argparse
import csv
import datetime
import os
import sys
import traceback

from . import index as index_module
from . import sheet as sheet_module

WORKBOOK_FILENAME = "lit_index.xlsx"
ANNOTATION_FIELDS = (
    "summary",
    "key_findings",
    "topic_tags",
    "method_tags",
    "region_tags",
    "taxa_tags",
    "vicar_relevance",
)
TAG_FIELDS = ("topic_tags", "method_tags", "region_tags", "taxa_tags", "vicar_relevance")

VOCABULARY = {
    "topic_tags": {
        "coral reef", "mesophotic", "fish spawning aggregation", "reef fish", "coral disease",
        "SCTLD", "bleaching", "hurricane", "resilience", "regime shift", "connectivity",
        "ecosystem function", "restoration", "monitoring", "mangrove", "pelagic",
        "water quality", "ciguatera", "herbivory", "fisheries management",
        "marine protected area", "climate change", "STEM education", "robotics ethics",
    },
    "method_tags": {
        "AUV", "ROV", "photogrammetry", "3D", "machine learning", "deep learning",
        "computer vision", "image annotation", "semantic segmentation", "remote sensing",
        "acoustic telemetry", "passive acoustics", "SLAM", "time series", "modeling",
        "field experiment", "genetics", "diver survey", "benthic survey",
    },
    "region_tags": {
        "USVI", "Caribbean", "Belize", "Cayman Islands", "Florida", "Bahamas", "Pacific",
        "Great Barrier Reef", "Red Sea", "global",
    },
    "taxa_tags": {
        "scleractinia", "Orbicella", "Acropora", "Porites", "octocoral", "sponge", "algae",
        "reef fish", "Nassau grouper", "red hind", "snapper", "urchin", "dinoflagellate",
        "Symbiodiniaceae",
    },
    "vicar_relevance": {
        "automation infrastructure", "reef research", "STEM workforce", "VICARIUS platform",
        "background",
    },
}


def split_tags(value):
    """Split a comma-separated tag cell into a clean list.

    Parameters:
        value (str | None): the cell contents.

    Returns:
        list[str]: the tags, stripped, with blanks removed.
    """
    if not value:
        return []
    return [tag.strip() for tag in str(value).split(",") if tag.strip()]


def check_tags(row):
    """Report tags that fall outside the controlled vocabulary.

    Parameters:
        row (dict): an annotation record.

    Returns:
        list[str]: one message per offending tag, empty when every tag is known.
    """
    problems = []
    for field in TAG_FIELDS:
        for tag in split_tags(row.get(field)):
            if tag not in VOCABULARY[field]:
                problems.append(f"{field}: {tag!r} is not in the vocabulary")
    return problems


def load_annotations(paths):
    """Read one or more annotation CSVs, keyed by row key.

    Later files win when two supply the same key, which makes a re-run of one batch safe.

    Parameters:
        paths (list[str]): annotation CSV paths.

    Returns:
        tuple[dict, list[str]]: the annotations by key, and the paths that were missing.
    """
    annotations, missing = {}, []
    for path in paths:
        if not os.path.exists(path):
            missing.append(path)
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                key = (record.get("key") or "").strip()
                if key:
                    annotations[key] = record
    return annotations, missing


def apply_annotations(root, annotation_paths, dry_run=False, strict=False):
    """Merge annotations into the index and rebuild the workbook.

    Parameters:
        root (str): the library folder.
        annotation_paths (list[str]): annotation CSV paths.
        dry_run (bool): when True, report without changing anything.
        strict (bool): when True, refuse to write if any tag is outside the vocabulary.

    Returns:
        dict: the keys ``annotated``, ``unmatched``, ``still_blank``, ``tag_problems``
        and ``log``.

    Raises:
        ValueError: when ``strict`` is set and any tag is outside the vocabulary.
    """
    paths = index_module.library_paths(root)
    rows = index_module.read_index(paths["index"])
    annotations, missing_files = load_annotations(annotation_paths)

    log = [
        f"VICAR Lit annotate, {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"library: {root}",
        f"rows: {len(rows)}  annotations offered: {len(annotations)}",
    ]
    for path in missing_files:
        log.append(f"WARN annotation file not found: {path}")

    by_key = {row.get("key", ""): row for row in rows}
    annotated, tag_problems = 0, []

    for key, record in annotations.items():
        row = by_key.get(key)
        if row is None:
            log.append(f"UNMATCHED {key}: no such row in the index")
            continue
        problems = check_tags(record)
        for problem in problems:
            tag_problems.append(f"{key}: {problem}")
        changed = False
        for field in ANNOTATION_FIELDS:
            value = (record.get(field) or "").strip()
            if value:
                row[field] = value
                changed = True
        if changed:
            annotated += 1

    unmatched = [k for k in annotations if k not in by_key]
    still_blank = [
        row.get("key", "")
        for row in rows
        if not (row.get("topic_tags") or "").strip()
        or row.get("topic_tags") == "needs review"
    ]
    log.append(f"annotated: {annotated}  unmatched: {len(unmatched)}  still blank: {len(still_blank)}")
    if tag_problems:
        log.append(f"tags outside the vocabulary: {len(tag_problems)}")
        for problem in tag_problems[:40]:
            log.append(f"  {problem}")

    if strict and tag_problems:
        raise ValueError(f"{len(tag_problems)} tags fall outside the vocabulary, nothing was written")

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
            paths["logs"], f"annotate_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(log) + "\n")

    return {
        "annotated": annotated,
        "unmatched": unmatched,
        "still_blank": still_blank,
        "tag_problems": tag_problems,
        "log": "\n".join(log),
    }


def main(argv=None):
    """Merge annotations from the command line.

    Parameters:
        argv (list[str] | None): arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: 0 on success, 1 when the run raised.
    """
    parser = argparse.ArgumentParser(description="Merge summaries and tags into the index.")
    parser.add_argument("root", help="the library folder, the one holding pdfs/")
    parser.add_argument("annotations", nargs="+", help="annotation CSV paths")
    parser.add_argument("--dry-run", action="store_true", help="report without changing anything")
    parser.add_argument("--strict", action="store_true", help="refuse to write on any unknown tag")
    args = parser.parse_args(argv)

    try:
        result = apply_annotations(
            args.root, args.annotations, dry_run=args.dry_run, strict=args.strict
        )
    except Exception:
        traceback.print_exc()
        return 1

    print(result["log"])
    print(
        f"\nannotated {result['annotated']}, unmatched {len(result['unmatched'])}, "
        f"still blank {len(result['still_blank'])}, tag problems {len(result['tag_problems'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
