"""Build the formatted workbook that becomes the VICAR Lit Google Sheet.

Every long text column clips rather than wraps, and every data row keeps a fixed short
height, so the sheet stays compact. A reader clicks a cell to read the full summary.
"""

import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .index import COLUMNS, TAG_DELIMITER, TAG_FACETS, build_tags_all

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
    sheet["A2"] = "Copy a tag into the Search tab to filter by it."
    sheet["A2"].font = Font(italic=True, color="666666")

    sheet["A3"] = "See the Read me tab for how to filter the index by these tags."
    sheet["A3"].font = Font(italic=True, color="666666")

    for position, label in enumerate(["facet", "tag", "papers", "paste this to filter"]):
        cell = sheet.cell(row=4, column=position + 1, value=label)
        cell.font = Font(bold=True, color=HEADER_FONT_COLOR)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)

    line = 5
    for facet in TAG_FACETS:
        for tag, count in sorted(counts.get(facet, {}).items(), key=lambda kv: (-kv[1], kv[0])):
            sheet.cell(row=line, column=1, value=facet.replace("_tags", "").replace("_", " "))
            sheet.cell(row=line, column=2, value=tag)
            sheet.cell(row=line, column=3, value=count)
            sheet.cell(row=line, column=4, value=f"{TAG_DELIMITER}{tag}{TAG_DELIMITER}")
            sheet.row_dimensions[line].height = DATA_ROW_HEIGHT
            line += 1

    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 34
    sheet.column_dimensions["C"].width = 10
    sheet.column_dimensions["D"].width = 30
    sheet.freeze_panes = "A5"
    if line > 5:
        sheet.auto_filter.ref = f"A4:D{line - 1}"


# Every column, in index order, with a definition a reader can act on.
COLUMN_DEFINITIONS = [
    ("link", "Click to open this paper's PDF in Google Drive. Filled automatically."),
    ("key", "The paper's permanent id, built as FirstAuthor_Year_ShortTitle. It is also the PDF's "
            "filename without .pdf, so a key always tells you which file to open. Quote the key "
            "when you refer to a paper in notes or in email, because titles get retyped and keys "
            "do not. Never edit a key by hand: fix the metadata and the key and filename follow."),
    ("filename", "The PDF in the pdfs folder. Always the key plus .pdf."),
    ("authors", "Full author list, semicolons between names. Long lists are shortened here with "
                "et al.; the CSV keeps every name."),
    ("first_author", "First author surname only. Use this to sort or filter by person."),
    ("year", "Publication year. Stored as a number so it sorts correctly."),
    ("title", "Paper title, taken from the publisher record where one was found."),
    ("journal", "Journal, book or report series."),
    ("doi", "Digital Object Identifier, the permanent address of the paper. Blank means none was "
            "found or the one on the page pointed at a different paper and was removed."),
    ("url", "Publisher or open access link."),
    ("citations", "Times this paper has been cited, from OpenAlex. Blank means no record was "
                  "found, never zero."),
    ("citations_retrieved", "The date that count was fetched. An old date means an old number."),
    ("citations_per_year", "Citations divided by the paper's age in years. This is what makes a "
                           "2024 paper comparable to a 1994 one."),
    ("impact", "A label derived from the two columns above. See the impact rules below."),
    ("tags_all", "Every tag on the paper, joined with pipe characters. This is the column to "
                 "filter on. Rebuilt automatically from the four tag columns, so never edit it."),
    ("topic_tags", "What the paper is about: coral reef, bleaching, resilience, and so on."),
    ("method_tags", "How the work was done: AUV, photogrammetry, deep learning, telemetry."),
    ("region_tags", "Where: USVI, Caribbean, Belize, Pacific."),
    ("taxa_tags", "What organisms: scleractinia, sponge, Nassau grouper."),
    ("vicar_relevance", "Which part of VICAR the paper serves: automation infrastructure, reef "
                        "research, VICARIUS platform, STEM workforce, or background."),
    ("summary", "Two to four sentences on what the study did and what it found. Click the cell "
                "to read it all; the text is clipped so rows stay one line tall."),
    ("key_findings", "The specific claims, separated by semicolons."),
    ("source", "Which folder or person this copy came from."),
    ("date_added", "When the row was created."),
    ("notes", "Anything needing a human eye: a scanned PDF, a thin summary, or metadata that "
              "did not match the file."),
]

IMPACT_RULES = [
    ("high impact", "500 or more citations, or 40 or more per year"),
    ("well cited", "100 or more citations, or 15 or more per year"),
    ("standard", "10 or more citations"),
    ("emerging", "Fewer than 10 citations and published within the last 3 years"),
    ("low", "Everything else"),
    ("unrated", "No citation record was found, so no judgement is made"),
]


