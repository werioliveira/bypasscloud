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
            
            # Obtém o browser (já com lógica de count e to_thread)
            browser_data = await get_browser(request.proxy)
            browser = browser_data['browser']
            
            # Incrementa o contador de uso
            browsers_data[request.proxy if request.proxy else 'default']['count'] += 1

            # Executa operações síncronas em threads separadas
            tab = await asyncio.to_thread(browser.new_tab)
            
            await asyncio.to_thread(tab.get, request.url)
            await asyncio.sleep(0.5) # Usar asyncio.sleep

            bypasser = CloudflareBypasserEvolved(tab)
            
            if request.clear_session:
                await asyncio.to_thread(bypasser.clear_session)
                await asyncio.to_thread(tab.get, request.url)
                await asyncio.sleep(0.5)

            # O bypass é a parte mais pesada
            success = await asyncio.to_thread(bypasser.bypass)
            
            if not success:
                raise HTTPException(status_code=408, detail="Falha ao burlar Cloudflare")

            cookies = await asyncio.to_thread(tab.cookies)
            json_safe_cookies = [dict(c) for c in cookies]
            
            turnstile_token = None
            try:
                # Ele é síncrono, então to_thread é ideal, mas como é rápido e retorna erro rápido,
                # podemos tentar direto se tivermos cuidado, mas to_thread é mais seguro.
                # Aqui usei um wrapper para pegar o elemento com timeout curto
                def get_token():
                    try:
                        token_input = tab.ele("input[name='cf-turnstile-response']", timeout=2)
                        if token_input:
                            return token_input.attr("value")
                    except:
                        return None
                    return None
                
                turnstile_token = await asyncio.to_thread(get_token)
            except Exception:
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
        except Exception as e:
            logger.error(f"Erro ao processar requisição: {e}")
            # Aqui você poderia verificar se o erro foi de desconexão e resetar o browser
            # Ex: if isinstance(e, PageDisconnectedError): marcar browser como morto
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if tab:
                try:
                    await asyncio.to_thread(tab.close)
                except PageDisconnectedError:
                    logger.debug("Tab já estava desconectada ao tentar fechar.")
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