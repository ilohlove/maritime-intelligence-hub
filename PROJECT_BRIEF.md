# PROJECT_BRIEF.md

## Project Name

Template Smoke Test

## Project Slug

Template-Smoke-Test

## Purpose

Desktop smoke-test app created from the template to verify GUI startup, logging, version metadata, update metadata, and release workflow readiness.

## Main Features

- Simple CustomTkinter desktop shell.
- App name and current version display.
- Check Update action wired to remote raw `latest.json` metadata.
- Automatic update check shortly after the GUI opens.
- Update confirmation dialog with version and short changelog summary.
- Short on-screen log area.

## GUI Requirements

- Main screen.
- App name display.
- Version display.
- Check Update button.
- Update confirmation dialog.
- Short log area.

## Business Rules

- Keep app-specific logic in `app/services/business_logic.py`.
- Keep framework files stable unless a real project needs a framework change.
- Never release while required metadata is incomplete.

## Input Data

No business input is required for this smoke test.

## Output Data

Short UI log messages and standard application log file entries.

## Project Notes

Use this file as the main project-specific brief. Keep it short and update it before changing business logic.
