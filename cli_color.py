import importlib
import logging
from .sys_info import SysInfo as SYS

# -------------------------------------------------
# ANSI COLOR SUPPORT
# -------------------------------------------------

LOG_MODULE = "[StemSeparator] "

class CliColor:
    USE_COLOR = SYS.supports_color()
    RESET = "\033[0m" if USE_COLOR else ""
    BOLD = "\033[1m" if USE_COLOR else ""
    CYAN = "\033[96m" if USE_COLOR else ""
    GREEN = "\033[92m" if USE_COLOR else ""
    YELLOW = "\033[93m" if USE_COLOR else ""
    RED = "\033[91m" if USE_COLOR else ""
    GRAY = "\033[90m" if USE_COLOR else ""

    @staticmethod
    def banner_line():
        print(LOG_MODULE + CliColor.GRAY + "=" * 64 + CliColor.RESET)

    @staticmethod
    def log_banner_line():
        logging.info(LOG_MODULE + "=" * 64)

    @staticmethod
    def print(msg):
        print(LOG_MODULE + msg)


    @staticmethod
    def info(label, value):
        print(f"{LOG_MODULE}{CliColor.CYAN}{label:<10}{CliColor.RESET}: {value}")

    @staticmethod
    def ok(msg):
        print(LOG_MODULE + CliColor.GREEN + msg + CliColor.RESET)

    @staticmethod
    def warn(msg):
        print(LOG_MODULE + CliColor.YELLOW + msg + CliColor.RESET)

    @staticmethod
    def error(msg):
        print(LOG_MODULE + CliColor.RED + msg + CliColor.RESET)

    @staticmethod
    def exception(msg):
        print(LOG_MODULE + CliColor.RED + msg + CliColor.RESET)
        logging.exception(msg)
    
    @staticmethod
    def log_info(label, msg):
        logging.info(f"{LOG_MODULE}{label}: {msg}")

    @staticmethod
    def log_print( msg):        
        logging.info(LOG_MODULE + msg)

    @staticmethod
    def log_ok(msg):
        logging.info(LOG_MODULE + msg)

    @staticmethod
    def log_warn(msg):
        logging.warning(LOG_MODULE + msg)

    @staticmethod
    def log_error(msg):
        logging.error(LOG_MODULE + msg)

    @staticmethod
    def log_exception(msg):
        logging.exception(LOG_MODULE + msg)

    

    