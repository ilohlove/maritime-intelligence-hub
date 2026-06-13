# SOURCE_MASTER_SCHEMA.md

## Current Required Columns

- `ID`
- `Source Name`
- `Website`
- `Country`
- `Language`
- `Type`
- `Category`
- `Priority`
- `RSS`
- `API`
- `Crawl Method`
- `Frequency`
- `Audience`
- `Content Quality Score`
- `Business Value Score`
- `Crawl Difficulty`
- `Copyright Risk`
- `AI Summary Enabled`
- `Status`

## Recommended Columns To Add

- `RSS URL`
- `Robots Allowed`
- `Terms Notes`
- `Paywall`
- `Fetch Enabled`
- `Parser Strategy`
- `Title Selector`
- `Date Selector`
- `Article Selector`
- `Excerpt Selector`
- `Last Checked At`
- `Reliability Score`
- `Vietnam Relevance Score`
- `Dedup Strategy`
- `Legal Notes`
- `Owner Notes`

## Allowed Values

- `Priority`: `P1`, `P2`, `P3`
- `RSS`: `Yes`, `No`, `Partial`, `Unknown`
- `API`: `Yes`, `No`, `Partial`, `Unknown`
- `Frequency`: `Hourly`, `Daily`, `Weekly`, `Manual`
- `Crawl Difficulty`: `Easy`, `Medium`, `Hard`
- `Copyright Risk`: `Low`, `Medium`, `High`
- `AI Summary Enabled`: `Yes`, `No`
- `Status`: `Active`, `Future`, `Disabled`

## Validation Rules

- `ID`, `Source Name`, and `Website` must be unique.
- Scores must be numeric from 1 to 10.
- Active RSS sources should eventually include `RSS URL`.
- Active HTML sources should eventually include parser strategy notes.
- Medium or high copyright risk sources require legal notes before publishing.
