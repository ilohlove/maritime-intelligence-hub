# AI_PIPELINE_DESIGN.md

## Purpose

Define when AI is used and how output remains consistent, useful, and cost controlled.

## Pipeline

1. Receive deduplicated article metadata and cleaned text/excerpt.
2. Detect language.
3. Classify category.
4. Estimate maritime importance.
5. Generate Vietnamese summary.
6. Generate audience impact note.
7. Generate brief-ready item.
8. Save model, prompt version, token usage, and output.

## Cost Controls

- Deduplicate before AI calls.
- Do not send full content unless needed.
- Cache output by article hash.
- Use smaller models for classification and short summaries.
- Reserve stronger models for final brief editing if required.
- Batch non-urgent processing where possible.

## Output Requirements

Each AI-processed article should produce:

- Vietnamese headline suggestion.
- 2-4 sentence summary.
- category.
- importance score.
- audience relevance.
- source attribution.
- original URL.

## Prohibited Behavior

- Do not rewrite full articles.
- Do not fabricate facts not present in the source.
- Do not remove source attribution.
- Do not publish AI output without retaining the original URL.
