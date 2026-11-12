# macOS beta

Native packages are built separately for **Apple Silicon (arm64)** and **Intel (x64)**. The packages include Python and do not require a Python installation.

## Install

1. Download the matching archive from [v1.0.0-beta.2](https://github.com/AlexSmart1982/attentive-otter/releases/tag/v1.0.0-beta.2).
2. Extract the archive using Archive Utility. Move `Attentive_Otter.app` to Applications.
3. Open the application, add image folders and start a comparison. The interface is in Russian.

These builds require **macOS 14 or newer** and are tested on GitHub-hosted **macOS 15** machines. They use ad-hoc signatures, without an Apple Developer ID or notarization. If Gatekeeper blocks the downloaded app, first try opening it, then use **System Settings → Privacy & Security → Open Anyway**. Do not disable Gatekeeper globally.

## File operations

Discarded files go to the native macOS Trash through `NSFileManager`, or to the selected removal folder. Trash failures are reported; there is no permanent-delete fallback. Moves and replacements check file hashes and destination collisions. A saved transaction journal and verified backups support recovery after an interrupted operation.

macOS file locks are advisory. Close other applications that modify the same files while applying a plan. Unlike Windows sharing modes, these locks cannot prevent an uncooperative program from writing or renaming files. Identity and content checks detect changes at the checked boundaries; arbitrary concurrent modification is not guaranteed safe.

Saved comparisons contain absolute paths. A comparison saved against Windows paths cannot locate those photos on a Mac automatically; start a new comparison for the Mac archive. Existing Windows downloads and their matching source remain in [beta.1](https://github.com/AlexSmart1982/attentive-otter/releases/tag/v1.0.0-beta.1).

Settings and the image cache use the macOS application data location selected by Qt (`~/Library/Application Support/ComparsionV2`). Verification uses disposable files and a separate state directory.

## Build from source

Use native CPython **3.13.8** on the target architecture:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos.txt
.venv/bin/python -B desktop.pyw
QT_QPA_PLATFORM=offscreen .venv/bin/python -B -m unittest discover -v
.venv/bin/python -B build_macos_opencv.py
.venv/bin/python -B build_macos.py --output /tmp/Attentive_Otter_release
```

The distribution builds OpenCV 4.12.0 from the pinned opencv-python source with the required image modules. The recipe omits video backends and optional typing metadata; native image operations remain upstream OpenCV code. Tests run against this dependency before packaging.

The builder creates a `.app`, runs the deployment check against it, a relocated copy and the extracted distribution ZIP, and verifies the ad-hoc code signatures. Distribution archives preserve symlinks and executable permissions using `ditto`. The GitHub workflow runs every test module in a separate process and rejects any failed or interrupted test.

The dedicated Corresponding Source archives include the application snapshot, upstream source archives, licenses, dependency versions and build instructions. GitHub's automatic source ZIP is not the complete dependency-source bundle. The binary and source for each architecture must be distributed together or made available at the same download location under the applicable licenses.

The paired application/source packager uses a Git checkout to identify and archive the source commit. To rebuild only the application from an extracted Corresponding Source ZIP, install `requirements-macos.txt`, run `build_macos_opencv.py`, then run `python -m PyInstaller --noconfirm Attentive_Otter_macOS.spec`. The result is `dist/Attentive_Otter.app`.

## Published verification

[macOS build and tests](https://github.com/AlexSmart1982/attentive-otter/actions/runs/43183695772) passed on both architectures at commit `15ceb719c8d2468ebc1e5462542ad34275a37e36`, tagged `v1.0.0-beta.2`. Each architecture ran 180 cases: 178 passed and two Windows-only cases were skipped. [Windows regression](https://github.com/AlexSmart1982/attentive-otter/actions/runs/43183696579) passed at the same commit.

[Verification records](verification/macos) and the macOS [dependency-source inventories](../third_party/macos) accompany the published archives. The GitHub-calculated archive digests match the local checksums, and anonymous downloads were checked after publication.
