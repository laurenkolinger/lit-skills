---
name: lit-ingest
description: "Use whenever PDFs are waiting in the VICAR lab Lit ingest folder, or whenever someone says they dropped papers in ingest, sent papers for the reference library, wants papers added to the lab literature index, or asks what is in the lab library. It files each PDF under the lab naming standard, pulls citation counts from OpenAlex, writes the tags and the summary, appends a row to lit_index.csv, and rebuilds the spreadsheet. It does not write literature reviews or draft prose about the papers."
---

# VICAR lit ingest

The VICAR lab literature library lives at:

```
$VICAR_LIT_HOME
```

That folder syncs to Google Drive, so everything you write there reaches the lab.

```
Lit/
  README.md            what the library is and how to add a paper
  lit_index.csv        the index, one row per PDF in pdfs/, the source of truth
  lit_index.xlsx       the formatted build artifact, uploaded as the Google Sheet
  pdfs/                one clean copy per paper, named to the standard
  ingest/              people drop new PDFs here
  ingest/_processed/   originals land here once a run files them
  skill/               this skill and the litkit modules that do the work
  logs/                one log per run
```

## The one rule that governs everything

The index indexes the contents. A row exists if and only if its PDF sits in `pdfs/`.
Never add a row for a paper the lab does not hold. Never leave a PDF in `pdfs/` with no row.

## Run the ingest

1. Check what is waiting:

```bash
ls "$VICAR_LIT_HOME/ingest"
```

2. Do a dry run first and read what it plans to do:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.ingest "$VICAR_LIT_HOME" --dry-run
```

3. Run it for real:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.ingest "$VICAR_LIT_HOME" --source "who sent them"
```

Pass `--source` the name of whoever supplied the batch, for example `--source "Savanna Saunders"`.
Add `--no-network` only when OpenAlex is unreachable, and say in your report that citation
counts were left empty so someone can rerun later.

4. Confirm the index and the folder agree:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.index "$VICAR_LIT_HOME"
```

It exits 0 when they agree. Any other exit means you fix the mismatch before you stop.

## Fill the rows the run could not

The run writes every column it can read from the file and from OpenAlex. It leaves the
judgement columns for you. Find them with:

```bash
cd "$VICAR_LIT_HOME" && python3 -c "
import csv
rows = list(csv.DictReader(open('lit_index.csv')))
todo = [r for r in rows if r['topic_tags'] == 'needs review']
print(len(todo), 'rows need tags and a summary')
for r in todo: print(' ', r['key'])
"
```

For each of those rows:

1. Read the paper's opening pages:

```bash
pdftotext -f 1 -l 3 -q "<path to the pdf in pdfs/>" - | head -120
```

2. Write `summary` as two to four sentences saying what the study did and what it found.
   Name the sites, the counts, and the direction of change when the paper gives them.
   Write plainly, no em dashes, American spelling.

   Weak: "This important paper explores various aspects of reef decline."
   Strong: "Alvarez-Filip and colleagues combined nearly 500 surveys from 1969 to 2008 across the
   wider Caribbean. Reef rugosity above 2 fell from about 45 percent of reefs to about 2 percent.
   The decline ran in three phases and held across shallow, mid-depth and deep reefs."

3. Write `key_findings` as short claims separated by semicolons, each one standing alone.

4. Tag from the controlled vocabulary below. Use several tags per facet when they fit, separated
   by commas. Leave a facet empty when nothing applies rather than reaching for a near match.

5. Write `vicar_relevance` naming which part of VICAR the paper serves.

6. Rebuild the spreadsheet after you edit the CSV:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -c "
from litkit import index, sheet
root = os.environ['VICAR_LIT_HOME']
rows = index.read_index(index.library_paths(root)['index'])
sheet.build_workbook(rows, root + '/lit_index.xlsx')
print(len(rows), 'rows written')
"
```

## The controlled vocabulary

Use these words exactly. Adding a new tag is allowed when nothing fits, and when you add one,
write it into this list in the same run so the vocabulary stays closed.

**topic_tags**: coral reef, mesophotic, fish spawning aggregation, reef fish, coral disease,
SCTLD, bleaching, hurricane, resilience, regime shift, connectivity, ecosystem function,
restoration, monitoring, mangrove, pelagic, water quality, ciguatera, herbivory,
fisheries management, marine protected area, climate change, STEM education, robotics ethics

