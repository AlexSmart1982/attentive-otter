# Release provenance

## macOS beta.2

The native macOS packages are built from the Git commit recorded in each `build-info.json`, separately on macOS 15 arm64 and x86_64 runners. They require macOS 14+. Each architecture has its own binary and Corresponding Source archive, dependency-source inventory, exact package versions and SHA256 manifest.

The full regression suite runs in isolated module processes with failure propagation. `build_macos.py` tests the frozen executable with synthetic JPEG/PNG/HEIC fixtures, native Trash, selected-folder removal, moves/replacements, save/reload and recovery. It repeats deployment checks from a relocated path and from the extracted distribution ZIP. Ad-hoc signatures are checked with `codesign --verify --deep --strict`.

`build_macos_opencv.py` builds upstream OpenCV 4.12.0 image modules from the opencv-python 4.12.0.88 sdist, omitting video backends and optional typing metadata. The recipe, source archive and native wheel hash are included. Qt 5.15.17 and PyObjC notices/sources are included in the macOS source packages. The existing Windows inventory below applies to beta.1.

macOS file locks are advisory; see [platform limitations](MACOS.md). These checks do not establish safety under arbitrary concurrent changes, power loss or every filesystem. The builds have no Apple Developer ID or notarization.

## Windows beta.1

Public tag: **v1.0.0-beta.1**. Application version: **1.0 BETA**. This release publishes the already verified Windows x64 portable build.

| Item | Value |
| --- | --- |
| Application build time | 2026-11-07 22:04:35 UTC / 2026-11-08 01:04:35 Moscow |
| Python | CPython 3.13.8 x64 |
| Packager | PyInstaller 6.16.0 |
| Local automated suite | 174 tests passed |
| EXE SHA256 | `3e56e706ba7f8332b7bc566aaa95c1487179b516a3bf15ddb4bcf5d5abc1793b` |

The 78 application inputs at tag beta.1 match the hashes recorded in [build-info.json](../build-info.json). A fresh repository was seeded from the validated corresponding-source snapshot; development history and internal project memory are not copied into this repository. Later commits add macOS support; the root build-info remains the historical Windows build record.

At this tag, run `python .github/scripts/verify_release_snapshot.py` to check the recorded application inputs. The ordinary CI workflow runs the tests so that later development changes can be tested before a new release snapshot is produced.

`git_base_commit` in the build record refers to the earlier development history. The original build included working-copy changes, so that base commit alone does not describe the release. The recorded per-file hashes identify the actual inputs. New documentation, demonstration assets and CI files accompany the snapshot without changing the released application code.

The full **Corresponding Source ZIP** also carries the dependency-source archives, upstream notices and the original source-package manifest. The Windows ZIP carries the application-folder manifest. An additional `RELEASE_INFORMATION.txt` at each ZIP's top level identifies the public download and corresponding-source location.

## Verification

The local build checks covered PNG/JPEG/HEIC, the approved wordmark and licensing display, review controls, saved state and visible save feedback, real operations on disposable temporary files, collision handling and recovery. The EXE was also checked from a relocated Cyrillic path with a reduced `PATH` and without a console window.

Original reports are in [verification](verification). Personal photographs and saved comparisons are not part of the release packages. The demonstration screenshot was captured from the application with synthetic images; its source illustration is [demo_landscape.svg](images/demo_landscape.svg).

The package is unsigned. Byte-for-byte EXE reproducibility is not claimed. macOS is planned and is not included in this release.
