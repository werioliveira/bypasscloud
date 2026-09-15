import os
import platform
import logging
from urllib.parse import urlparse, unquote

from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger("cloudflare-bypass.browser")


def parse_proxy(proxy) -> dict | None:
    """Faz o parse da URL do proxy e devolve metadados para o Chrome.

    - Normaliza o scheme para o formato aceito pelo --proxy-server
      (Chrome não entende 'socks5h'; usamos 'socks5' + host-resolver-rules).
    - Dentro do container (Linux), '127.0.0.1'/'localhost' são o PRÓPRIO
      container, não a máquina host. Remapeia para 'host.docker.internal'.
    - Extrai usuário/senha (o Chrome IGNORA credenciais no --proxy-server;
      a autenticação é feita via interceptor CDP - ver install_proxy_auth).
    """
    if proxy is None:
        return None
    raw = str(proxy).strip()
    if not raw:
        return None
    if '://' not in raw:
        raw = f'http://{raw}'
    parsed = urlparse(raw)
    scheme = (parsed.scheme or 'http').lower()
    host = parsed.hostname
    if not host:
        raise ValueError(f"Proxy inválido (host ausente): '{proxy}'")
    # Mesmo default do curl quando a porta não é informada
    port = parsed.port or 1080

    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None

    is_socks = scheme.startswith('socks')
    chrome_scheme = scheme
    if chrome_scheme in ('socks', 'socks5h', 'socks4a'):
        chrome_scheme = 'socks5'
    if chrome_scheme not in ('http', 'https', 'socks4', 'socks5'):
        raise ValueError(f"Scheme de proxy não suportado pelo Chrome: '{scheme}'")

    # Dentro do container, loopback não é a máquina host.
    is_linux = platform.system() != 'Windows'
    if is_linux and host in ('localhost', '127.0.0.1', '::1') \
            and os.getenv('PROXY_NO_REMAP', '0').lower() not in ('1', 'true', 'yes'):
        logger.info(
            f"Proxy apontando para '{host}' remapeado para 'host.docker.internal' "
            f"(dentro do container, {host} é o próprio container)."
        )
        host = 'host.docker.internal'

    return {
        'original': str(proxy),
        'scheme': scheme,
        'chrome_scheme': chrome_scheme,
        'host': host,
        'port': port,
        'username': username,
        'password': password,
        'needs_auth': bool(username or password),
        'is_socks': is_socks,
        'server': f'{chrome_scheme}://{host}:{port}',
    }


def mask_proxy(info: dict | None) -> str:
    """Representação do proxy sem vazar credenciais nos logs."""
    if not info:
        return ''
    auth = f"{info['username']}:***@" if info['username'] else ''
    return f"{info['scheme']}://{auth}{info['host']}:{info['port']}"

def install_proxy_auth(tab, username: str, password: str) -> bool:
    """Instala um interceptor CDP para responder ao desafio 407 do proxy.

    O Chrome ignora 'user:senha@' no argumento --proxy-server (diferente do
    curl, que envia as credenciais automaticamente). Sem isso, toda request
    pelo proxy falha com ERR_PROXY_AUTH_REQUESTED/407 e a aba exibe uma
    página de erro -- por isso o bypass 'não traz dados'.

    Usa o domínio Fetch do CDP (mesma técnica do Puppeteer/Playwright).
    """
    try:
        driver = tab._driver  # driver CDP da aba (mesmo objeto usado pelo listener)

        def _on_auth_required(**kwargs):
            try:
                driver.run(
                    'Fetch.continueWithAuth',
                    requestId=kwargs.get('requestId'),
                    response='ProvideCredentials',
                    username=username,
                    password=password,
                )
            except Exception:
                try:
                    driver.run('Fetch.cancelAuth', requestId=kwargs.get('requestId'))
                except Exception:
                    pass

        def _on_request_paused(**kwargs):
            try:
                driver.run('Fetch.continueRequest', requestId=kwargs.get('requestId'))
            except Exception:
                pass

        driver.run('Fetch.enable', patterns=[{'urlPattern': '*'}], handleAuthRequests=True)
        driver.set_callback('Fetch.authRequired', _on_auth_required)
        driver.set_callback('Fetch.requestPaused', _on_request_paused)
        logger.info("Interceptor de autenticação de proxy instalado (CDP Fetch).")
        return True
    except Exception as e:
        logger.error(f"Falha ao instalar interceptor de autenticação de proxy: {e}")
        return False


def create_browser(proxy=None, headless=None, instance_id: str = "default"):
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
        options.headless(False)
        options.auto_port()
        options.set_argument("--window-size=1920,1080")
    else:
        logger.info("Executando no Linux (Docker/WSL)")
        browser_path = os.getenv("CHROMIUM_PATH", "/usr/bin/google-chrome-stable")
        options.set_paths(browser_path=browser_path)

        # auto_port() evita colisão de portas ao recriar navegadores
        options.auto_port()
        options.set_argument("--window-size=1920,1080")
        options.set_argument("--accept-lang=en-US")

        # Headless APENAS se pedido via HEADLESS=1. O container já roda sob
        # xvfb-run, então o Chrome deve abrir COM interface na tela virtual.
        # O --headless=new forçado era detectado pela Cloudflare e derrubava
        # o bypass no Docker (enquanto no Windows headed funcionava).
        want_headless = headless if headless is not None else \
            os.getenv("HEADLESS", "0").lower() in ("1", "true", "yes")
        if want_headless:
            options.set_argument("--headless=new")

        # Isola o perfil por instância (evita o erro de SingletonLock do Chrome)
        safe_id = str(instance_id).replace(":", "_").replace("/", "_")
        options.set_user_data_path(f"/tmp/drission_profiles/{safe_id}")

    # NOTA: removido o user-agent fixo (Chrome/125). O UA real do binário
    # precisa bater com os headers Sec-CH-UA que o próprio Chrome envia;
    # versão divergente é um sinal forte de bot para a Cloudflare.

    # --- 3. ANTI-DETECÇÃO ---
    options.set_argument("--disable-blink-features=AutomationControlled")

    # --- 4. PROXY ---
    info = parse_proxy(proxy)  # ValueError se inválido -> falha rápido com mensagem clara
    if info:
        logger.info(f"Configurando proxy: {mask_proxy(info)}")
        options.set_argument(f"--proxy-server={info['server']}")
        if info['is_socks']:
            # Com SOCKS o Chrome resolve DNS localmente por padrão (equivale
            # ao 'socks5' do curl). Estas regras forçam o DNS a passar pelo
            # proxy, como o 'socks5h' do curl.
            options.set_argument(
                f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {info['host']}"
            )

    # --- 5. PERFORMANCE ---
    # 'eager': tab.get() retorna quando o DOM está pronto (não espera todas as
    # imagens/recursos). O bypasser faz polling próprio do estado da página,
    # então a detecção/resolução de desafio não muda em nada.
    # Feature-detect: versões antigas do DrissionPage podem não ter o método.
    try:
        options.set_load_mode("eager")
    except Exception:
        pass

    # REMOVIDO: O set_local_port(9605) que estava aqui. Ele causava a morte do sistema quando o browser caía.

    try:
        if is_windows:
            logger.info("Iniciando navegador (Windows)...")
        else:
            logger.info(f"Iniciando navegador [{safe_id}] em: {browser_path}")
        return ChromiumPage(addr_or_opts=options)
    except Exception as e:
        logger.error(f"Falha crítica ao iniciar o navegador: {e}")
        raise