**method_tags**: AUV, ROV, photogrammetry, 3D, machine learning, deep learning, computer vision,
image annotation, semantic segmentation, remote sensing, acoustic telemetry, passive acoustics,
SLAM, time series, modeling, field experiment, genetics, diver survey, benthic survey

**region_tags**: USVI, Caribbean, Belize, Cayman Islands, Florida, Bahamas, Pacific,
Great Barrier Reef, Red Sea, global

**taxa_tags**: scleractinia, Orbicella, Acropora, Porites, octocoral, sponge, algae, reef fish,
Nassau grouper, red hind, snapper, urchin, dinoflagellate, Symbiodiniaceae

**vicar_relevance**: automation infrastructure, reef research, STEM workforce, VICARIUS platform,
background

## Citation counts and the impact label

The run reads `cited_by_count` from OpenAlex, matching first by DOI and then by title. It stamps
`citations_retrieved` with the date, so an old count is visible as an old count. It never guesses
a number: a paper OpenAlex cannot match gets an empty count and `impact` of `unrated`.

The label is age-adjusted so a 2025 paper is not judged against a 1994 one:

| Label | Rule |
|---|---|
| high impact | 500 or more citations, or 40 or more per year |
| well cited | 100 or more citations, or 15 or more per year |
| standard | 10 or more citations |
| emerging | fewer than 10 citations, published within the last 3 years |
| low | everything else |
| unrated | OpenAlex returned no match |

To refresh counts across the whole library later, rerun the enrichment rather than editing
numbers by hand.

## The naming standard

Every PDF in `pdfs/` is named `FirstAuthor_Year_ShortTitle.pdf`, for example
`AlvarezFilip_2009_FlatteningCaribbeanCoralReefsRegion.pdf`.

- First author surname, accents folded to ASCII, hyphens and apostrophes removed.
- Four-digit year, or `nd` when the year is unknown.
- The first five significant title words in CamelCase, stopwords dropped.
- A collision takes `_b`, then `_c`.
- The filename without `.pdf` is the row `key`.

Never rename a file in `pdfs/` by hand. The key and the filename have to stay in step, so let
the code name it.

## What the run rejects, and what it keeps

It rejects a file that does not exist, is empty, lacks the PDF magic bytes, falls under 10 kB,
or cannot be parsed into pages. Rejected files stay in `ingest/` so someone can see them, and
the reason goes in the run log.

It keeps a scanned report whose pages carry no extractable text, because a scan is still library
content. Those rows get `low text (likely scanned), check metadata` in the `notes` column. Read
the first page yourself and correct the title, authors and year before you call the run done.

## Bulk import from somewhere else

To pull a folder of PDFs that should stay where it is, import instead of ingesting. The
originals are never moved or changed:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.ingest "$VICAR_LIT_HOME" --import "<folder>" --source "<label>" --progress
```

Add `--recursive` to descend into subfolders.

## Papers that arrive without a PDF

Someone often sends citations rather than files, as Savanna did in September 2026. The index
cannot hold them, because the index indexes contents. Find each PDF first, put it in `ingest/`,
then run. When a PDF cannot be found, tell the person which ones are still missing instead of
creating a row with no file behind it.

## Changing the code

The modules under `skill/litkit/` carry a test suite. Run it before and after any edit:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m pytest tests/ -q
```

A change that alters the columns, the naming standard, or the impact thresholds breaks the
regression tests on purpose. Update the test and the README in the same edit, so the promise
to the lab and the code never drift apart.

## When citation lookups stop working

OpenAlex gives every caller a free daily budget and resets it at midnight UTC. Once it is spent,
every lookup returns nothing, which looks exactly like "this paper is not in OpenAlex". Check for
it before concluding a paper is unknown:

```bash
curl -s "https://api.openalex.org/works/doi:10.1038/s41598-019-54681-2?select=cited_by_count" | head -c 200
```

A rate limit message means the budget is gone. Run the ingest with `--no-network` so the papers
still get filed, then rerun the enrichment the next day to fill the citation counts.

## Repairing rows that came out wrong

Three tools handle the three ways a row goes bad. Each takes a dry run first.

