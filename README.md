# Garmin Coach — development home

A software which collects data from my garmin watch and gives insights and AI based recommendations.

This Git repository is the authoritative source for future development. It includes the garminAI section using local Qwen3 1.7B through Ollama. Version: **0.3.0**.

## Start the app

Stop the older Garmin Coach server with Control+C in its Terminal window. Then double-click **Garmin_Coach.command** in this folder. Keep its Terminal window open while using the app. You can create a Finder alias to this launcher on your Desktop; do not copy the script by itself.

The launcher runs the source in this repository. It will refuse to reuse an older installation on the same port and explain how to stop it. The dashboard footer shows the application version. For future changes, edit this project, check the result, commit it, and restart the server when Python code changes. Refresh the browser for frontend changes. No patch ZIP installation is required.

## Source and private data

The ignored, owner-only `.coach-local.json` contains two paths: `python` and `data_dir`. It contains no API key or readings. The current setup reuses:

- Python: `~/Documents/Garmin_coach/.venv/bin/python`
- Private data: `~/Documents/Garmin_coach/data`

Your readings, Garmin key, token and model selection stay at their existing location. No database, key, model or Python environment was copied into this repository. **Do not remove or rename the original Documents/Garmin_coach folder:** this development setup depends on its `data` and `.venv` folders. Its older application code is retained as a fallback for now.

`Settings.load()` uses `COACH_DATA_DIR` when explicitly set, otherwise the configured private data path, otherwise project `data/` for a fresh standalone installation. The permanent launcher pins `COACH_DATA_DIR` to the configured path to avoid accidentally opening a different database. Existing key and model environment overrides still apply.

For another computer, create a private `.coach-local.json` with absolute paths:

```json
{"python": "/absolute/path/to/venv/bin/python", "data_dir": "/absolute/path/to/private/data"}
```

Create the environment with Python 3.9+ and install `requirements.txt` first. Do not commit this local configuration.

## Development workflow

From this directory:

```sh
# Inspect and test changes using the configured environment
git status
python3 scripts/develop.py -m pytest -q
node --check garmin_coach/static/app.js
node --check garmin_coach/static/coach.js

# Review and save a logical change; stage explicit source paths
git diff
git add garmin_coach tests scripts README.md
git commit -m "Describe the behavior change"
```

GitHub remote `origin` is <https://github.com/saidinesh75/Garmin_coach>. The repository is public; only source code and synthetic tests are tracked. Readings, credentials, local paths, environments and backup bundles remain excluded. Git tracks code, not live data backups.

After reviewing and committing a change, use `git push origin main` to synchronize it. Use `git pull --ff-only` to retrieve remote updates when the local branch has no divergent commits. Publishing future changes still requires user authorization.

To inspect an older version without affecting this one:

```sh
git log --oneline
git show v0.3.0:README.md
```

Prefer a reviewed `git revert` for a committed regression, rather than deleting project files or resetting uncommitted work. Software changes that alter the database schema need a separate migration and backup plan.

## Local AI

Ollama and `qwen3:1.7b` are already set up in the existing installation. This launcher uses that installation's saved model selection automatically. Keep Ollama running and open **garminAI** in the dashboard. Connection checking is automatic.

Briefings and weekly reviews are generated on demand and reuse cached results when inputs are unchanged. Independent questions do not retain conversation history. Python calculates comparisons; the model receives compact aggregate evidence and your current optional check-in, not Garmin credentials, GPS or raw API responses. Local generation uses a 4096-token context, at most 700 output tokens, thinking disabled and `keep_alive: 0`. There is no paid API fallback.

Current coaching comparisons require at least four recent observations and fourteen baseline observations per metric. Eight imported days cannot establish the earlier baseline; use historical backfill to retrieve more history. Wearable readings and model interpretations do not establish a diagnosis. Evidence-reference validation does not guarantee the truth of generated prose.

## Old downloads and backups

Nothing has been deleted or moved from your existing installation. The following are candidates for a separate archive after this development launcher has been verified:

| Existing item under Documents/Garmin_coach | Treatment |
| --- | --- |
| `data/` | Keep: live readings, credentials and settings |
| `.venv/` | Keep: the runtime used by this development project |
| `Garmin_Coach_Local_AI_Update/` | Extracted patch; archive after verification |
| `garminAI_Interface_Update/` | Extracted patch; archive after verification |
| `Garmin_Connection_Check.zip` | Diagnostic download; archive after verification |
| `Garmin_TLS_Repair/` | Repair package; archive after verification |
| `local-ai-update-backups/` | Source rollback copies; retain one known-working baseline before pruning |
| `.venv-before-tls-repair/` | Old environment; keep until the current runtime is verified |

Keep the previous launcher as a fallback until the new one works. After verification, keep only the new launcher visible on the Desktop and archive old patch downloads together. Never delete a folder merely because its name contains Garmin.

## Backup and scope

Stop the app before making a complete secure backup of the existing `data/` folder, including SQLite sidecar files when present. This folder includes secrets and health data; encrypt/protect that backup. Source checkpoints must exclude data, local configuration, environments and caches.

The app remains a loopback-only, single-user tool. See `docs/API.md` for Garmin endpoint mappings. Tests use synthetic data and mocked external services. Real model performance and historical Garmin availability require local verification.
