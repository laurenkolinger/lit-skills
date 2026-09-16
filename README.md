# Literature library skills

Two Claude Code skills that turn a folder of PDFs into a searchable lab literature library.

- **lit-ingest** files new PDFs, names them to a standard, pulls citation counts from OpenAlex,
  writes tags and a summary, and keeps a CSV index plus a formatted spreadsheet in step.
- **lit-search** answers "what do we have on X" from that index, asking what you are working on
  before it searches, and naming only papers the library actually holds.

Built for a coral reef lab, useful for any lab with a pile of PDFs and no way through them.

## Install

Paste this to your agent:

> Install the literature library skills from https://github.com/laurenkolinger/lit-skills
> by following the Install section of its README, then tell me where my library lives.

Or do it yourself:

```bash
git clone https://github.com/laurenkolinger/lit-skills.git
cd lit-skills
./install.sh /path/to/your/library
```

`install.sh` copies both skills into `~/.claude/skills/`, copies the `litkit` python package
into your library folder, creates the folder layout, and prints the one line to add to your
shell profile.

## What it expects

One folder, anywhere, pointed at by `VICAR_LIT_HOME`:

```
library/
  lit_index.csv        the index, one row per PDF, the source of truth
  lit_index.xlsx       the formatted spreadsheet, three tabs: Search, Lit index, Tags
  pdfs/                one clean copy per paper, named FirstAuthor_Year_ShortTitle.pdf
  ingest/              drop new PDFs here
  skill/litkit/        the python package
  logs/                one log per run
```

The rule the whole thing rests on: **the index indexes the contents.** A paper has a row only
when its PDF is in `pdfs/`. Nothing is listed on a promise.

## Use it

Drop PDFs in `ingest/`, then ask your agent to run the ingest. Or run it yourself:

```bash
cd "$VICAR_LIT_HOME/skill" && python3 -m litkit.ingest "$VICAR_LIT_HOME" --source "who sent them"
```

To find something, ask your agent what the library has on a topic. The lit-search skill opens by
showing you the topics actually present, so you pick from what exists.

## The tools

| Command | What it does |
|---|---|
| `python3 -m litkit.ingest <root>` | File everything in `ingest/`, append rows, rebuild the sheet |
| `python3 -m litkit.ingest <root> --import DIR` | Bulk import from folders elsewhere, originals untouched |
| `python3 -m litkit.index <root>` | Audit: does the index match the folder? Exits 0 when it does |
| `python3 -m litkit.refresh <root>` | Refresh citation counts by DOI |
| `python3 -m litkit.repair <root>` | Re-derive metadata for rows that look wrong |
| `python3 -m litkit.corrections <root> file.csv` | Apply hand-checked fixes, renaming files to match |
| `python3 -m litkit.annotate <root> tags.csv` | Merge summaries and tags into the index |
| `python3 -m litkit.links <root> --ids ids.json` | Fill the click-through link column |

Every one takes `--dry-run`.

## Searching by more than one tag

Tags live twice: once per facet (`topic_tags`, `method_tags`, `region_tags`, `taxa_tags`) and
once joined in `tags_all`, pipe delimited:

```
|coral reef|monitoring|AUV|USVI|scleractinia|reef research|
```

The pipes are the point. A comma-separated list cannot be searched for two tags at once, and
matching bare text finds fragments. Wrapping each tag lets a spreadsheet match a whole tag with
`SEARCH("|AUV|", tags_all)`, and lets you AND several together. The Search tab does exactly
that: type up to three tags plus free text, and it filters with `FILTER` and `ISNUMBER(SEARCH(...))`.
No scripts, no add-ons.

The Tags tab lists every tag in use with a count, so you always search for something that exists.

## Citation counts and impact

Counts come from OpenAlex, which is free and needs no key. It has a daily budget that resets at
midnight UTC; when it runs out every lookup returns nothing, which looks the same as "not found".
Set `VICAR_LIT_CONTACT_EMAIL` to your address to use the polite pool.

The impact label is age adjusted, so a 2025 paper is not judged against a 1994 one:

| Label | Rule |
|---|---|
| high impact | 500+ citations, or 40+ per year |
| well cited | 100+ citations, or 15+ per year |
| standard | 10+ citations |
| emerging | under 10 citations, published within 3 years |
| low | everything else |
| unrated | no match found |

## What it refuses to do

It will not invent a citation, guess a DOI, or list a paper it does not hold. A DOI lookup whose
record plainly describes a different paper is rejected rather than written in, because a row
naming the wrong paper is worse than a row with no count. Rows it is unsure about say so in
`notes` instead of looking confident.

## Tests

```bash
cd lit-skills && python3 -m pytest tests/ -q
```

463 tests: unit, adversarial, integration and regression. The adversarial set is built from real
breakage, including PDFs whose embedded metadata held a production filename, journal running
heads that read like titles, and DOIs scraped off a data availability statement.

## Requirements

Python 3.9+, `openpyxl`, `pypdf`, and `pdftotext` (poppler) for fast text extraction.

```bash
pip install openpyxl pypdf
brew install poppler        # or: apt-get install poppler-utils
```

## License

MIT.
