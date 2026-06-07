# RELEASE_WORKFLOW.md

## Before Any Release

1. Review changes with `git status` and `git diff`.
2. Confirm `version.json`, `latest.json`, and `CHANGELOG.md` versions match.
3. Confirm `version.json` has no `__PROJECT_NAME__`, `__PROJECT_SLUG__`, or `__RELEASE_DATE__` placeholders.
4. Confirm `latest.json` has a non-empty `download_url` before publishing it for end users.
5. Run the security check below.

## Security Check

Run before every commit, push, tag, or release:

```bat
git status
git diff --cached
git ls-files
```

Scan tracked and staged content for:

```text
password
passwd
secret
token
api_key
apikey
access_key
private_key
client_secret
authorization
bearer
```

If any suspicious value is found, stop and report it. Do not push.

## First Release

1. Run `git init` if the project is not already a repository.
2. Verify `.gitignore` blocks `.env`, logs, backup, temp, build, dist, specs, caches, and IDE files.
3. Run the security check.
4. Commit source code.
5. Create or verify the GitHub repo with `gh repo create`.
6. Push source code.
7. Run `build.bat first`.
8. Verify:
   - `dist/BV-{ProjectSlug}.exe`
   - `dist/BV-Updater.exe`
9. Create Release `v1.0.0`.
10. Upload both executables.
11. Update `latest.json` with the app executable release asset URL.
12. Commit and push `latest.json`.
13. Verify the raw `latest.json` URL.

## Update Release

When the user says `Hoan thanh phien ban moi`:

1. Review changes.
2. Increment the semantic version.
3. Update `version.json`.
4. Update `CHANGELOG.md`.
5. Update `latest.json`.
6. Run `build.bat release`.
7. Verify `dist/BV-{ProjectSlug}.exe`.
8. Run the security check.
9. Commit and push source.
10. Create and push the tag.
11. Create the GitHub Release.
12. Upload `dist/BV-{ProjectSlug}.exe`.
13. Upload `dist/BV-Updater.exe` only if `updater/updater.py` changed.
14. Update `latest.json` with the new release asset URL.
15. Commit and push `latest.json`.
16. Verify the raw `latest.json` URL.

## Error Classes

- Template error: invalid template structure, metadata, docs, or placeholders.
- Build error: Python, tkinter, PyInstaller, dependency, or executable output failure.
- GitHub error: auth, repo, push, tag, release, upload, or raw URL failure.
- Update error: `latest.json`, download URL, updater, backup, replacement, or rollback failure.
- Security error: ignored files, tracked secrets, or suspicious credentials.
