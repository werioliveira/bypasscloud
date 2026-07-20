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
from DrissionPage.errors import PageDisconnectedError

logger = logging.getLogger("cloudflare-bypass.api")

# Dicionário para armazenar navegadores e seus contadores de uso
# Estrutura: { 'default': {'browser': obj, 'count': 0}, 'proxy:123': {'browser': obj, 'count': 0} }
browsers_data = {} 
MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
browser_lock = asyncio.Lock()
MAX_REQUESTS_BEFORE_RESTART = 30

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicia o navegador padrão
    logger.info("Iniciando navegador padrão...")
    try:
        # Executa em thread para não bloquear o startup se demorar muito
        default_browser = await asyncio.to_thread(create_browser)
        browsers_data['default'] = {'browser': default_browser, 'count': 0}
        logger.info("Navegador padrão pronto.")
    except Exception as e:
        logger.error(f"Erro ao iniciar navegador padrão: {e}")
        browsers_data['default'] = {'browser': None, 'count': 0}

    yield

    # Encerra todos os navegadores
    logger.info("Encerrando todos os navegadores...")
    for key, data in list(browsers_data.items()):
        browser = data['browser']
        if browser:
            try:
                await asyncio.to_thread(browser.quit)
                logger.info(f"Navegador '{key}' fechado.")
            except Exception as e:
                logger.error(f"Erro ao fechar navegador '{key}': {e}")
    browsers_data.clear()

app = FastAPI(title="Cloudflare Bypass API", version="2.1.1", lifespan=lifespan)

async def get_browser(proxy: str = None):
    """
    Retorna um navegador pronto para uso.
    """
    key = proxy if proxy else 'default'
    
    # Se não existe ou o navegador morreu, cria um novo
    async with browser_lock: # <--- Garante que apenas UM browser seja criado por vez
        if key not in browsers_data or browsers_data[key]['browser'] is None:
            logger.info(f"Criando navegador para: {key}")
            try:
                new_browser = await asyncio.to_thread(create_browser, proxy=proxy)
                browsers_data[key] = {'browser': new_browser, 'count': 0}
            except Exception as e:
                logger.error(f"Erro ao criar navegador: {e}")
                raise HTTPException(status_code=400, detail=f"Erro ao iniciar browser: {e}")

    browser_info = browsers_data[key]
    
    # Lógica de reinício preventivo
    if browser_info['count'] >= MAX_REQUESTS_BEFORE_RESTART:
        logger.info(f"Reiniciando navegador para {key} (limite de {MAX_REQUESTS_BEFORE_RESTART} atingido).")
        try:
            await asyncio.to_thread(browser_info['browser'].quit)
        except:
            pass # Ignora erros ao fechar o antigo
        
        try:
            new_browser = await asyncio.to_thread(create_browser, proxy=proxy)
            browsers_data[key] = {'browser': new_browser, 'count': 0}
        except Exception as e:
            raise HTTPException(status_code=503, detail="Falha ao reiniciar navegador")

    return browsers_data[key]

@app.post("/v1")
async def solver_endpoint(request: ClientRequest):
    if not is_safe_url(request.url):
        raise HTTPException(status_code=400, detail="URL inválida")

    async with semaphore:
        tab = None
        try:
            logger.info("Processando requisição", extra={'extra_fields': {'url': request.url}})
            
            # Obtém a instância do navegador de forma segura
            browser_data = await get_browser(request.proxy)
            browser = browser_data['browser']
            
            # Cria a nova aba para a requisição atual
            tab = await asyncio.to_thread(browser.new_tab)
            
            await asyncio.to_thread(tab.get, request.url)
            await asyncio.sleep(1.0) # Aguarda renderização de scripts

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

            # Incrementa o contador de uso com segurança
            async with browser_lock:
                key = request.proxy if request.proxy else 'default'
                if key in browsers_data:
                    browsers_data[key]['count'] += 1

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
        except Exception as e:
            logger.error(f"Erro ao processar requisição: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if tab:
                try:
                    await asyncio.to_thread(tab.close)
                except Exception as e:
                    logger.warning(f"Erro ao fechar tab: {e}")

@app.get("/health")
async def health():
    # Acesso seguro ao browser default
    default_browser = browsers_data.get('default', {}).get('browser')
    return {
        "status": "ok",
        "browser": "connected" if default_browser else "disconnected",
        "platform": platform.system()
    }