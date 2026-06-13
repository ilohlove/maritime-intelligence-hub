# NEWS_SCORING_SYSTEM.md

## Purpose

Reduce AI cost and improve editorial consistency by scoring articles before summary generation.

## Score Range

Use a 1-10 importance score.

## Base Signals

- Source priority.
- Source quality score.
- Business value score.
- Category.
- Vietnam relevance.
- Recency.
- Safety/regulation impact.
- Market disruption.
- Port/logistics impact.
- Duplicate or syndicated content.

## Suggested Rule

Start with source quality and business value, then adjust:

- P1 source: increase confidence.
- Safety/regulation/accident: increase importance.
- Vietnam or regional relevance: increase importance.
- Carrier/port disruption: increase importance.
- Duplicate item: suppress.
- Low confidence extraction: hold for review.

## AI Cost Rule

Only send articles to AI when:

- the article is not a duplicate;
- the source is active;
- the article meets a minimum score threshold;
- the source permits AI summary;
- legal notes do not block processing.

## MVP Threshold

Use threshold `6` for AI summary candidates and `8` for brief lead candidates.
