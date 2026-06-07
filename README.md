# Template Smoke Test

Desktop smoke-test app created from the Python desktop template. It verifies that the generated project can open a CustomTkinter GUI, read version metadata, write logs, expose update metadata, and stay ready for the documented release workflow.

## App Metadata

- Project name: Template Smoke Test
- Project slug: Template-Smoke-Test
- App name: BV-Template-Smoke-Test
- Exe name: BV-Template-Smoke-Test.exe
- latest.json URL: https://raw.githubusercontent.com/ilohlove/BV-Template-Smoke-Test/main/latest.json

## Features

- Opens a simple CustomTkinter GUI.
- Displays app name and current version from `version.json`.
- Provides a Check Update button using the remote raw `latest.json` URL from `version.json`.
- Shows short log messages in the UI and writes through the template logger.

## Run From Source

```bat
python -m app.main
```

## Build

First release builds the app and updater:

```bat
build.bat first
```

Update releases build only the app:

```bat
build.bat release
```

## Release

Before any release, read `RELEASE_WORKFLOW.md`.

When ready, ask Codex:

```text
Hoan thanh phien ban moi
```

Codex will review changes, bump metadata, build, self-test, scan for secrets, commit, push, create the tag and GitHub Release, upload the executable, update `latest.json`, and verify the raw `latest.json` URL.

## Security

Do not commit `.env`, credentials, tokens, logs, backups, temporary files, `build/`, `dist/`, or PyInstaller specs. The required ignore rules are in `.gitignore`.
