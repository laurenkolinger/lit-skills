"""Build the formatted workbook that becomes the VICAR Lit Google Sheet.

Every long text column clips rather than wraps, and every data row keeps a fixed short
height, so the sheet stays compact. A reader clicks a cell to read the full summary.
"""

import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .index import COLUMNS, TAG_FACETS, build_tags_all

SHEET_TITLE = "Lit index"
TAGS_TITLE = "Tags"
README_TITLE = "Read me"
LINK_TEXT = "open"
DATA_ROW_HEIGHT = 20
HEADER_ROW_HEIGHT = 26
HEADER_FILL = "1F4E5A"
HEADER_FONT_COLOR = "FFFFFF"
DEFAULT_COLUMN_WIDTH = 16

# Column widths chosen so the columns a reader scans stay legible and the long text columns
# stay narrow enough that the sheet does not sprawl sideways.
COLUMN_WIDTHS = {
    "link": 7,
    "key": 34,
    "filename": 34,
    "authors": 30,
    "first_author": 16,
    "year": 7,
    "title": 46,
    "journal": 24,
    "doi": 24,
    "url": 24,
    "citations": 10,
    "citations_retrieved": 13,
    "citations_per_year": 12,
    "impact": 13,
    "tags_all": 40,
    "topic_tags": 26,
    "method_tags": 26,
    "region_tags": 16,
    "taxa_tags": 18,
    "vicar_relevance": 26,
    "summary": 60,
    "key_findings": 60,
    "source": 18,
    "date_added": 12,
    "notes": 24,
}

NUMERIC_COLUMNS = {"citations", "citations_per_year", "year"}

# A 290-author consortium report makes one cell 5,000 characters wide. The CSV keeps every
# name; the sheet shows a readable form, because the sheet is the reading surface.
MAX_AUTHORS_SHOWN = 8


def abbreviate_authors(value, limit=MAX_AUTHORS_SHOWN):
    """Shorten a long author list for display, leaving short ones untouched.

    Parameters:
        value (str | None): the full author list, semicolon separated.
        limit (int): how many authors to show before adding "et al.".

    Returns:
        str: the display form of the author list.
    """
    if not value or not str(value).strip():
        return ""
    names = [n.strip() for n in str(value).split(";") if n.strip()]
    if len(names) <= limit:
        return str(value)
    return "; ".join(names[:limit]) + f"; et al. ({len(names)} authors)"


def _coerce_number(column, value):
    """Convert a numeric column's text back to a number so the sheet can sort and filter it.

    Parameters:
        column (str): the column name.
        value: the cell value as stored in the CSV.

    Returns:
        int | float | str: a number when the column is numeric and the value parses,
        otherwise the value unchanged.
    """
    if column not in NUMERIC_COLUMNS or value in (None, ""):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return value


def build_workbook(rows, output_path, columns=None):
    """Write the index rows to a formatted xlsx file.

    Parameters:
        rows (list[dict]): index rows.
        output_path (str): where to write the workbook.
        columns (list[str] | None): column order, defaulting to the index columns.

    Returns:
        str: the path written.

    Raises:
        ValueError: when ``output_path`` is empty.
    """
    if not output_path:
        raise ValueError("build_workbook needs an output path")
    headers = list(columns or COLUMNS)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = SHEET_TITLE

    header_font = Font(bold=True, color=HEADER_FONT_COLOR, size=11)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL)
    header_alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)
    # wrap_text stays False everywhere: that is what keeps rows short and makes text clip.
    data_alignment = Alignment(horizontal="left", vertical="center", wrap_text=False)

    for position, name in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=position, value=name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        letter = get_column_letter(position)
        sheet.column_dimensions[letter].width = COLUMN_WIDTHS.get(name, DEFAULT_COLUMN_WIDTH)
    sheet.row_dimensions[1].height = HEADER_ROW_HEIGHT

    for offset, row in enumerate(rows):
        row = dict(row)
        row["tags_all"] = build_tags_all(row)
        row_number = offset + 2
        for position, name in enumerate(headers, start=1):
            value = row.get(name, "")
            if name == "authors":
                value = abbreviate_authors(value)
            if name == "link" and value:
                cell = sheet.cell(row=row_number, column=position, value=LINK_TEXT)
                cell.hyperlink = str(value)
                cell.font = Font(color="0563C1", underline="single", size=11)
            else:
                cell = sheet.cell(row=row_number, column=position, value=_coerce_number(name, value))
            cell.alignment = data_alignment
        sheet.row_dimensions[row_number].height = DATA_ROW_HEIGHT

    last_column = get_column_letter(len(headers))
    last_row = len(rows) + 1
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:{last_column}{last_row}"
    sheet.sheet_view.showGridLines = True

    _add_tags_tab(workbook, rows)
    _add_readme_tab(workbook, headers, len(rows))

    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    workbook.save(output_path)
    return output_path


