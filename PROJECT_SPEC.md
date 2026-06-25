# PROJECT_SPEC.md

## Purpose

Professional Python desktop app template for many local projects.

The template supports local development, PyInstaller `.exe` builds, GitHub source publishing, GitHub Releases, release asset upload, `latest.json` updates, and auto-update delivery for end users.

## Rule Priority

`PROJECT_SPEC.md` is the highest rule file.

Required rule files:

- `AGENTS.md`
- `PROJECT_SPEC.md`
- `PROJECT_TEMPLATE_CONFIG.json`
- `PROJECT_BRIEF.md`
- `FILE_MAP.md`
- `RELEASE_WORKFLOW.md`

## Release Model

Codex may use GitHub commands only when the user asks to initialize a real project or says:

`Hoan thanh phien ban moi`

Before every commit, push, tag, or release, Codex must run the security checks in `RELEASE_WORKFLOW.md`.

When creating the first real project release, Codex must:

1. Confirm project metadata has no placeholders.
2. Run `git init` if needed.
3. Verify `.gitignore`.
4. Scan for secrets.
5. Commit source code.
6. Create or verify the GitHub repository with `gh`.
7. Push source code.
8. Run `build.bat first`.
9. Create GitHub Release `v1.0.0`.
10. Upload `dist/BV-{ProjectSlug}.exe` and `dist/BV-Updater.exe`.
11. Update and push `latest.json`.
12. Verify the raw `latest.json` URL.

When the user says `Hoan thanh phien ban moi`, Codex must:

1. Review changes.
2. Increment version using the project version rollover rule.
3. Update `version.json`.
4. Update `CHANGELOG.md`.
5. Update `latest.json`.
6. Run `build.bat release`.
7. Self-test.
8. Scan for secrets.
9. Commit source.
10. Push source.
11. Create tag.
12. Create GitHub Release.
13. Upload only `dist/BV-{ProjectSlug}.exe`.
14. Upload `dist/BV-Updater.exe` only if `updater/updater.py` changed.
15. Get the release asset `download_url`.
16. Update, commit, and push `latest.json`.
17. Verify raw `latest.json`.
18. Report results.

Version rollover rule:

- Increment the patch version by 1 until patch `15`.
- After `x.y.15`, the next version is `x.(y+1).0`.
- Example: `1.0.14` -> `1.0.15` -> `1.1.0` -> `1.1.1`.

Codex must not:

- Commit, push, tag, create releases, or upload assets if suspected secrets are found.
- Release while metadata placeholders remain.
- Upload `BV-Updater.exe` during normal update releases unless the updater changed.

## Project Files

- `version.json`
- `latest.json`
- `latest.example.json`
- `CHANGELOG.md`
- `README.md`
- `.env.example`
- `.gitignore`
- `requirements.txt`
- `build.bat`
- `app/`
- `updater/`
- `logs/.gitkeep`
- `backup/.gitkeep`
- `temp/.gitkeep`
- `assets/.gitkeep`

## Required App Structure

- `app/__init__.py`
- `app/main.py`
- `app/gui.py`
- `app/config.py`
- `app/logger.py`
- `app/crash_handler.py`
- `app/error_handler.py`
- `app/version_checker.py`
- `app/update_manager.py`
- `app/updater_launcher.py`
- `app/services/__init__.py`
- `app/services/business_logic.py`
- `updater/__init__.py`
- `updater/updater.py`

## Metadata

`version.json` must contain:

- `company`
- `project_name`
- `project_slug`
- `app_name`
- `exe_name`
- `updater_name`
- `latest_json_url`
- `version`
- `release_date`

`latest.json` must contain:

- `version`
- `release_date`
- `download_url`
- `changelog`
- `force_update`

## Placeholders

Replace these when creating a real project:

- `__PROJECT_NAME__`
- `__PROJECT_SLUG__`
- `__RELEASE_DATE__`

Do not release while placeholders remain.

When creating a real project, also configure `latest_json_url` in `version.json` to the raw GitHub URL for that project's `latest.json`.

## Build Rules

`build.bat` must use `python` from PATH and must not depend on a hard-coded Python path or the `py` launcher.

`build.bat` must support:

- `build.bat first`: builds `BV-{ProjectSlug}.exe` and `BV-Updater.exe`.
- `build.bat release`: builds only `BV-{ProjectSlug}.exe`.

Before building, it must verify that `python`, `tkinter`, and `PyInstaller` are available. If any check fails, stop the build.

PyInstaller app builds must include:

- `--onefile`
- `--windowed`
- `--add-data "version.json;."`
- `--add-data "latest.json;."`
- `--collect-all customtkinter`
- `--hidden-import tkinter`
- `--hidden-import tkinter.ttk`

If `assets/icon.ico` exists, `build.bat` must include it.

`build.bat` must read names from `version.json` when possible, or use clear fallback variables at the top.

The built app exe must be able to read `version.json` and `latest.json` from bundled resources.

## Update Rules

The app must check updates against the remote raw `latest.json` URL from `version.json` field `latest_json_url`. It must not decide update availability from the bundled local `latest.json`, because bundled metadata becomes stale after release.

The app must automatically check for updates shortly after the GUI opens. If a newer version is available, show a confirmation dialog with the new version and a short changelog summary before downloading or launching the updater. The manual Check Update action must use the same dialog flow.

If remote `latest.json` has no `download_url`, the app must skip the update, avoid crashing, and write a warning log.

The updater must:

- Wait for the main app to exit.
- Backup the old app.
- Replace it with the new app.
- Roll back if replacement fails.
- Keep only the latest backup.

## Security Rules

`.gitignore` must block:

- `.env`
- `.env.*`
- logs except `logs/.gitkeep`
- backup except `backup/.gitkeep`
- temp except `temp/.gitkeep`
- `dist/`
- `build/`
- `*.spec`
- `__pycache__/`
- `*.pyc`
- `.vscode/`
- `.idea/`

Before every push, scan for suspicious keywords:

- `password`
- `passwd`
- `secret`
- `token`
- `api_key`
- `apikey`
- `access_key`
- `private_key`
- `client_secret`
- `authorization`
- `bearer`

If suspicious content is found, stop and report it.
