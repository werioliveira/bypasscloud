# browser.py
import platform
import logging
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger("cloudflare-bypass.browser")

def create_browser(proxy: str = None, headless: bool = False):
    is_windows = platform.system() == "Windows"
    options = ChromiumOptions()

    # Argumentos comuns
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-blink-features=AutomationControlled")
    options.set_argument("--disable-extensions")
    options.set_argument("--disable-software-rasterizer")
    #options.set_argument("--blink-settings=imagesEnabled=false")
    if is_windows:
        logger.info("Executando no Windows")
    else:
        logger.info("Executando no Linux")
        options.set_paths(browser_path="/usr/bin/chromium-browser")
        options.headless(headless)
        options.set_argument("--accept-lang=en-US")

    # Configura proxy via argumento de linha de comando (aceita http, https, socks5)
    if proxy:
        logger.info(f"Configurando proxy via argumento: --proxy-server={proxy}")
        options.set_argument(f"--proxy-server={proxy}")

    options.auto_port()
    return ChromiumPage(addr_or_opts=options)