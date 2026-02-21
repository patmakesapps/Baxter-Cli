# Baxter CLI Release Checklist

Use this checklist for every release to keep publishing and regression checks consistent.

## Pre-Release

1. Ensure branch is clean enough for release work:
   - `git status`
2. Bump version in `pyproject.toml`.
3. Verify docs reflect current behavior:
   - `README.md`
   - this `RELEASE.md`

## Build Artifacts

1. Activate repo venv.
2. Install/upgrade release tooling:
   - `python -m pip install --upgrade build twine`
3. Build:
   - `python -m build`
4. Validate artifacts:
   - `python -m twine check dist/*`

## Install Smoke Test

1. Create a fresh folder + fresh venv.
2. Install from local wheel:
   - `pip install C:\Baxter\Baxter-Cli\dist\baxter_cli-<version>-py3-none-any.whl`
3. Run Baxter from that folder:
   - `baxter`
4. Verify user-level key loading:
   - `%USERPROFILE%\.baxter\.env` on Windows
   - `~/.baxter/.env` on macOS/Linux

## Publish

1. Upload to TestPyPI first:
   - `python -m twine upload --repository testpypi dist/*`
2. Install from TestPyPI in a fresh env and smoke test.
3. Publish to PyPI:
   - `python -m twine upload dist/*`
4. Verify public install:
   - `pip install -U baxter-cli`

## Post-Release

1. Commit release metadata/docs if needed.
2. Tag release:
   - `git tag v<version>`
   - `git push origin v<version>`
3. Add a short changelog note for what changed in this version.