def _add_readme_tab(workbook, headers, row_count):
    """Add the documentation tab and put it first.

    The spreadsheet travels to people who were not here when it was built, so it explains
    itself: what the columns mean, how to filter it, how the impact label is derived, and how to
    set up the Claude skill that maintains it.

    Parameters:
        workbook (openpyxl.Workbook): the workbook being built.
        headers (list[str]): the column order on the index sheet.
        row_count (int): how many papers the index holds.

    Returns:
        None
    """
    sheet = workbook.create_sheet(README_TITLE, 0)
    tags_letter = _column_letter(headers, "tags_all")
    line = 1

    heading = Font(bold=True, size=14, color=HEADER_FILL)
    subheading = Font(bold=True, size=11)
    body = Alignment(horizontal="left", vertical="top", wrap_text=True)
    mono = Font(name="Menlo", size=10)
    quiet = Font(italic=True, color="666666")

    def title(text):
        nonlocal line
        line += 1
        cell = sheet.cell(row=line, column=1, value=text)
        cell.font = heading
        sheet.row_dimensions[line].height = 26
        line += 1

    def pair(left, right, label_font=subheading):
        nonlocal line
        a = sheet.cell(row=line, column=1, value=left)
        a.font = label_font
        a.alignment = body
        b = sheet.cell(row=line, column=2, value=right)
        b.alignment = body
        sheet.row_dimensions[line].height = max(15, 13 * (1 + len(str(right)) // 95))
        line += 1

    def note(text, font=None):
        nonlocal line
        cell = sheet.cell(row=line, column=1, value=text)
        cell.font = font or Font(size=11)
        cell.alignment = body
        sheet.row_dimensions[line].height = max(15, 13 * (1 + len(str(text)) // 130))
        line += 1

    def gap():
        nonlocal line
        line += 1

    sheet.cell(row=1, column=1, value="VICAR lab literature library").font = Font(bold=True, size=18, color=HEADER_FILL)
    sheet.row_dimensions[1].height = 30
    line = 2
    note(f"{row_count} papers. Every paper here has a PDF in the pdfs folder and exactly one row "
         f"on the '{SHEET_TITLE}' tab.", quiet)
    gap()

    title("How to use this")
    pair("Find a paper", f"Go to the '{SHEET_TITLE}' tab. Click the filter arrow on the "
                         f"{tags_letter} column (tags_all), choose Filter by condition, then Text "
                         f"contains, and type a tag wrapped in pipes, for example |AUV|.")
    pair("Two tags at once", "Same menu, but choose Custom formula is, and enter an = sign "
                             f"followed by: AND(ISNUMBER(SEARCH(\"|AUV|\",${tags_letter}2)),"
                             f"ISNUMBER(SEARCH(\"|USVI|\",${tags_letter}2)))")
    pair("Why the pipes", "Tags are wrapped in pipe characters so a search matches a whole tag. "
                          "Searching AUV without pipes would also match AUV survey.")
    pair("See every tag", f"The '{TAGS_TITLE}' tab lists each tag with how many papers carry it, "
                          "and the exact string to paste.")
    pair("Open a paper", "Click the word open in column A. It goes straight to the PDF in Drive.")
    pair("Read a summary", "Click the cell. Long text is clipped on purpose so rows stay one line "
                           "tall and the table stays scannable.")
    pair("Add a paper", "Put the PDF in the ingest folder, then ask Claude to run the ingest. "
                        "Everything else is automatic.")
    gap()

    title("What each column means")
    for name, definition in COLUMN_DEFINITIONS:
        pair(name, definition, label_font=Font(bold=True, name="Menlo", size=10))
    gap()

    title("How the impact label is decided")
    note("Raw citation counts favour old papers, so a paper is judged on its total and on its "
         "rate. Whichever test it passes first sets the label. The rate is citations divided by "
         "age in years, with a one year floor so a paper published this year is not divided by "
         "zero.")
    gap()
    for label, rule in IMPACT_RULES:
        pair(label, rule, label_font=Font(bold=True, size=11))
    gap()
    note("unrated is not a low score. It means no citation record was found, either because the "
         "paper has no DOI in the file or because it is a report, thesis or preprint that "
         "citation databases do not index.", quiet)
    gap()

    title("How the key works")
    note("A key looks like Nemeth_2005_PopulationCharacteristicsRecoveringVirginIslands. It is "
         "the first author's surname, the year, and the first few significant words of the "
         "title. It is also the filename of the PDF, so a key always tells you which file to "
         "open, and a filename always tells you which row to look at.")
    note("Use the key when you refer to a paper in notes, in email or in a manuscript draft. "
         "Titles get retyped and shortened; keys do not change.")
    note("Never rename a PDF by hand. If a key is wrong it is because the metadata is wrong. Fix "
         "the metadata and the key and filename are rebuilt to match.")
    gap()

    title("Setting up Claude to work with this folder")
    note("Two things are needed, and both are one time.")
    gap()
    pair("1. Google Drive for Desktop", "This folder has to be synced to the computer, not just "
         "visible in a browser. Claude reads and writes real files on disk, and Drive carries "
         "the changes back up. Without the sync there is nothing for it to open.")
    pair("2. A Claude Code account", "Claude Code is the terminal and desktop app, not the "
         "website. Install it and sign in.")
    gap()
    note("Then install the two skills, which teach Claude how this library works:", subheading)
    note("git clone https://github.com/laurenkolinger/lit-skills.git", mono)
    note("cd lit-skills && ./install.sh \"<the full path to this Lit folder>\"", mono)
    gap()
    note("Or paste this to Claude and let it do the whole thing:", subheading)
    note("Install the literature library skills from https://github.com/laurenkolinger/"
         "lit-skills by following the Install section of its README, then ask me where my "
         "library lives.", mono)
    gap()
    pair("lit-ingest", "Files new PDFs dropped in the ingest folder: names them, looks up the "
                       "citation, writes tags and a summary, and updates this spreadsheet.")
    pair("lit-search", "Answers questions like what do we have on AUVs in the USVI. It asks what "
                       "you are working on, shows the topics actually present, and comes back "
                       "with specific papers and a reason for each.")
    gap()

    title("Rules this library keeps")
    note("A paper gets a row only when its PDF is actually here. Nothing is listed on a promise.")
    note("No citation is invented and no DOI is guessed. A lookup that returns a different paper "
         "than the file is rejected, and the row says so in notes rather than looking confident.")
    note("The CSV next to this file is the source of truth. This spreadsheet is built from it, "
         "never the other way round, so anything typed here is overwritten on the next update.",
         Font(bold=True, size=11, color="9C2500"))

    sheet.column_dimensions["A"].width = 26
    sheet.column_dimensions["B"].width = 108
    sheet.sheet_view.showGridLines = False
