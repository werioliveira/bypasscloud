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

browsers_data = {} 
MAX_CONCURRENT_REQUESTS = 5
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
browser_lock = asyncio.Lock()
MAX_REQUESTS_BEFORE_RESTART = 30

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando navegador padrão...")
    try:
        # Passa o instance_id 'default'
        default_browser = await asyncio.to_thread(create_browser, instance_id='default')
        browsers_data['default'] = {'browser': default_browser, 'count': 0}
        logger.info("Navegador padrão pronto.")
    except Exception as e:
        logger.error(f"Erro ao iniciar navegador padrão: {e}")
        browsers_data['default'] = {'browser': None, 'count': 0}

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
    key = proxy if proxy else 'default'
    
    async with browser_lock:
        # 1. Verifica se existe e não é None
        if key in browsers_data and browsers_data[key]['browser'] is not None:
            
            # CORREÇÃO 4: Verifica se o processo do SO está realmente vivo
            if not is_browser_alive(browsers_data[key]['browser']):
                logger.warning(f"Detectado navegador Zumbi na chave '{key}'. Limpando...")
                try:
                    await asyncio.to_thread(browsers_data[key]['browser'].quit)
                except Exception:
                    pass
                # Força a virar None para cair no bloco de criação abaixo
                browsers_data[key]['browser'] = None

        # 2. Cria um novo se precisar (se for novo ou se o zumbi foi limpo acima)
        if key not in browsers_data or browsers_data[key]['browser'] is None:
            logger.info(f"Criando navegador para: {key}")
            try:
                # Passa o instance_id para o browser.py isolar os arquivos
                new_browser = await asyncio.to_thread(create_browser, proxy=proxy, instance_id=key)
                browsers_data[key] = {'browser': new_browser, 'count': 0}
            except Exception as e:
                logger.error(f"Erro ao criar navegador: {e}")
                raise HTTPException(status_code=400, detail=f"Erro ao iniciar browser: {e}")

        # 3. Lógica de reinício preventivo
        if browsers_data[key]['count'] >= MAX_REQUESTS_BEFORE_RESTART:
            logger.info(f"Reiniciando navegador para {key} (limite atingido).")
            try:
                await asyncio.to_thread(browsers_data[key]['browser'].quit)
            except:
                pass
            
            # CORREÇÃO 5: Pausa vital no Linux. Dá tempo do SO liberar a porta e o arquivo .lock
            await asyncio.sleep(0.5) 
            
            try:
                new_browser = await asyncio.to_thread(create_browser, proxy=proxy, instance_id=key)
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
            
            browser_data = await get_browser(request.proxy)
            browser = browser_data['browser']
            
            tab = await asyncio.to_thread(browser.new_tab)
            
            await asyncio.to_thread(tab.get, request.url)
            await asyncio.sleep(1.0)

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