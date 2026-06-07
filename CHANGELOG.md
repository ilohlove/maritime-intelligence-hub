# Changelog

## v1.0.3 - 2026-06-07

- Added automatic update checks shortly after the GUI opens.
- Added update confirmation with version and short changelog summary.
- Added app executable download and updater launch flow after user confirmation.

## v1.0.2 - 2026-06-07

- Fixed update checks to read the remote raw `latest.json` URL from `version.json`.
- Added clearer update status handling for current, available, invalid, and failed checks.
- Updated template release rules to prevent bundled `latest.json` from being used as the update source.

## v1.0.1 - 2026-06-07

- Ran the template update-release workflow for smoke testing.

## v1.0.0 - 2026-06-07

- Created Template Smoke Test from the desktop template.
- Added simple GUI metadata display and update-check action.
