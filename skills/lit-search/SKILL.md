---
name: lit-search
description: "Use whenever someone wants to find papers in the lab literature library: they ask what the lab has on a topic, ask for reading on something, say they are starting a thesis chapter or a proposal section and need sources, ask which paper covers a method, ask what to read first, or ask for the most cited work on a subject. Start by asking what they are looking for and offering the topics actually present in the library, then return specific papers with a reason for each. It searches and recommends; it does not add papers, which is the lit-ingest skill."
---

# VICAR lit search

This skill answers "what do we have on X" from the lab literature index. It reads
`lit_index.csv`, which lists every paper the lab holds. Never recommend a paper that is not
in that file, and never invent a citation.

Find the library at `$VICAR_LIT_HOME` when that is set, otherwise at:

```
$VICAR_LIT_HOME
```

## Open the conversation before you search

Do not guess what someone wants from one word. Open with a short question and show them what
the library actually holds, so they choose from real options rather than imagining them.

Run this first, every time, so your offer reflects the library as it is today:

```bash
cd "$VICAR_LIT_HOME" && python3 -c "
import csv, collections
rows = list(csv.DictReader(open('lit_index.csv')))
counts = collections.Counter()
for r in rows:
    for t in (r['topic_tags'] or '').split(','):
        if t.strip(): counts[t.strip()] += 1
print(f'{len(rows)} papers in the library')
print()
for tag, n in counts.most_common():
    print(f'{n:4}  {tag}')
"
```

Then ask, in one message, something like:

> The library has 243 papers. What are you working on, and how deep do you need to go?
> The biggest topics are coral reef (120), monitoring (61), reef fish (44), climate change (31),
> resilience (29) and coral disease (24). There is also a methods side: AUV, photogrammetry,
> deep learning, acoustic telemetry.
>
> Tell me the question you are trying to answer and I will pull the papers that bear on it.
> If you want a starting point rather than everything, say so and I will give you five.

Ask about these when the answer would change what you return, and not otherwise:

1. The question they are answering, not just the subject. "How fast do reefs recover after a
   hurricane" points somewhere different from "hurricanes."
2. What it is for: a thesis chapter, a proposal section, a methods choice, a lab meeting.
3. How much they want: a starting five, or everything the library holds.
4. Whether they need recent work specifically, or the foundational papers.

## Search the index

Match against `tags_all`, which wraps every tag in pipes so a whole tag matches and a fragment
does not. Also search `title`, `summary` and `key_findings` as free text, because a paper is
often relevant without carrying the obvious tag.

```bash
cd "$VICAR_LIT_HOME" && python3 -c "
import csv, re, sys
TAGS = ['AUV', 'USVI']            # whole tags, combined with AND
TEXT = ['hotspot', 'mapping']     # free text, combined with OR, searched across the row
rows = list(csv.DictReader(open('lit_index.csv')))
def hit(r):
    if TAGS and not all(f'|{t}|' in r['tags_all'] for t in TAGS): return False
    if not TEXT: return True
    blob = ' '.join([r['title'], r['summary'], r['key_findings']]).lower()
    return any(t.lower() in blob for t in TEXT)
found = [r for r in rows if hit(r)]
found.sort(key=lambda r: int(r['citations'] or 0), reverse=True)
print(f'{len(found)} papers')
for r in found[:25]:
    print(f\"{r['year']}  {(r['citations'] or '-'):>6}  {r['impact']:<12} {r['first_author']}\")
    print(f\"        {r['title'][:95]}\")
    print(f\"        {r['key']}\")
"
```

Useful variations:

- Recent work only: add `if int(r['year'] or 0) >= 2020`.
- The foundational papers: sort by `citations` and take the top of the list.
- What a specific person in the lab wrote: filter on `authors` containing the surname.
- Everything tied to one part of VICAR: filter on `vicar_relevance`.

## Answer with papers, not with a list

Give between three and eight papers unless they asked for everything. For each one, write the
citation, then one sentence saying why this paper answers their question. The `summary` column
holds what the study did and found; use it, do not restate the title.

Order them the way a person should read them: the one that frames the question, then the ones
that answer it, then the methods papers.

Format each as:

> **Nemeth 2005**, Population characteristics of a recovering US Virgin Islands red hind
> spawning aggregation following protection. Marine Ecology Progress Series, 195 citations.
> Start here for the Red Hind Bank baseline: mean size rose about 10 cm over 12 years of
> closure, which is the comparison every later USVI aggregation paper is measured against.
> [open](the link column value)

Then say what you did not include and why, in one line. "I left out the Belize collapse case
studies, say the word if you want the contrast."

## Rules that keep the answers honest

1. Every paper you name must exist in `lit_index.csv`. Quote its `key` so it can be checked.
2. Never state a finding the row does not support. When `key_findings` says the text was too
   thin, say the summary is thin and offer to read the PDF properly.
3. A row whose `notes` mention a metadata mismatch or a scan is not trustworthy on its
   citation details. Say so rather than passing it on.
4. Citation counts are a snapshot. Give the number with its `citations_retrieved` date when
   the count is doing real work in your answer.
5. When the library holds nothing useful, say that plainly and suggest what to search for
   outside it. Do not pad an answer with loosely related papers.
6. When someone asks for "the best" paper, rank on fit to their question first, and use
   `impact` only to break ties. A high citation count on an off-topic paper helps nobody.

## When the library comes up short

Say which parts of their question the library covers and which it does not. Offer to hunt for
the missing papers, and tell them the route: find the PDFs, drop them in `ingest/`, and run the
`lit-ingest` skill. Papers arrive as citations without files quite often, and the index
only holds papers the lab actually has.

## Checklist before you answer

- You asked what they are working on before searching, unless they already said.
- You offered real topics, read from the file today, not remembered ones.
- Every paper named appears in `lit_index.csv`, quoted by `key`.
- Every paper carries one sentence on why it answers their question.
- Papers are ordered for reading, not by score.
- Thin summaries, mismatched metadata and stale counts are flagged rather than smoothed over.
- You said what you left out.
