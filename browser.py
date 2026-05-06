# browser.py
import platform
import logging
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger("cloudflare-bypass.browser")

def create_browser(proxy: str = None, headless: bool = False):
    is_windows = platform.system() == "Windows"
    options = ChromiumOptions()
    
    # --- OTIMIZAÇÕES DE RAM E CPU (DIETA DO CHROME) ---
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-software-rasterizer")
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-dev-shm-usage") # Usa a memória do sistema ao invés do /dev/shm
    
    #O CORINGA: Desativa o carregamento de imagens. Economiza uns 40% de RAM e CPU ( cloudflare está detectando ).
    #options.set_argument("--blink-settings=imagesEnabled=false")
    
    # Limita a memória RAM que o motor JavaScript (V8) pode usar para 256MB (O padrão é 1.5GB!)
    options.set_argument("--js-flags=--max-old-space-size=256")
    
    # Mata processos em background do Chrome que não fazem falta pro bypass
    options.set_argument("--disable-background-networking")
    options.set_argument("--disable-default-apps")
    options.set_argument("--disable-sync")
    options.set_argument("--disable-translate")
    options.set_argument("--disable-extensions")
    options.set_argument("--disable-component-extensions-with-background-pages")
    options.set_argument("--no-first-run")
    options.set_argument("--safebrowsing-disable-auto-update")
    options.set_argument("--disable-breakpad") # Impede a criação de logs de crash que enchem o disco
    # ------------------------------------------------

    # Anti-detecção
    options.set_argument("--disable-blink-features=AutomationControlled")
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