**A title or author that extraction got wrong.** `repair` re-reads the PDF and asks OpenAlex
again, and rewrites only what improves. It changes an author list only on an OpenAlex match,
never on a guess from the page text.

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.repair "$VICAR_LIT_HOME" --dry-run
```

**A row you have read and know the answer for.** Write it into a corrections CSV with the
columns `old_key,title,authors,first_author,year,journal,doi,new_key` and apply it. This is the
right tool when you have the paper in front of you: reading it beats tuning a heuristic. The
run renames the PDF to match the new key and reports any duplicates the fix reveals.

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.corrections "$VICAR_LIT_HOME" corrections.csv --dry-run
```

**A row whose PDF is a different paper than its metadata.** This happens when a DOI was picked
up off a data-availability statement or a neighbouring article in a reprint. The ingest now
flags it in `notes` as "metadata mismatch, check this row". Confirm by reading page 1, then fix
it with a correction, and clear `citations`, `citations_retrieved`, `citations_per_year` and set
`impact` to `unrated`, because those numbers belong to the wrong paper.

## Merging summaries and tags

Write the judgement columns into their own CSV with the header
`key,summary,key_findings,topic_tags,method_tags,region_tags,taxa_tags,vicar_relevance`,
then merge. Blank cells never erase what is already there, so a partial pass is safe.

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.annotate "$VICAR_LIT_HOME" annotations.csv --dry-run
```

Add `--strict` to refuse the merge if any tag falls outside the controlled vocabulary.

## Reading a paper for its summary

Two pages is often not enough. A journal cover page, a PubMed Central banner or a download
notice can fill them, and the abstract starts on page 2 or 3. When the text looks like front
matter, read deeper before concluding the paper is unusable:

```bash
pdftotext -f 1 -l 8 -q "$VICAR_LIT_HOME/pdfs/<file>.pdf" - | head -160
```

Assign tags from the title even when the text stays thin. A row with no tags cannot be filtered,
which is worse than a row with a short summary.

## The link and tags_all columns

`link` is the first column and holds a Drive URL straight to the PDF. It is filled by:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.links "$VICAR_LIT_HOME" --ids data/drive_ids.json
```

`data/drive_ids.json` maps each filename to its Drive file id. Drive ids survive a rename, so
after a corrections run that renamed files, remap the affected entries rather than refetching
everything. When a filename has no id the row still gets a working Drive search link, so no row
is ever left without a way through.

To rebuild the id map after adding papers, ask an agent to page the Drive folder with the Drive
connector. Be warned: the connector's `nextPageToken` pagination cycles forever on this folder
and silently returns only the first 164 files. Partition the query by `createdTime` windows so
each window returns under 100 results and no token.

`tags_all` is derived from the four facet columns every time the index is written. Never edit it
by hand, edit the facets. It wraps each tag in pipes so the Search tab can match a whole tag.

## Refreshing citation counts

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.refresh "$VICAR_LIT_HOME"          # fill the blanks
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.refresh "$VICAR_LIT_HOME" --all    # update everything
```

It looks up by DOI only. It never searches by title, because a title search that matched the
wrong paper is how wrong counts got into the index in the first place.

## When a row describes the wrong paper

A DOI scraped off a data availability statement, or off a neighbouring article in a reprint,
resolves to someone else's paper. The enrichment now tests the match against the title read from
the PDF and rejects it outright when they plainly disagree, clearing the DOI and flagging
`metadata mismatch, check this row` in `notes`. When you see that flag:

1. Read page 1 of the PDF and establish what the paper actually is.
2. Write a corrections row with the real title, authors, year and, only if you can read it off
   the page or verify it, the DOI. Use `<clear>` in a cell to empty a field you know is wrong
   but cannot replace.
3. Rerun `litkit.refresh --keys <the key>` so the citation count matches the corrected DOI.

Never guess a DOI. On 2026-09-16 the plausible-looking DOI for a paper turned out to belong to a
completely different article in the same journal, and only a lookup caught it.

## Checklist before you call an ingest done

- The dry run was read before the real run.
- `python3 -m litkit.index <root>` exited 0.
- Every row that the run left as `needs review` now carries tags, a summary and key findings.
- Every scanned row had its title, authors and year checked by eye.
- `lit_index.xlsx` was rebuilt after the last CSV edit.
- Rejected files were reported by name, with the reason, to whoever sent them.
- The run log in `logs/` names every paper added.
