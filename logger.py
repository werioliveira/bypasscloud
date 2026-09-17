
import sys
import logging
from datetime import datetime

try:
    from colorama import Fore, Style, init as _colorama_init
    _colorama_init(autoreset=True)
    _HAS_COLORAMA = True
except Exception:  # pragma: no cover - colorama é opcional
    _HAS_COLORAMA = False

# ----------------------------------------------------------------------
# Cores / estilos (através do colorama, se disponível)
# ----------------------------------------------------------------------
if _HAS_COLORAMA:
    C_RESET   = Style.RESET_ALL
    C_BOLD    = Style.BRIGHT
    C_GREY    = Fore.LIGHTBLACK_EX
    C_GREEN   = Fore.GREEN
    C_YELLOW  = Fore.YELLOW
    C_RED     = Fore.RED
    C_BLUE    = Fore.BLUE
    C_CYAN    = Fore.CYAN
    C_MAGENTA = Fore.MAGENTA
    C_WHITE   = Fore.WHITE
else:
    C_RESET = C_BOLD = C_GREY = C_GREEN = C_YELLOW = C_RED = ""
    C_BLUE = C_CYAN = C_MAGENTA = C_WHITE = ""


# ----------------------------------------------------------------------
# Prefixos icônicos (MEDIUM + pictogramas ASCII)
# ----------------------------------------------------------------------
PREFIX = {
    logging.DEBUG:    f"{C_GREY}[D] {C_RESET}",
    logging.INFO:     f"{C_BLUE}[i] {C_RESET}",
    logging.WARNING:  f"{C_YELLOW}[!] {C_RESET}",
    logging.ERROR:    f"{C_RED}[✖] {C_RESET}",
    logging.CRITICAL: f"{C_RED}[✖] {C_RESET}",
}

# ----------------------------------------------------------------------
# Classe de filtro: insere o prefixo colorido no início do msg
# ----------------------------------------------------------------------
class ConsoleColorFilter(logging.Filter):
    """Insere um prefixo colorido (nível + ícone) no início de cada log."""

    def filter(self, record):
        prefix = PREFIX.get(record.levelno, "")
        if prefix:
            record.msg = f"{prefix}{record.msg}"
        return True


# ----------------------------------------------------------------------
# Formatação legível no console (sempre com timestamp e módulo)
# ----------------------------------------------------------------------
class ConsoleFormatter(logging.Formatter):
    """Formata logs para leitura humana no console (não JSON)."""

    DEFAULT_FORMAT = "%(asctime)s | %(name)s | %(message)s"
    DEFAULT_DATE   = "%H:%M:%S"

    def __init__(self, *, fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATE, colors=True):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.use_colors = colors and _HAS_COLORAMA

    def format(self, record):
        # O "asctime" só existe após o formatter chamar record.created.
        # Forçamos a criação do timestamp aqui para podermos colorir:
        if not hasattr(record, "asctime") or not record.asctime:
            record.asctime = self.formatTime(record, self.datefmt)

        if self.use_colors:
            asctime = f"{C_GREY}{record.asctime}{C_RESET}"
            name    = f"{C_CYAN}{record.name}{C_RESET}"
            message = record.getMessage()

            record.asctime = asctime
            record.name    = name
            record.message = message

        return super().format(record)


# ----------------------------------------------------------------------
# Setup: console colorido + JSON para saída externa (se desejado)
# ----------------------------------------------------------------------
def setup_console_logger(
    name: str,
    level: int = logging.INFO,
    use_colors: bool = True,
    json_output: bool = False,
) -> logging.Logger:
    """Cria (ou reconfigura) um logger com saída colorida no console.

    Parameters
    ----------
    name: str
        Nome do logger (ex.: "cloudflare-bypass.api").
    level: int
        Nível mínimo de logs.
    use_colors: bool
        Se False, desabilita cores mesmo se colorama estiver instalado.
    json_output: bool
        Se True, mantém um segundo handler que emite JSON puro para stdout
        (útil quando o processo também alimenta o elastic/filebeats/etc).
    """
    logger = logging.getLogger(name)
    logger.handlers = []

    # Handler principal: console colorido
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ConsoleFormatter(colors=use_colors and _HAS_COLORAMA))
    console_handler.addFilter(ConsoleColorFilter())
    logger.addHandler(console_handler)

    # Handler opcional: JSON puro (compatibilidade com infra que consome logs estruturados)
    if json_output:
        from logger import JsonFormatter  # import local para evitar ciclo
        json_handler = logging.StreamHandler(sys.stdout)
        json_handler.setFormatter(JsonFormatter())
        logger.addHandler(json_handler)

    logger.setLevel(level)
    logger.propagate = False
    return logger
