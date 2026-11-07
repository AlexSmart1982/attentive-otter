# Contributing

Please open an issue describing a bug or proposed change before a substantial contribution. Include the application version, operating system and steps to reproduce. Use synthetic samples or photographs you have permission to share.

See [build and test instructions](docs/BUILDING.md). Current automated tests target Windows; macOS is planned.

File actions must remain separate from review decisions. Preserve preflight checks, source identity/hash validation, exclusion between project writes, collision handling and transaction recovery. Exercise file changes only on disposable temporary fixtures, and add regression coverage for a bug affecting saved state or file integrity.

Keep platform support claims consistent with actual builds and checks. Changes to application code are distributed under the project's GPL-3.0-only terms; preserve upstream notices for dependencies.
