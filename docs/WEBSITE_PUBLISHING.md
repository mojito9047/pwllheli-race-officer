# Website publishing HTML export

The series page includes a **Preview HTML** / **Download HTML** export for producing a single results file that can be uploaded to a club website. (Preview was called *Publish HTML* until v0.279 — it opens the page to look at and publishes nothing.)

There is also a **CSV** export on both the race and series pages, and it is a different thing for a different job: from v0.243 it is a **Sailwave import file**, not a report to read. See [Sailwave CSV export](#sailwave-csv-export) below. If you want results a person reads, use the HTML on this page.

## What it creates

The export is a standalone HTML file containing:

- the Pwllheli Sailing Club logo and any configured sponsor logos in a grey banner at the top of the page;
- the series title and generation time;
- a matrix of result links, with one row per IRC/YTC class result table;
- an **Overall** link for each class table;
- links to the race result tables for that class;
- series summary tables with race-by-race scores, discards and totals;
- race result tables with rank, class, boat, sail number, rating, status, start, finish, elapsed,
  corrected time and per-boat finish-video links where public clips exist &mdash; including a
  finish taken on the hut's horn switch, which is recorded as a horn clip rather than a finish
  one and was missed until v0.285;
- public start-video links above each race table where start clips exist. Missing/unavailable website-facing clips are shown simply as **No Video**.

The layout is deliberately similar to a Sailwave published results page, but it is generated directly from the Race Officer database and uses the app's current IRC/YTC class, rating snapshot, discard and series-scoring logic.

## How to export

1. Open **Series**.
2. Open the series to publish.
3. Check that race results and series scores look correct on the series page.
4. Click **Preview HTML** to open the standalone page in a new browser tab.
5. Click **Download HTML** to save the file.
6. Upload the downloaded `.html` file to the club website.

## Publishing it from the app (v0.281)

Steps 5 and 6 are the ones that happen on Monday if they happen at all. **Publish to website** does
both: it renders the results as they stand and uploads them to the club's public Cloudflare R2
bucket, so the standings can go out at the end of each day's racing without anyone opening an FTP
client.

It uses **the same public bucket the race videos go to** — the one configured under Settings →
Video recording, with its public base URL. A second bucket would mean second credentials, a second
base URL and a second thing to get wrong. The button only appears once that bucket is configured.

Each series publishes into its own folder, `results/series-<id>/`, and **every publish writes two
objects**:

| Object | Cached | What it is for |
| --- | --- | --- |
| `<Series>-results-<date>-<time>.html` | a year, immutable | the record of what went out that evening; never changes |
| `latest.html` | one minute | the link for the club website |

Put **`latest.html`** on the club website. Paste it once and it keeps up for the rest of the
season. The one-minute cache is what makes that work: a longer one and Cloudflare would go on
serving Saturday's standings on Wednesday, which is the fault this whole feature exists to
prevent, arriving by another route.

**Published…** on the series page lists what has gone out, with the club-website link in a box to
copy and each dated publish openable. The competitor landing page shows a **Published results**
link on the series roll-up, pointing at the same `latest.html`, so a competitor and the club
website are reading the same document.

Nothing is recorded unless the upload succeeded, so the list never offers a link that was never
written. A failed publish says what went wrong rather than "try again" — the bucket is over the
hut's 4G link and the credentials are somebody else's to fix.

## Notes

- The file embeds the Pwllheli Sailing Club logo and configured sponsor logos directly into the HTML, so normal website upload still only requires one `.html` file. The logo strip sits on a grey banner to help pale sponsor logos stand out.
- Video links are direct Cloudflare R2 URLs, and only appear for clips that have been uploaded
  there. A published file is read from the club website, on phones and on computers at home, so
  a link back into the hut is no link at all &mdash; until v0.285 that is what it was, and one
  measured document held 43 of them. A clip that has not been published shows as **No Video**,
  in the preview as well as the published file, so the preview tells the truth about what will
  go out. Turn public video publishing on under **Settings &rarr; Video recording** to get the
  links. The competitor pages are unaffected: they are served by the hut, so they still play
  clips straight from it.
- The export is provisional; it reflects the data and scoring settings at the time it is generated.
- If you edit finishes, ratings, class bands, starts or discards, download a new HTML file and replace the previous one on the website.

## Printing

When printed, the export shows all series and race tables rather than only the currently selected table. This makes it useful for PDF printing or archiving.

## Sailwave CSV export

From **v0.243** the CSV downloads on the race page and the series page are **Sailwave import files**. Before that they were stacked human-readable tables, which Sailwave cannot parse at all.

Sailwave's importer wants one flat table: a single header row of field names it recognises, then one row per competitor per race, with races told apart by `RaceNo`. See [how to import a list of race results](https://www.sailwave.com/how-do-i-import-a-list-of-race-results) and the [field names](https://www.sailwave.com/field-names) reference.

### Columns

```text
RaceNo, RaceName, PublishedRaceDate, PublishedRaceTime, HelmName, Boat, SailNo,
Class, Fleet, Club, Rating, Start, Finish, Elapsed, Code
```

Every one of those is a name Sailwave recognises, so an import needs no column mapping.

- **`Class`** is the boat class or the rating band the race was scored in; **`Fleet`** carries the rating system (IRC or YTC).
- **`HelmName`** comes from the boat's *owner* field, which is the nearest thing the app records. Blank if there is none.
- **`Code`** is blank for a finisher and carries `DNF`, `DNS`, `DNC`, `RET`, `OCS` or `DSQ` otherwise — the app's entry statuses are already the same words.
- **`Place` is deliberately not exported.** Sailwave scores from elapsed time and rating; sending a finishing order as well would import this app's arithmetic and then ask Sailwave to redo it, with nothing to say which wins if they ever disagreed.
- `Elapsed` *is* sent alongside `Start` and `Finish`, because a race here can have per-class start times, so the elapsed time is the app's own rather than always end-minus-start of the fleet.

### One file per rating system

A Sailwave series is scored under one rating system, and a competitor carries one `Rating` — but this app produces IRC *and* YTC results from the same finish times. So each page offers **Sailwave CSV (IRC)** and **Sailwave CSV (YTC)**, and the ratings are formatted the way each system is written (IRC TCC to three decimals, YTC as a whole number). Mixing them in one file would silently rescore a fleet.

Boats the app excludes from a system's results — no IRC certificate when scoring IRC, for instance — are **not** in that file. They are genuinely not part of that result; the race page and the HTML publish list them as excluded.

### Race numbering

A race is numbered by its position in its series, so importing week by week through a season lands each race in its own Sailwave race rather than overwriting race 1 every time. A race in no series is race 1.

The series export contains every race in the series in one file, numbered in order. It does **not** export the standings: Sailwave works out its own totals and discards from the race results, which is the reason for importing them.

### Importing

1. Open the race or series page and download the CSV for the rating system you score.
2. In Sailwave, **File → Import → Competitors and results from a CSV file** (see the Sailwave documentation for the wizard).
3. The header row means no column mapping should be needed.
4. Check the scoring system and discard profile in Sailwave: this app's settings are not carried in the file.

---
Copyright © 2026 CapeNet Ltd. All Rights Reserved.