def _column_letter(headers, name):
    """Find the spreadsheet column letter for a named index column.

    Parameters:
        headers (list[str]): the column order written to the sheet.
        name (str): the column name.

    Returns:
        str: the column letter, or ``A`` when the column is absent.
    """
    return get_column_letter(headers.index(name) + 1) if name in headers else "A"


def _add_tags_tab(workbook, rows):
    """Add a tab listing every tag in use, with how many papers carry it.

    Parameters:
        workbook (openpyxl.Workbook): the workbook being built.
        rows (list[dict]): the index rows.

    Returns:
        None
    """
    sheet = workbook.create_sheet(TAGS_TITLE)
    counts = {}
    for row in rows:
        for facet in TAG_FACETS:
            for tag in str(row.get(facet) or "").split(","):
                tag = tag.strip()
                if tag:
                    counts.setdefault(facet, {})
                    counts[facet][tag] = counts[facet].get(tag, 0) + 1

    sheet["A1"] = "Every tag in the library, and how many papers carry it"
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A2"] = ("A reference for what the library actually covers. You do not need to use "
                   "these by hand: ask the agent in plain language and it picks the tags.")
    sheet["A2"].font = Font(italic=True, color="666666")

    for position, label in enumerate(["facet", "tag", "papers"]):
        cell = sheet.cell(row=4, column=position + 1, value=label)
        cell.font = Font(bold=True, color=HEADER_FONT_COLOR)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)

    line = 5
    for facet in TAG_FACETS:
        for tag, count in sorted(counts.get(facet, {}).items(), key=lambda kv: (-kv[1], kv[0])):
            sheet.cell(row=line, column=1, value=facet.replace("_tags", "").replace("_", " "))
            sheet.cell(row=line, column=2, value=tag)
            sheet.cell(row=line, column=3, value=count)
            sheet.row_dimensions[line].height = DATA_ROW_HEIGHT
            line += 1

    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 34
    sheet.column_dimensions["C"].width = 10
    sheet.freeze_panes = "A5"
    if line > 5:
        sheet.auto_filter.ref = f"A4:C{line - 1}"


# Every column, in index order, with a definition a reader can act on.
COLUMN_DEFINITIONS = [
    ("link", "Opens the PDF."),
    ("key", "Permanent id for the paper. Also the PDF filename."),
    ("filename", "The PDF file on disk."),
    ("authors", "Full author list. Long ones are shortened here with et al."),
    ("first_author", "First author surname."),
    ("year", "Publication year."),
    ("title", "Paper title."),
    ("journal", "Journal, book or report series."),
    ("doi", "DOI as published. Blank when none was confirmed."),
    ("url", "Link to the published version."),
    ("citations", "Citation count from OpenAlex. Blank means unknown, not zero."),
    ("citations_retrieved", "Date that count was pulled."),
    ("citations_per_year", "Citations divided by years since publication."),
    ("impact", "Citation tier, defined below."),
    ("tags_all", "Every tag in one cell. This is what the search skill matches."),
    ("topic_tags", "Subject: coral reef, bleaching, disease, resilience."),
    ("method_tags", "Approach: AUV, photogrammetry, deep learning, telemetry."),
    ("region_tags", "Where the work happened."),
    ("taxa_tags", "Species or groups studied."),
    ("vicar_relevance", "Which part of VICAR the paper serves."),
    ("summary", "Two to four sentences on what the study did and found."),
    ("key_findings", "The results worth remembering."),
    ("source", "Where this copy came from."),
    ("date_added", "Date the row was created."),
    ("notes", "Anything needing a human eye: a scan, a thin summary, a mismatch."),
]

