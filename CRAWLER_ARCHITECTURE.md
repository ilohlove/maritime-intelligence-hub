# CRAWLER_ARCHITECTURE.md

## Purpose

Define a simple, maintainable source collection architecture.

## Collection Strategy

1. RSS collector for sources with RSS.
2. HTML collector for approved non-RSS sources.
3. Scrapy only for sources that need crawl structure.
4. Playwright only when JavaScript rendering is unavoidable.

## MVP Source Order

Start with active P1 sources.

RSS-ready P1 sources:

- Safety4Sea.
- Splash247.
- Maritime Executive.
- gCaptain.
- Marine Insight.
- Hai Quan Online.

Partial RSS P1 sources:

- BIMCO.
- Seatrade Maritime.

HTML P1 sources:

- IMO.
- ICS.
- Maersk News.
- MSC Newsroom.
- CMA CGM News.
- Hapag-Lloyd News.
- Vinamarine.
- Vietnam Register.
- Saigon Newport.
- VIMC.

## Reliability Rules

- One failed source must not stop the full run.
- Log source ID, URL, status code, error message, and retry count.
- Store fetch time.
- Respect configured frequency.
- Respect disabled/future status.
- Respect legal notes and robots decisions before publishing.
