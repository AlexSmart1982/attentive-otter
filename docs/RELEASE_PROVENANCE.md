# Windows release provenance

Public tag: **v1.0.0-beta.1**. Application version: **1.0 BETA**. This release publishes the already verified Windows x64 portable build.

| Item | Value |
| --- | --- |
| Application build time | 2026-11-07 22:04:35 UTC / 2026-11-08 01:04:35 Moscow |
| Python | CPython 3.13.8 x64 |
| Packager | PyInstaller 6.16.0 |
| Local automated suite | 174 tests passed |
| EXE SHA256 | `3e56e706ba7f8332b7bc566aaa95c1487179b516a3bf15ddb4bcf5d5abc1793b` |

The 78 application inputs in this repository match the hashes recorded in [build-info.json](../build-info.json). A fresh repository was seeded from the validated corresponding-source snapshot; development history and internal project memory are not copied into this repository.

At this tag, run `python .github/scripts/verify_release_snapshot.py` to check the recorded application inputs. The ordinary CI workflow runs the tests so that later development changes can be tested before a new release snapshot is produced.

`git_base_commit` in the build record refers to the earlier development history. The original build included working-copy changes, so that base commit alone does not describe the release. The recorded per-file hashes identify the actual inputs. New documentation, demonstration assets and CI files accompany the snapshot without changing the released application code.

The full **Corresponding Source ZIP** also carries the dependency-source archives, upstream notices and the original source-package manifest. The Windows ZIP carries the application-folder manifest. An additional `RELEASE_INFORMATION.txt` at each ZIP's top level identifies the public download and corresponding-source location.

## Verification

The local build checks covered PNG/JPEG/HEIC, the approved wordmark and licensing display, review controls, saved state and visible save feedback, real operations on disposable temporary files, collision handling and recovery. The EXE was also checked from a relocated Cyrillic path with a reduced `PATH` and without a console window.

Original reports are in [verification](verification). Personal photographs and saved comparisons are not part of the release packages. The demonstration screenshot was captured from the application with synthetic images; its source illustration is [demo_landscape.svg](images/demo_landscape.svg).

The package is unsigned. Byte-for-byte EXE reproducibility is not claimed. macOS is planned and is not included in this release.
