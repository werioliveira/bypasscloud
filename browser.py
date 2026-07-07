# browser.py
import platform
import logging
import time
import psutil
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger("cloudflare-bypass.browser")


def _terminate_process_tree(pid: int, timeout: float = 3.0):
    """Garante que o Chromium e seus subprocessos não fiquem como zumbis."""
    if not pid or not psutil.pid_exists(pid):
        return

    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return

    processes = parent.children(recursive=True) + [parent]
    for proc in processes:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as exc:
            logger.debug(f"Falha ao enviar terminate para PID {proc.pid}: {exc}")

    gone, alive = psutil.wait_procs(processes, timeout=timeout)
    if alive:
        for proc in alive:
            try:
                logger.warning(f"Forçando kill do processo Chromium preso: PID {proc.pid}")
                proc.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                logger.debug(f"Falha ao enviar kill para PID {proc.pid}: {exc}")
        psutil.wait_procs(alive, timeout=timeout)


def close_browser_safely(browser, process_id: int | None = None):
    """Fecha o navegador via DrissionPage e força cleanup do processo se ele travar."""
    pid = process_id
    if pid is None:
        try:
            pid = browser.process_id
        except Exception:
            pid = None

    try:
        browser.quit(timeout=5, force=True)
    except Exception as exc:
        logger.warning(f"Falha no browser.quit(); limpando processo manualmente: {exc}")
    finally:
        if pid:
            time.sleep(0.2)
            _terminate_process_tree(pid)


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
    options.set_argument("--disable-crash-reporter")
    options.set_argument("--disable-renderer-backgrounding")
    options.set_argument("--disable-background-timer-throttling")
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
