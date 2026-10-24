import hashlib
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

def apply_stealth(tab) -> bool:
    """Injeta patches mínimos e coerentes para imitar um Chrome real.

    Princípio: NÃO mascarar com valores falsos o que o Chrome real já
    preenche corretamente (basta desviar). Patches aplicados:
    - navigator.webdriver removido;
    - plugins/mimeTypes realistas APENAS se o navegador reportar vazio;
    - window.chrome completo;
    - permissions.query corrigido (retornava objeto inválido);
    - em headless, o token 'Headless' do UA é removido em runtime mantendo
      a versão exata do binário (coerente com Sec-CH-UA).
    Deve ser chamado antes de navegar para a URL alvo.
    """
    try:
        driver = tab._driver
        driver.run('Page.addScriptToEvaluateOnNewDocument', source='''
            (() => {
                // 1. webdriver: indefinido, como num Chrome real sem automação.
                //    (--disable-blink-features=AutomationControlled já faz isso
                //    nativamente; isto é redundância defensiva.)
                try { delete Object.getPrototypeOf(navigator).webdriver; } catch (e) {}
                try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined, configurable: true}); } catch (e) {}

                // 2. plugins: lista realista do Chrome desktop (PDF viewer),
                //    com length/item/namedItem coerentes. Só intervenho se
                //    vazio (headless); um Chrome headed já tem plugins reais.
                if (navigator.plugins.length === 0) {
                    const pdfMimes = [
                        {type: 'application/pdf',       suffixes: 'pdf'},
                        {type: 'text/pdf',              suffixes: 'pdf'},
                        {type: 'application/x-google-chrome-pdf', suffixes: 'pdf'},
                    ];
                    const names = [
                        ['Chrome PDF Viewer',   'Portable Document Format'],
                        ['Chromium PDF Viewer', 'Portable Document Format'],
                        ['Microsoft Edge PDF Viewer', 'Portable Document Format'],
                        ['WebKit built-in PDF', 'Portable Document Format'],
                    ];
                    const plugins = names.map(([name, description]) => {
                        const p = {name, description, filename: 'internal-pdf-viewer', length: pdfMimes.length};
                        pdfMimes.forEach((m, i) => p[i] = {type: m.type, suffixes: m.suffixes, description, plugins: null});
                        return p;
                    });
                    plugins.item = (i) => plugins[i] || null;
                    plugins.namedItem = (n) => plugins.find(p => p.name === n) || null;
                    Object.defineProperty(navigator, 'plugins', {get: () => plugins, configurable: true});
                    const mimes = pdfMimes;
                    mimes.item = (i) => mimes[i] || null;
                    mimes.namedItem = (n) => mimes.find(m => m.type === n) || null;
                    Object.defineProperty(navigator, 'mimeTypes', {get: () => mimes, configurable: true});
                }

                // 3. window.chrome existe em navegadores reais
                if (!window.chrome) {
                    window.chrome = {app: {isInstalled: false}, runtime: {}, csi: () => {}, loadTimes: () => {}};
                }

                // 4. permissions.query: Estado real (o patch anterior retornava
                //    um objeto inválido, {state: NotificationPermission}, que
                //    a própria Cloudflare podia notar como valor inconsistente).
                const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (originalQuery) {
                    window.navigator.permissions.query = (parameters) => (
                        parameters && parameters.name === 'notifications'
                            ? Promise.resolve({state: Notification.permission, onchange: null})
                            : originalQuery(parameters)
                    );
                }
            })();
        ''')
        logger.debug("Scripts de stealth injetados via CDP.")

        # 5. UA coerente em headless: token HeadlessChrome → Chrome, mantendo a
        #    versão EXATA do binário (discrepância UA ↔ Sec-CH-UA é o que
        #    derrubava o bypass antes). Em headed este UA não contém 'Headless'
        #    e nada é alterado.
        try:
            res = driver.run('Runtime.evaluate', expression='navigator.userAgent', returnByValue=True)
            ua = (res.get('result') or {}).get('value') if isinstance(res, dict) else None
            if ua and 'Headless' in ua:
                clean_ua = ua.replace('Headless', '')
                driver.run(
                    'Network.setUserAgentOverride',
                    userAgent=clean_ua,
                    acceptLanguage='pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.3',
                )
                logger.info(f"User-agent de headless limpo (versão real preservada): {clean_ua}")
        except Exception as e:
            logger.debug(f"Não foi possível ajustar UA de headless: {e}")

        return True
    except Exception as e:
        logger.debug(f"Falha ao injetar scripts de stealth: {e}")
        return False


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
            """Callback para requisições que exigem autenticação (407 Proxy Auth)."""
            try:
                request_id = kwargs.get('requestId')
                if not request_id:
                    logger.debug("Fetch.authRequired sem requestId, ignorando")
                    return
                driver.run(
                    'Fetch.continueWithAuth',
                    requestId=request_id,
                    response='ProvideCredentials',
                    username=username,
                    password=password,
                )
                logger.debug(f"Proxy auth fornecido para requestId={request_id}")
            except Exception as e:
                logger.warning(f"Erro ao fornecer credenciais de proxy: {e}")
                try:
                    driver.run('Fetch.cancelAuth', requestId=kwargs.get('requestId'))
                except Exception:
                    pass

        def _on_request_paused(**kwargs):
            """Callback para requisições pausadas (padrão '*' intercepta tudo)."""
            try:
                request_id = kwargs.get('requestId')
                if not request_id:
                    return
                # Continua a requisição sem modificação
                driver.run('Fetch.continueRequest', requestId=request_id)
            except Exception as e:
                logger.debug(f"Erro ao continuar request pausada: {e}")

        # 1. Habilita Fetch domain para TODAS as requests (pattern '*')
        # handleAuthRequests=True garante que authRequired seja disparado para 401/407
        # IMPORTANTE: Fetch.enable ANTES dos callbacks (ordem importa no CDP)
        driver.run('Fetch.enable', patterns=[{'urlPattern': '*'}], handleAuthRequests=True)

        # 2. Registra callbacks DEPOIS do enable
        driver.set_callback('Fetch.authRequired', _on_auth_required)
        driver.set_callback('Fetch.requestPaused', _on_request_paused)

        logger.info("Interceptor de autenticação de proxy instalado (CDP Fetch).")
        return True
    except Exception as e:
        logger.error(f"Falha ao instalar interceptor de autenticação de proxy: {e}")
        # Tenta desabilitar Fetch se falhou no meio
        try:
            driver.run('Fetch.disable')
        except Exception:
            pass
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
    # Isola o perfil por instância (evita o erro de SingletonLock do Chrome)
    safe_id = str(instance_id).replace(":", "_").replace("/", "_")

    want_headless = False
    if is_windows:
        logger.info("Executando no Windows")
        options.headless(True)
        # Janela maximizada como um Chrome real aberto pelo usuário
        options.set_argument("--start-maximized")
        # Porta FIXA por instância: auto_port() é incompatível com perfil
        # customizado no DrissionPage 4.1.1.2 (set_user_data_path desativa o
        # auto-port e limpa o endereço -> 'address.split(':')' explode
        # 'not enough values to unpack'). Porta determinística via hash do id.
        port = 9600 + (int(hashlib.md5(safe_id.encode()).hexdigest()[:8], 16) % 2000)
        options.set_address(f"127.0.0.1:{port}")
        # Perfil PERSISTENTE (igual ao Chrome real): cookies como cf_clearance
        # sobrevivem entre execuções. Um desafio resolvido uma vez não é
        # resolvido de novo, exatamente como no Chrome do desktop.
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "chrome_profiles", safe_id)
        os.makedirs(profile_dir, exist_ok=True)
        options.set_user_data_path(profile_dir)
    else:
        logger.info("Executando no Linux (Docker/WSL)")
        browser_path = os.getenv("CHROMIUM_PATH", "/usr/bin/google-chrome-stable")
        options.set_paths(browser_path=browser_path)

        # Porta FIXA por instância (mesma restrição do Windows: auto_port()
        # é incompatível com set_user_data_path no DrissionPage 4.1.1.2)
        port = 9600 + (int(hashlib.md5(safe_id.encode()).hexdigest()[:8], 16) % 2000)
        options.set_address(f"127.0.0.1:{port}")
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

        options.set_user_data_path(f"/tmp/drission_profiles/{safe_id}")

    # NOTA: removido o user-agent fixo (Chrome/125). O UA real do binário
    # precisa bater com os headers Sec-CH-UA que o próprio Chrome envia;
    # versão divergente é um sinal forte de bot para a Cloudflare.

    # --- 3. ANTI-DETECÇÃO ---
    options.set_argument("--disable-blink-features=AutomationControlled")

    # Argumentos que DESVIAM de um Chrome real foram removidos:
    # - Sem user-agent fixo: UA fixo (Chrome/124) divergia do Sec-CH-UA do
    #   binário real → inconsistência = sinal forte de bot → a Cloudflare
    #   recarregava o desafio ATÉ MESMO com clique manual.
    # - Sem --disable-features=IsolateOrigins,site-per-process nem
    #   --disable-site-isolation-trials (traços não-padrão).

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
    # Flags VPS-friendly (baixo CPU/RAM) — adicione VPS_MODE=1 no .env para ativar
    is_vps = os.getenv("VPS_MODE", "0").lower() in ("1", "true", "yes")
    if is_vps:
        options.set_argument("--disable-background-timer-throttling")
        options.set_argument("--disable-backgrounding-occluded-windows")
        options.set_argument("--disable-renderer-backgrounding")
        options.set_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees,NetworkService")
        options.set_argument("--disable-ipc-flooding-protection")
        options.set_argument("--metrics-recording-only")
        options.set_argument("--disable-breakpad")
        # Cache/memória
        options.set_argument("--disk-cache-size=10485760")   # 10 MB
        options.set_argument("--media-cache-size=5242880")    # 5 MB
        logger.info("VPS_MODE ativo: flags de economia de CPU/RAM aplicadas")

    # Modo 'normal' (default) para garantir que JS do desafio under-attack carregue
    # (eager pode impedir execução do JS da Cloudflare, deixando a página em branco).
    try:
        options.set_load_mode("normal")
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