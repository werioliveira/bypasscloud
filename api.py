# api.py
import time
import asyncio
import logging
import platform
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from fastapi import FastAPI, HTTPException
from models import ClientRequest, ClientResponse, Solution
from utils import is_safe_url
from bypasser import CloudflareBypasserEvolved
from browser import create_browser, close_browser_safely
from DrissionPage.errors import PageDisconnectedError

logger = logging.getLogger("cloudflare-bypass.api")

MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
MAX_REQUESTS_BEFORE_RESTART = int(os.getenv("MAX_REQUESTS_BEFORE_RESTART", "30"))
MAX_TABS_PER_BROWSER = int(os.getenv("MAX_TABS_PER_BROWSER", "6"))
BROWSER_IDLE_TTL_SECONDS = int(os.getenv("BROWSER_IDLE_TTL_SECONDS", "600"))
BROWSER_RESTART_LOCK_TIMEOUT = float(os.getenv("BROWSER_RESTART_LOCK_TIMEOUT", "30"))


@dataclass
class BrowserState:
    browser: object = None
    count: int = 0
    active_tabs: int = 0
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    proxy: str = None
    process_id: int = None


# Estrutura: { 'default': BrowserState(...), 'proxy:123': BrowserState(...) }
browsers_data: dict[str, BrowserState] = {}
browsers_registry_lock = asyncio.Lock()
cleanup_task: asyncio.Task | None = None


def _browser_key(proxy: str = None) -> str:
    return proxy or 'default'


def _get_process_id(browser) -> int | None:
    try:
        return browser.process_id
    except Exception:
        return None


async def _create_browser_state(key: str, proxy: str = None, state: BrowserState | None = None) -> BrowserState:
    browser = await asyncio.to_thread(create_browser, proxy=proxy)
    if state is None:
        state = BrowserState(proxy=proxy)
        browsers_data[key] = state
    state.browser = browser
    state.count = 0
    state.proxy = proxy
    state.process_id = _get_process_id(browser)
    state.last_used = time.monotonic()
    logger.info("Navegador criado", extra={'extra_fields': {'key': key, 'pid': state.process_id}})
    return state


async def _close_browser_state(key: str, state: BrowserState, reason: str):
    browser = state.browser
    state.browser = None
    state.count = 0
    state.active_tabs = 0
    if not browser:
        return

    logger.info("Fechando navegador", extra={'extra_fields': {'key': key, 'pid': state.process_id, 'reason': reason}})
    try:
        await asyncio.to_thread(close_browser_safely, browser, state.process_id)
        logger.info("Navegador fechado", extra={'extra_fields': {'key': key, 'pid': state.process_id}})
    except Exception as e:
        logger.error(f"Erro ao fechar navegador '{key}': {e}")
    finally:
        state.process_id = None


