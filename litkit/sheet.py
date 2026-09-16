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
SEARCH_TITLE = "Search"
TAGS_TITLE = "Tags"
LINK_TEXT = "open"
# A computed column the reader filters on. It is not an index column: it exists only in the
# workbook, so the CSV stays free of spreadsheet machinery.
MATCH_COLUMN = "match"
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
    "match": 9,
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
    headers = list(columns or COLUMNS) + [MATCH_COLUMN]

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

    # Fill the match column. Every function here predates dynamic arrays, so the formula
    # survives being opened and resaved by Google Sheets as an xlsx.
    tags_letter = _column_letter(headers, "tags_all")
    title_letter = _column_letter(headers, "title")
    summary_letter = _column_letter(headers, "summary")
    match_position = headers.index(MATCH_COLUMN) + 1
    for offset in range(len(rows)):
        line = offset + 2
        tag_tests = " ".join(
            f'IF(Search!$B${box}="",TRUE,ISNUMBER(SEARCH("|"&Search!$B${box}&"|",${tags_letter}{line}))),'
            for box in (3, 4, 5)
        )
        text_test = (
            f'IF(Search!$B$6="",TRUE,'
            f'OR(ISNUMBER(SEARCH(Search!$B$6,${title_letter}{line})),'
            f'ISNUMBER(SEARCH(Search!$B$6,${summary_letter}{line}))))'
        )
        cell = sheet.cell(row=line, column=match_position, value=f"=AND({tag_tests}{text_test})")
        cell.alignment = data_alignment

    last_column = get_column_letter(len(headers))
    last_row = len(rows) + 1
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:{last_column}{last_row}"
    sheet.sheet_view.showGridLines = True

    _add_search_tab(workbook, headers, len(rows))
    _add_tags_tab(workbook, rows)

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


def _add_search_tab(workbook, headers, row_count):
    """Add a tab holding the search boxes that drive the match column on the index.

    Deliberately built from IF, AND, ISNUMBER and SEARCH only. FILTER and SORT are
    dynamic-array functions, and the xlsx format cannot carry them: on 2026-09-16 Google Sheets
    opened this workbook, found it could not represent them, replaced the formula with
    __xludf.DUMMYFUNCTION and saved that back. Old functions survive the round trip.

    Parameters:
        workbook (openpyxl.Workbook): the workbook being built.
        headers (list[str]): the column order on the index sheet.
        row_count (int): how many data rows the index sheet holds.

    Returns:
        None
    """
    sheet = workbook.create_sheet(SEARCH_TITLE, 0)
    match_letter = _column_letter(headers, MATCH_COLUMN)

    sheet["A1"] = "Search the VICAR lit library"
    sheet["A1"].font = Font(bold=True, size=14)

    labels = ["Tag 1", "Tag 2", "Tag 3", "Words in title or summary"]
    hints = [
        "Type a tag exactly as it appears on the Tags tab, for example AUV.",
        "Leave a box empty to ignore it. Boxes combine with AND.",
        "",
        "Free text, for example bleaching or St. Thomas.",
    ]
    for offset, (label, hint) in enumerate(zip(labels, hints)):
        line = 3 + offset
        sheet[f"A{line}"] = label
        sheet[f"A{line}"].font = Font(bold=True)
        box = sheet[f"B{line}"]
        box.fill = PatternFill("solid", fgColor="FFF2CC")
        box.border = Border(*[Side(style="thin", color="BFBFBF")] * 4)
        if hint:
            sheet[f"C{line}"] = hint
            sheet[f"C{line}"].font = Font(italic=True, color="666666")

    sheet["A8"] = "How to see the results"
    sheet["A8"].font = Font(bold=True, size=12)
    steps = [
        "1. Type one or more tags into the yellow boxes above.",
        f"2. Go to the '{SHEET_TITLE}' tab.",
        f"3. Click the filter arrow on the '{MATCH_COLUMN}' column and tick TRUE only.",
        "4. The rows left are your matches. Column A links straight to each PDF.",
        "5. To start over, clear the yellow boxes and set that filter back to all.",
    ]
    for offset, step in enumerate(steps):
        sheet[f"A{9 + offset}"] = step

    sheet["A16"] = "Matches right now"
    sheet["A16"].font = Font(bold=True)
    sheet["B16"] = f"=COUNTIF('{SHEET_TITLE}'!${match_letter}$2:${match_letter}${row_count + 1},TRUE)"
    sheet["B16"].font = Font(bold=True, size=12)
    sheet["C16"] = "out of " + str(row_count) + " papers"
    sheet["C16"].font = Font(italic=True, color="666666")

    sheet["A18"] = "If you would rather not use the boxes"
    sheet["A18"].font = Font(bold=True)
    sheet["A19"] = (
        f"On the {SHEET_TITLE} tab, filter the tags_all column by condition, custom formula is:"
    )
    tags_letter = _column_letter(headers, "tags_all")
    sheet["A20"] = f'=AND(ISNUMBER(SEARCH("|AUV|",${tags_letter}2)),ISNUMBER(SEARCH("|USVI|",${tags_letter}2)))'
    sheet["A20"].font = Font(name="Menlo", size=10)

    for letter, width in {"A": 62, "B": 30, "C": 46}.items():
        sheet.column_dimensions[letter].width = width


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
    sheet["A2"] = "Copy a tag into the Search tab to filter by it."
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
