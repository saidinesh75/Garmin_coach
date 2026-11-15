# Garmin Coach development

This repository is the canonical source for Garmin Coach. Implement future changes here; do not generate one-off update installers for normal development.

- Keep API keys, tokens, health readings, local path configuration, model files and Python environments out of Git and source archives.
- The ignored `.coach-local.json` selects the existing runtime and private data directory. Preserve these locations unless the user asks to migrate them.
- Use `python3 scripts/develop.py -m pytest -q` for synthetic tests. Do not use real Garmin data or credentials as fixtures.
- Keep AI inference local with no paid/cloud fallback. Generated interpretations must remain distinguishable from measured evidence.
- Do not commit incidental user changes. Stage explicit source files, review the diff, and make a local commit for a completed change when requested or part of this development workflow. Do not push or publish without authorization.
- Keep existing production files and patch backups intact until the development launcher is verified. Do not delete legacy files by filename alone.