async def _browser_cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.monotonic()
        for key, state in list(browsers_data.items()):
            if key == 'default':
                continue
            if state.browser and state.active_tabs == 0 and now - state.last_used > BROWSER_IDLE_TTL_SECONDS:
                async with state.lock:
                    if state.browser and state.active_tabs == 0 and now - state.last_used > BROWSER_IDLE_TTL_SECONDS:
                        await _close_browser_state(key, state, "idle ttl")
                        async with browsers_registry_lock:
                            browsers_data.pop(key, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cleanup_task
    logger.info("Iniciando navegador padrão...")
    try:
        async with browsers_registry_lock:
            await _create_browser_state('default')
        logger.info("Navegador padrão pronto.")
    except Exception as e:
        logger.error(f"Erro ao iniciar navegador padrão: {e}")
        browsers_data['default'] = BrowserState()

    cleanup_task = asyncio.create_task(_browser_cleanup_loop())
    try:
        yield
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

        logger.info("Encerrando todos os navegadores...")
        for key, state in list(browsers_data.items()):
            async with state.lock:
                await _close_browser_state(key, state, "shutdown")
        browsers_data.clear()


app = FastAPI(title="Cloudflare Bypass API", version="2.1.2", lifespan=lifespan)


async def get_browser(proxy: str = None):
    """Retorna um navegador pronto para uso, com criação/restart serializados por chave."""
    key = _browser_key(proxy)
    async with browsers_registry_lock:
        state = browsers_data.get(key)
        if state is None:
            state = BrowserState(proxy=proxy)
            browsers_data[key] = state

    try:
        await asyncio.wait_for(state.lock.acquire(), timeout=BROWSER_RESTART_LOCK_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Browser ocupado reiniciando")

    try:
        if state.browser is None:
            try:
                await _create_browser_state(key, proxy=proxy, state=state)
            except Exception as e:
                logger.error(f"Erro ao criar navegador: {e}")
                raise HTTPException(status_code=400, detail=f"Proxy inválido ou erro ao iniciar browser: {e}")

        should_restart = state.count >= MAX_REQUESTS_BEFORE_RESTART or state.active_tabs >= MAX_TABS_PER_BROWSER
        if should_restart:
            if state.active_tabs > 0:
                raise HTTPException(status_code=503, detail="Browser no limite de abas ativas; tente novamente")
            logger.info(f"Reiniciando navegador para {key} (limite atingido).")
            await _close_browser_state(key, state, "preventive restart")
            try:
                await _create_browser_state(key, proxy=proxy, state=state)
            except Exception:
                raise HTTPException(status_code=503, detail="Falha ao reiniciar navegador")

        state.count += 1
        state.active_tabs += 1
        state.last_used = time.monotonic()
        return key, state.browser
    finally:
        if state.lock.locked():
            state.lock.release()


async def release_browser_tab(key: str, mark_broken: bool = False):
    state = browsers_data.get(key)
    if not state:
        return
    async with state.lock:
        state.active_tabs = max(0, state.active_tabs - 1)
        state.last_used = time.monotonic()
        if mark_broken and state.browser and state.active_tabs == 0:
            await _close_browser_state(key, state, "browser disconnected/error")


@app.post("/v1")
async def solver_endpoint(request: ClientRequest):
    if not is_safe_url(request.url):
        raise HTTPException(status_code=400, detail="URL inválida")

    async with semaphore:
        tab = None
        browser_key = None
        mark_browser_broken = False
        try:
            logger.info("Processando requisição", extra={'extra_fields': {'url': request.url}})
            browser_key, browser = await get_browser(request.proxy)
            tab = await asyncio.to_thread(browser.new_tab)
            await asyncio.to_thread(tab.get, request.url)
            await asyncio.sleep(0.5)

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

            def get_token():
                try:
                    token_input = tab.ele("input[name='cf-turnstile-response']", timeout=2)
                    if token_input:
                        return token_input.attr("value")
                except Exception:
                    return None
                return None

            turnstile_token = await asyncio.to_thread(get_token)
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
        except PageDisconnectedError:
            mark_browser_broken = True
            logger.error("Browser/tab desconectado durante o processamento")
            raise HTTPException(status_code=503, detail="Browser desconectado; tente novamente")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro ao processar requisição: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if tab:
                try:
                    await asyncio.to_thread(tab.close)
                except PageDisconnectedError:
                    mark_browser_broken = True
                    logger.debug("Tab já estava desconectada ao tentar fechar.")
                except Exception as e:
                    logger.warning(f"Erro ao fechar tab: {e}")
            if browser_key:
                await release_browser_tab(browser_key, mark_broken=mark_browser_broken)


@app.get("/health")
async def health():
    default_browser = browsers_data.get('default', BrowserState()).browser
    return {
        "status": "ok",
        "browser": "connected" if default_browser else "disconnected",
        "platform": platform.system(),
        "browsers": {
            key: {
                "connected": bool(state.browser),
                "active_tabs": state.active_tabs,
                "requests": state.count,
                "pid": state.process_id,
            }
            for key, state in browsers_data.items()
        }
    }
