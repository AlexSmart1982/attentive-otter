# Changelog

## 1.0 BETA 2 — 2026-11-12

Native macOS application bundles for Apple Silicon and Intel; macOS 14+.

- Native macOS Trash, exclusive moves, metadata-preserving copies and journal recovery.
- Platform-specific Trash/date labels and macOS startup error reporting.
- Native OpenCV image-processing build with pinned upstream sources and a published recipe.
- Full tests in isolated processes, real filesystem tests on both architectures, and verification of the app, relocated app and extracted distribution ZIP.
- Matching source archives, dependency sources, notices and checksums for each architecture.
- Ad-hoc signatures; Apple Developer ID signing and notarization are not provided.

Windows beta.1 and its matching source remain available unchanged.

## 1.0 BETA 1 — 2026-11-08

First public Windows x64 beta release, tag `v1.0.0-beta.1`.

- Content-based grouping of JPEG, PNG and HEIC images with rotation handling and cached features.
- Side-by-side review, EXIF, configurable recommendations and explanations.
- Three actions in one row per card, with keep buttons toward the center and explanations beside paths.
- Undo/redo, saved comparisons and visible save progress/confirmation.
- Removal to the Windows Recycle Bin or a selected folder, moves, replacements and collision handling.
- Source identity/hash checks, project-write exclusion and transaction recovery.
- Removed the per-photo path-resolution walk from ordinary saving and closing; strict application checks remain.
- 174 automated tests and verified portable Windows packages with corresponding dependency sources and notices.

macOS is planned and is not part of this release.
