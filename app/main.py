from app.crash_handler import install_crash_handler
from app.gui import AppGUI
from app.logger import logger


def main():
    install_crash_handler()
    logger.info("Application starting")
    app = AppGUI()
    app.run()


if __name__ == "__main__":
    main()