IMPACT_RULES = [
    ("high impact", "500 or more citations, or 40 or more per year"),
    ("well cited", "100 or more citations, or 15 or more per year"),
    ("standard", "10 or more citations"),
    ("emerging", "Fewer than 10 citations and published within the last 3 years"),
    ("low", "Everything else"),
    ("unrated", "No citation record was found, so no judgment is made"),
]


def _add_readme_tab(workbook, headers, row_count):
    """Add the documentation tab and put it first.

    The copy lives in :func:`readme_blocks` and the layout in :func:`_render_readme`, so the
    words can be rewritten without touching the spreadsheet mechanics.

    Parameters:
        workbook (openpyxl.Workbook): the workbook being built.
        headers (list[str]): the column order on the index sheet.
        row_count (int): how many papers the index holds.

    Returns:
        None
    """
    sheet = workbook.create_sheet(README_TITLE, 0)
    _render_readme(sheet, readme_blocks(row_count))


# The Read me tab is a document, not a table. Prose is merged across both columns so it can
# actually be read: on 2026-09-16 every prose line sat in the narrow left column and was cut off
# at about thirty characters.
README_LABEL_WIDTH = 30
README_TEXT_WIDTH = 96
README_CHARS_PER_LINE = 118
README_LINE_HEIGHT = 15


def _render_readme(sheet, blocks):
    """Lay out the Read me tab from a list of blocks.

    Parameters:
        sheet (openpyxl.worksheet.worksheet.Worksheet): the tab to write into.
        blocks (list[dict]): each with ``kind``, ``left`` and ``text``.

    Returns:
        int: the last row written.
    """
    ink = HEADER_FILL
    styles = {
        "title": Font(bold=True, size=18, color=ink),
        "subtitle": Font(italic=True, size=11, color="666666"),
        "heading": Font(bold=True, size=13, color=ink),
        "prose": Font(size=11),
        "label": Font(bold=True, size=11),
        "code": Font(name="Menlo", size=10, color="1F4E5A"),
        "bullet": Font(size=11, color="333333"),
        "warn": Font(bold=True, size=11, color="9C2500"),
    }
    wrap = Alignment(horizontal="left", vertical="top", wrap_text=True)

    def height_for(text, chars=README_CHARS_PER_LINE):
        lines = 1 + len(str(text)) // max(1, chars)
        return max(README_LINE_HEIGHT, README_LINE_HEIGHT * lines)

    line = 1
    for block in blocks:
        kind = block.get("kind", "prose")
        text = str(block.get("text") or "")
        left = str(block.get("left") or "")

        if kind == "gap":
            sheet.row_dimensions[line].height = 8
            line += 1
            continue

        if kind == "pair":
            label = sheet.cell(row=line, column=1, value=left)
            label.font = styles["label"]
            label.alignment = wrap
            value = sheet.cell(row=line, column=2, value=text)
            value.alignment = wrap
            sheet.row_dimensions[line].height = height_for(text, chars=94)
            line += 1
            continue

        # Everything else spans both columns so long lines are never clipped.
        sheet.merge_cells(start_row=line, start_column=1, end_row=line, end_column=2)
        cell = sheet.cell(row=line, column=1, value=("    " + text) if kind == "bullet" else text)
        cell.alignment = wrap
        if kind == "title":
            cell.font = styles["title"]
            sheet.row_dimensions[line].height = 30
        elif kind == "heading":
            cell.font = styles["heading"]
            sheet.row_dimensions[line].height = 24
        elif kind == "code":
            cell.font = styles["code"]
            cell.fill = PatternFill("solid", fgColor="F2F5F6")
            sheet.row_dimensions[line].height = height_for(text)
        elif kind == "warn":
            cell.font = styles["warn"]
            sheet.row_dimensions[line].height = height_for(text)
        else:
            cell.font = styles.get(kind, styles["prose"])
            sheet.row_dimensions[line].height = height_for(text)
        line += 1

    sheet.column_dimensions["A"].width = README_LABEL_WIDTH
    sheet.column_dimensions["B"].width = README_TEXT_WIDTH
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    return line - 1

