import time
import asyncio
import logging
import os
import platform
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from models import ClientRequest, ClientResponse, Solution
from utils import is_safe_url, find_navigation_error
from bypasser import CloudflareBypasserEvolved, AccessDeniedException
from browser import create_browser, parse_proxy, install_proxy_auth, mask_proxy
from DrissionPage.errors import PageDisconnectedError
from logger import setup_console_logger as setup_logger

logger = setup_logger("cloudflare-bypass.api")

browsers_data = {}
key_locks: dict = {}  # um lock POR proxy: requests de proxies diferentes rodam em paralelo
MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
MAX_REQUESTS_BEFORE_RESTART = 30

# Navegadores com proxy abertos já no startup (separados por vírgula).
# Ex.: PREWARM_PROXIES=http://172.17.0.1:8989,socks5://10.0.0.2:1080
PREWARM_PROXIES = [p.strip() for p in os.getenv('PREWARM_PROXIES', '').split(',') if p.strip()]


def _get_key_lock(key: str) -> asyncio.Lock:
    """Retorna (criando se preciso) o lock da chave de proxy."""
    lock = key_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        key_locks[key] = lock
    return lock


def _canonical_key(proxy: str = None) -> str:
    """Chave canônica do navegador: 'default' ou 'scheme://host:porta'.

    Garante que 'http://x:8989', 'x:8989' e 'http://x:8989/' usem o MESMO
    navegador (e o mesmo lock), inclusive o pré-aquecido por PREWARM_PROXIES.
    """
    try:
        info = parse_proxy(proxy)
    except ValueError:
        # Proxy malformado: usa a string crua como chave; get_browser vai
        # revalidar e responder 400 com a mensagem adequada.
        return str(proxy).strip() if proxy and str(proxy).strip() else 'default'
    return info['server'] if info else 'default'


# CORREÇÃO 3: Função para detectar o "Navegador Zumbi"
def is_browser_alive(browser) -> bool:
    """Tenta acessar o navegador para verificar se o processo no SO ainda está vivo."""
    try:
        # Tentar acessar a aba atual força uma comunicação com o CDP do Chrome.
        # Se o processo morreu, isso vai lançar uma exceção.
        _ = browser.tab
        return True
    except Exception:
        return False


