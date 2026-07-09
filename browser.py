import platform
import logging
import os
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger("cloudflare-bypass.browser")

def create_browser(proxy: str = None, headless: bool = False):
    is_windows = platform.system() == "Windows"
    options = ChromiumOptions()
    
    # --- 1. PREVENÇÃO CONTRA CRASHES EM DOCKER/WSL ---
    # Essas flags resolvem o Trace/breakpoint trap e falta de memória
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-setuid-sandbox")
    options.set_argument("--disable-dev-shm-usage") # Crucial para VPS com pouca RAM
    options.set_argument("--disable-gpu")
    
    # Desativa telemetria e checagens de CPU que falham em containers
    options.set_argument("--disable-logging")
    options.set_argument("--log-level=3")
    options.set_argument("--no-crash-upload")
    options.set_argument("--disable-crash-reporter")
    options.set_argument("--disable-perf-profiling")
    options.set_argument("--disable-features=Diagnostics")

# --- 2. CONFIGURAÇÃO DE SISTEMA OPERACIONAL ---
    if is_windows:
        logger.info("Executando no Windows")
        options.headless(True)
        options.auto_port()
    else:
        logger.info("Executando no Linux (Docker/WSL)")
        
        browser_path = os.getenv("CHROMIUM_PATH", "/usr/bin/google-chrome-stable")
        options.set_paths(browser_path=browser_path)
        
        # Garante portas dinâmicas e limpas para evitar colisões no Docker
        options.auto_port() 
        
        # Força headless correto no Linux
        options.set_argument("--headless=new")
        options.set_argument("--accept-lang=en-US")
        
        # IMPORTANTE: No DrissionPage, use set_user_data_path e deixe ele gerenciar
        # Adicione um timestamp ou número aleatório se os navegadores abrirem juntos
        options.set_user_data_path("/tmp/drission_profiles")

    # --- 3. ANTI-DETECÇÃO ---
    options.set_argument("--disable-blink-features=AutomationControlled")

    # --- 4. PROXY ---
    if proxy:
        logger.info(f"Configurando proxy via argumento: --proxy-server={proxy}")
        options.set_argument(f"--proxy-server={proxy}")

    # --- 5. PORTA CDP ---
    # No Linux Docker, fixar a porta é mais estável para o DrissionPage se conectar
    if not is_windows:
        options.set_local_port(9605)
    else:
        options.auto_port()

    try:
        logger.info(f"Iniciando navegador em: {browser_path if not is_windows else 'Padrão Windows'}")
        return ChromiumPage(addr_or_opts=options)
    except Exception as e:
        logger.error(f"Falha crítca ao iniciar o navegador: {e}")
        raise