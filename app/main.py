from app.crash_handler import install_crash_handler
from app.cli import run_cli
from app.gui import AppGUI
from app.logger import logger


def main(argv=None):
    install_crash_handler()
    argv = list(argv or [])

    if "--gui" in argv or not argv:
        if "--gui" in argv:
            argv.remove("--gui")
        logger.info("GUI application starting")
        app = AppGUI()
        app.run()
        return 0

    logger.info("CLI application starting")
    return run_cli(argv)


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