async def _start_browser(proxy: str = None):
    """Cria e registra um navegador (com ou sem proxy). Não lança exceção."""
    key = _canonical_key(proxy)
    try:
        pinfo = parse_proxy(proxy)
        browser = await asyncio.to_thread(create_browser, proxy=proxy, instance_id=key)
        browsers_data[key] = {'browser': browser, 'count': 0, 'inflight': 0, 'proxy_auth': pinfo}
        logger.info(f"Proxy: {mask_proxy(pinfo) or 'SEM PROXY'}")
        logger.info(f"Chave canônica do navegador: {key}")
        logger.info(f"Navegador pronto: {key}")
    except Exception as e:
        logger.error(f"Erro ao iniciar navegador [{key}]: {e}")
        browsers_data[key] = {'browser': None, 'count': 0, 'inflight': 0, 'proxy_auth': None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sem proxy: sempre pré-aquecido no startup
    api_logger = setup_logger("cloudflare-bypass.api")
    api_logger.info("Iniciando navegador padrão (sem proxy)...")
    await _start_browser(None)

    # Com proxy: abre os navegadores listados em PREWARM_PROXIES em paralelo.
    # Assim a 1ª requisição de cada proxy não paga o custo de abrir o Chrome
    # e requests com proxy e sem proxy já nascem em navegadores separados.
    if PREWARM_PROXIES:
        api_logger.info(f"Pré-aquecendo navegadores com proxy: {PREWARM_PROXIES}")
        await asyncio.gather(*[_start_browser(p) for p in PREWARM_PROXIES])

    yield

    logger.info("Encerrando todos os navegadores...")
    for key, data in list(browsers_data.items()):
        browser = data['browser']
        if browser:
            try:
                await asyncio.to_thread(browser.quit)
            except Exception as e:
                pass
    browsers_data.clear()

app = FastAPI(title="Cloudflare Bypass API", version="2.1.2", lifespan=lifespan)

async def get_browser(proxy: str = None):
    """Retorna os dados do navegador da chave (proxy ou 'default').

    - Lock POR chave: requisições de proxies diferentes rodam em paralelo.
    - 'inflight' conta as abas em uso; o reinício preventivo só acontece com
      zero abas em voo, para nunca matar a aba de outra requisição.
    """
    key = _canonical_key(proxy)

    async with _get_key_lock(key):
        data = browsers_data.get(key)

        # 1. Zumbi: o objeto existe, mas o processo do SO morreu
        if data and data['browser'] is not None and not is_browser_alive(data['browser']):
            logger.warning(f"Detectado navegador Zumbi na chave '{key}'. Limpando...")
            try:
                await asyncio.to_thread(data['browser'].quit)
            except Exception:
                pass
            data['browser'] = None

        # 2. Cria se for a primeira vez da chave ou se o zumbi foi limpo
        if data is None or data['browser'] is None:
            logger.info(f"Criando navegador para: {key}")
            try:
                # parse_proxy valida/normaliza o proxy (pode levantar ValueError)
                pinfo = parse_proxy(proxy)
                # Passa o instance_id para o browser.py isolar os arquivos
                new_browser = await asyncio.to_thread(create_browser, proxy=proxy, instance_id=key)
                data = {'browser': new_browser, 'count': 0, 'inflight': 0, 'proxy_auth': pinfo}
                browsers_data[key] = data
            except Exception as e:
                logger.error(f"Erro ao criar navegador: {e}")
                raise HTTPException(status_code=400, detail=f"Erro ao iniciar browser: {e}")

        # 3. Reinício preventivo (recicla memória/estado do Chrome) —
        #    apenas quando nenhuma aba está em uso no navegador.
        if data['count'] >= MAX_REQUESTS_BEFORE_RESTART:
            if data['inflight'] == 0:
                logger.info(f"Reiniciando navegador para {key} (limite atingido).")
                try:
                    await asyncio.to_thread(data['browser'].quit)
                except Exception:
                    pass
                # CORREÇÃO 5: Pausa vital no Linux. Libera a porta e o arquivo .lock
                await asyncio.sleep(0.5)
                try:
                    data['proxy_auth'] = parse_proxy(proxy)
                    data['browser'] = await asyncio.to_thread(create_browser, proxy=proxy, instance_id=key)
                    data['count'] = 0
                except Exception as e:
                    logger.error(f"Erro ao reiniciar navegador para {key}: {e}")
                    raise HTTPException(status_code=503, detail="Falha ao reiniciar navegador")
            else:
                logger.info(f"Reinício de [{key}] adiado: {data['inflight']} aba(s) em uso.")

        # 4. Marca uma aba em voo (decrementada no finally do endpoint)
        data['inflight'] += 1

    return data

async def _recycle_browser(proxy: str = None):
    """Mata o navegador da chave (se existir) e cria outro do zero.

    Usado quando o Chrome desconecta no meio de uma requisicao
    (PageDisconnectedError): o objeto continua existindo, mas a conexao
    CDP esta morta — is_browser_alive nao detecta todos os casos.
    """
    key = _canonical_key(proxy)
    async with _get_key_lock(key):
        data = browsers_data.get(key)
        if data and data['browser'] is not None:
            logger.warning(f"Reciclando navegador [{key}]...")
            try:
                await asyncio.to_thread(data['browser'].quit)
            except Exception:
                pass
            data['browser'] = None
    data = await get_browser(proxy)
    data['inflight'] = 0  # nenhuma aba valida restou do navegador antigo
    return data


@app.post("/v1")
async def solver_endpoint(request: ClientRequest):
    if not is_safe_url(request.url):
        raise HTTPException(status_code=400, detail="URL inválida")

    # 2 tentativas: se o Chrome desconectar no meio (PageDisconnectedError),
    # o navegador e reciclado e a requisicao e reprocessada automaticamente.
    for tentativa in (1, 2):
        tab = None
        browser_data = None
        try:
            logger.info(
                f"Processando requisição{tentativa_hint} | "
                f"proxy={'SIM' if browser_data.get('proxy_auth') else 'NÃO'} | "
                f"chave={key} | "
                f"url={request.url}"
            )
            
            browser_data = await get_browser(request.proxy)
            browser = browser_data['browser']
            pinfo = browser_data.get('proxy_auth')

            tab = await asyncio.to_thread(browser.new_tab)

            # Proxies com usuário/senha: o Chrome ignora credenciais no
            # --proxy-server (o curl envia sozinho). Instalamos um interceptor
            # CDP para responder ao 407, senão TODA request pelo proxy falha
            # e a aba exibe página de erro.
            if pinfo and pinfo.get('needs_auth'):
                await asyncio.to_thread(
                    install_proxy_auth, tab, pinfo['username'], pinfo['password']
                )

            loaded = await asyncio.to_thread(tab.get, request.url)
            await asyncio.sleep(0.5)

            # Detecta falha de navegação (ex.: proxy inacessível/407). Sem isso
            # a API devolvia o HTML da página de erro do Chrome como se fosse
            logger.info(
                f"Requisição concluída{tentativa_hint} | "
                f"proxy={'SIM' if browser_data.get('proxy_auth') else 'NÃO'} | "
                f"chave={key} | "
                f"tempo={elapsed:.2f}s | "
                f"url final={tab.url}"
            )

            # sucesso e o cliente ficava sem dados e sem explicação.
            final_url = tab.url or ''
            err_code = find_navigation_error(tab.html or '')
            if (not loaded) or final_url.startswith(('chrome-error://', 'about:')) or err_code:
                hint = ''
                if err_code in ('ERR_PROXY_CONNECTION_FAILED', 'ERR_TUNNEL_CONNECTION_FAILED', 'ERR_SOCKS_CONNECTION_FAILED'):
                    hint = (" O Chrome nao alcancou o proxy. Verifique se o tunel "
                            "(cloudflared) e o app de proxy no celular estao de pe.")
                elif err_code in ('ERR_PROXY_AUTH_REQUESTED', 'ERR_INVALID_AUTH_CREDENTIALS'):
                    hint = (" O proxy exige usuario/senha e a autenticacao falhou "
                            "(o Chrome ignora credenciais na URL do proxy).")
                raise HTTPException(
                    status_code=502,
                    detail=f"Navegacao falhou ({err_code or 'sem resposta'}; url final: {final_url}).{hint}"
                )

            bypasser = CloudflareBypasserEvolved(tab)
            
            if request.clear_session:
                await asyncio.to_thread(bypasser.clear_session)
                await asyncio.to_thread(tab.get, request.url)
                await asyncio.sleep(0.5)

            success = await asyncio.to_thread(bypasser.bypass)
            
            if not success:
                raise HTTPException(status_code=408, detail="Falha ao burlar Cloudflare")

            cookies = await asyncio.to_thread(tab.cookies)
            json_safe_cookies = [dict(c) for c in cookies]
            
            turnstile_token = None
            try:
                def get_token():
                    try:
                        token_input = tab.ele("input[name='cf-turnstile-response']", timeout=3)
                        if token_input:
                            return token_input.attr("value")
                    except:
                        return None
                    return None
                
                turnstile_token = await asyncio.to_thread(get_token)
            except Exception:
                pass

            # Sem lock: o event loop é single-thread e aqui não há await entre
            # leitura e escrita. Só conta requisições bem-sucedidas.
            browser_data['count'] += 1

            return ClientResponse(
                status="ok",
                solution=Solution(
                    url=tab.url,
                    status=200,
                    response=tab.html,
                    userAgent=tab.user_agent,
                    cookies=json_safe_cookies,
                    turnstile_token=turnstile_token
                )
            )
        except PageDisconnectedError as e:
            # Chrome morreu no meio da requisicao (tunel/proxy caiu, OOM etc.).
            # Recicla o navegador e tenta de novo; na 2a falha, erro claro.
            logger.warning(f"Chrome desconectado (tentativa {tentativa}/2): {e}. Reciclando navegador...")
            await _recycle_browser(request.proxy)
            if tentativa == 2:
                raise HTTPException(
                    status_code=502,
                    detail=("O navegador Chrome desconectou durante a requisicao e foi reciclado "
                            f"automaticamente. Tente novamente em alguns segundos. ({e})")
                )
        except AccessDeniedException as e:
            # 403 = bloqueio real do Cloudflare (IP/proxy banido pela regra do site)
            raise HTTPException(status_code=403, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro ao processar requisição: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if browser_data:
                browser_data['inflight'] = max(0, browser_data['inflight'] - 1)
            if tab:
                try:
                    await asyncio.to_thread(tab.close)
                except Exception as e:
                    pass

@app.get("/health")
async def health():
    default_data = browsers_data.get('default', {})
    default_browser = default_data.get('browser')
    
    # CORREÇÃO 6: O health agora usa a mesma lógica para não mentir que está "connected"
    is_alive = is_browser_alive(default_browser) if default_browser else False
    
    return {
        "status": "ok",
        "browser": "connected" if is_alive else "disconnected",
        "platform": platform.system()
    }