def readme_blocks(row_count):
    """The Read me tab copy, as layout blocks.

    Kept apart from the rendering so the words can be rewritten without touching spreadsheet
    mechanics, and so tests can read the copy directly.

    Parameters:
        row_count (int): how many papers the index holds.

    Returns:
        list[dict]: blocks with ``kind``, ``left`` and ``text``.
    """
    def b(kind, text="", left=""):
        return {"kind": kind, "left": left, "text": text}

    blocks = [
        b("title", "VICAR lab literature library"),
        b("subtitle", f"{row_count} papers. Every paper has a PDF in this folder and one row in this index."),
        b("gap"),

        b("heading", "Ask Claude in plain language"),
        b("prose", "Open Claude Code in this folder and type a question. Claude reads the index "
                   "and the PDFs, then answers with specific papers and why each one fits."),
        b("bullet", "What do we have on thermal bleaching in the Caribbean?"),
        b("bullet", "Which papers used photogrammetry or structure from motion?"),
        b("bullet", "Five most cited papers on herbivory, and what each one found."),
        b("bullet", "I am starting a thesis chapter on coral disease. What should I read first?"),
        b("bullet", "Which papers cover the US Virgin Islands?"),
        b("gap"),

        b("heading", "Add papers"),
        b("prose", "Drop PDFs in the ingest folder and tell Claude to run the ingest. Claude "
                   "renames each file, pulls the citation record, writes the tags and the "
                   "summary, and adds a row to the index."),
        b("gap"),

        b("heading", "Set up, once"),
        b("pair", "The desktop or terminal app. The claude.ai website cannot open files on your "
                  "machine.", left="Claude Code subscription"),
        b("pair", "Sync this folder to your machine, because Claude opens the real PDFs on disk. "
                  "You are set once the folder shows up in Finder.", left="Google Drive for Desktop"),
        b("pair", "They give Claude the naming rules, the tag vocabulary, and the list of things "
                  "it may not invent.", left="Two skills installed"),
        b("gap"),

        b("heading", "Install"),
        b("prose", "Paste this to Claude:"),
        b("code", "Install the literature library skills from "
                  "https://github.com/laurenkolinger/lit-skills by following the Install section "
                  "of its README, then ask me where my library lives."),
        b("prose", "Or run three commands in a terminal:"),
        b("code", "git clone https://github.com/laurenkolinger/lit-skills.git"),
        b("code", "cd lit-skills"),
        b("code", './install.sh "<path to this Lit folder>"'),
        b("gap"),

        b("heading", "The two skills"),
        b("pair", "Answers questions about the library and recommends papers.", left="lit-search"),
        b("pair", "Adds new papers from the ingest folder.", left="lit-ingest"),
        b("gap"),

        b("heading", "Columns"),
    ]
    blocks += [b("pair", definition, left=name) for name, definition in COLUMN_DEFINITIONS]
    blocks += [
        b("gap"),
        b("heading", "How impact is set"),
        b("prose", "Impact comes from the citation count and the citations per year, so an old "
                   "paper and a new one are judged on comparable terms."),
    ]
    blocks += [b("pair", rule, left=label) for label, rule in IMPACT_RULES]
    blocks += [
        b("prose", "Unrated means the count is unknown, not low. An unrated paper may still be "
                   "widely cited."),
        b("gap"),

        b("heading", "Keys"),
        b("code", "Nemeth_2005_PopulationCharacteristicsRecoveringVirginIslands"),
        b("prose", "A key is the first author surname, the year, and the first few real words of "
                   "the title. The key is also the PDF filename. Refer to papers by key: people "
                   "retype and shorten titles, so a title is not a reliable identifier. If you "
                   "rename a PDF by hand, Claude can no longer match it to its row."),
        b("gap"),

        b("heading", "What Claude does and does not do"),
        b("pair", "Claude adds a row only after the PDF is in this folder.", left="Adding a row"),
        b("pair", "Claude leaves a citation or DOI blank when it cannot confirm one. It does not "
                  "guess.", left="Citations and DOIs"),
        b("warn", "lit_index.csv holds the real data. This spreadsheet is generated from it, so "
                  "the next rebuild overwrites anything you type in here."),
    ]
    return blocks

