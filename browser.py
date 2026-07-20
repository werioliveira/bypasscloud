import platform
import logging
import os
from DrissionPage import ChromiumPage, ChromiumOptions
from requests import options

logger = logging.getLogger("cloudflare-bypass.browser")

# Adicionado o parâmetro instance_id para isolar perfis e portas
def create_browser(proxy: str = None, headless: bool = False, instance_id: str = "default"):
    is_windows = platform.system() == "Windows"
    options = ChromiumOptions()
    
    # --- 1. PREVENÇÃO CONTRA CRASHES EM DOCKER/WSL ---
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-setuid-sandbox")
    options.set_argument("--disable-dev-shm-usage") 
    
    options.set_argument("--disable-logging")
    options.set_argument("--log-level=3")
    options.set_argument("--no-crash-upload")
    options.set_argument("--disable-crash-reporter")
    options.set_argument("--disable-perf-profiling")
    options.set_argument("--disable-features=Diagnostics")

    # --- 2. CONFIGURAÇÃO DE SISTEMA OPERACIONAL ---
    if is_windows:
        logger.info("Executando no Windows")
        options.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        options.set_argument("--window-size=1920,1080")
        options.headless(True)
        options.auto_port()
    else:
        logger.info("Executando no Linux (Docker/WSL)")
        options.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        browser_path = os.getenv("CHROMIUM_PATH", "/usr/bin/google-chrome-stable")
        options.set_paths(browser_path=browser_path)
        
        # CORREÇÃO 1: Usar auto_port() para evitar colisão de portas ao recriar navegadores
        options.auto_port() 
        options.set_argument("--window-size=1920,1080")
        options.set_argument("--headless=new")
        options.set_argument("--accept-lang=en-US")
        
        # CORREÇÃO 2: Isolar o perfil por instância (evita o erro de SingletonLock do Chrome)
        safe_id = instance_id.replace(":", "_").replace("/", "_")
        profile_path = f"/tmp/drission_profiles/{safe_id}"
        options.set_user_data_path(profile_path)

    # --- 3. ANTI-DETECÇÃO ---
    options.set_argument("--disable-blink-features=AutomationControlled")

    # --- 4. PROXY ---
    if proxy:
        logger.info(f"Configurando proxy via argumento: --proxy-server={proxy}")
        options.set_argument(f"--proxy-server={proxy}")

    # REMOVIDO: O set_local_port(9605) que estava aqui. Ele causava a morte do sistema quando o browser caía.

    try:
        logger.info(f"Iniciando navegador [{safe_id if not is_windows else 'Windows'}] em: {browser_path if not is_windows else 'Padrão'}")
        return ChromiumPage(addr_or_opts=options)
    except Exception as e:
        logger.error(f"Falha crítica ao iniciar o navegador: {e}")
        raise