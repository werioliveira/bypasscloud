# api.py
import time
import asyncio
import logging
import platform
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from models import ClientRequest, ClientResponse, Solution
from utils import is_safe_url
from bypasser import CloudflareBypasserEvolved
from browser import create_browser
from bypasser import CloudflareBypasserEvolved, AccessDeniedException
from DrissionPage.errors import PageDisconnectedError
logger = logging.getLogger("cloudflare-bypass.api")

# Armazena navegadores por chave (proxy string ou 'default')
browsers = {}
MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicia o navegador padrão (sem proxy) imediatamente
    logger.info("Iniciando navegador padrão...")
    try:
        browsers['default'] = create_browser()
        logger.info("Navegador padrão pronto.")
    except Exception as e:
        logger.error(f"Erro ao iniciar navegador padrão: {e}")
        browsers['default'] = None

    yield

    # Encerra todos os navegadores ao desligar a API
    logger.info("Encerrando todos os navegadores...")
    for key, browser in list(browsers.items()):
        if browser:
            try:
                browser.quit()
                logger.info(f"Navegador '{key}' fechado.")
            except Exception as e:
                logger.error(f"Erro ao fechar navegador '{key}': {e}")
    browsers.clear()

app = FastAPI(title="Cloudflare Bypass API", version="2.1.0", lifespan=lifespan)

async def get_browser(proxy: str = None):
    """
    Retorna um navegador pronto para uso. Se proxy for especificado,
    reutiliza ou cria um navegador dedicado para aquele proxy.
    """
    if proxy:
        # Cria ou reutiliza navegador com este proxy
        if proxy not in browsers:
            logger.info(f"Criando navegador com proxy: {proxy}")
            try:
                browsers[proxy] = create_browser(proxy=proxy)
            except Exception as e:
                logger.error(f"Erro ao criar navegador com proxy: {e}")
                raise HTTPException(status_code=400, detail=f"Proxy inválido: {e}")
        return browsers[proxy]
    else:
        # Navegador padrão (já foi criado no lifespan)
        if not browsers.get('default'):
            raise HTTPException(status_code=503, detail="Navegador padrão indisponível")
        return browsers['default']

@app.post("/v1")
async def solver_endpoint(request: ClientRequest):
    if not is_safe_url(request.url):
        raise HTTPException(status_code=400, detail="URL inválida")

    async with semaphore:
        tab = None
        browser = None
        try:
            logger.info("Processando requisição", extra={'extra_fields': {'url': request.url}})
            browser = await get_browser(request.proxy)
            tab = browser.new_tab()
            tab.get(request.url)
            time.sleep(0.5)

            bypasser = CloudflareBypasserEvolved(tab)
            if request.clear_session:
                bypasser.clear_session()
                tab.get(request.url)
                time.sleep(0.5)

            success = bypasser.bypass()
            if not success:
                raise HTTPException(status_code=408, detail="Falha ao burlar Cloudflare")

            cookies = tab.cookies()
            json_safe_cookies = [dict(c) for c in cookies]
            turnstile_token = None
            try:
                token_input = tab.ele("input[name='cf-turnstile-response']", timeout=0.5)
                if token_input:
                    turnstile_token = token_input.attr("value")
            except:
                pass

            logger.info("Requisição concluída com sucesso!")
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
        except HTTPException:
            raise
        except AccessDeniedException as e:
            logger.error(f"Bloqueio detectado: {str(e)}")
            raise HTTPException(status_code=403, detail=str(e))
        except Exception as e:
            logger.error(f"Erro ao processar requisição: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if tab:
                try:
                    tab.close()
                except PageDisconnectedError:
                    logger.debug("Tab já estava desconectada ao tentar fechar.")
                except Exception:
                    pass

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "browser": "connected" if browsers.get('default') else "disconnected",
        "platform": platform.system()
    }