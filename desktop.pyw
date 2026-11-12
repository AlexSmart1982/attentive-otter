"""Console-free entry point with a visible report if startup fails."""

if __name__ == "__main__":
    try:
        from multiprocessing import freeze_support
        freeze_support()
        import sys
        if len(sys.argv) == 3 and sys.argv[1] == "--verify-deployment":
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from deployment_check import run
            sys.exit(run(sys.argv[2]))
        from app_ui import main
        main()
    except Exception:
        import ctypes
        from datetime import datetime
        import os
        from pathlib import Path
        import traceback

        detail = traceback.format_exc()
        try:
            folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ComparsionV2" / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            filename = folder / f"startup-{datetime.now():%Y%m%d-%H%M%S}.log"
            filename.write_text(detail, encoding="utf-8")
            detail = f"Подробности ошибки: {filename}\n\n{detail}"
        except OSError:
            pass
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, detail, "Attentive Otter — ошибка запуска", 0x10)
        else:
            import sys
            print(detail, file=sys.stderr)
            try:
                from PyQt5.QtWidgets import QApplication, QMessageBox
                app = QApplication.instance() or QApplication([])
                QMessageBox.critical(None, "Attentive Otter — ошибка запуска", detail)
            except Exception:
                pass
