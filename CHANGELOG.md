# Changelog

## 1.0.1 - 2026-11-24

- Require a higher version for every changed pull request and reject same-version content changes during installation.
- Record the source Git commit in each installed copy and show it with `--status`.

## 1.0.0 - 2026-11-20

- Introduce an explicit skill version and release process.
- Add a self-contained installer that replaces the previous installation so removed files do not persist.
- Add an update command that retrieves the latest release from the repository's default branch.

Earlier releases were unversioned. The installer treats an existing unversioned `wzmh-code-skill` as a legacy installation.
