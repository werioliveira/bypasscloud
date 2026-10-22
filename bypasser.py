# bypasser.py
import os
import random
import time
import logging
from DrissionPage import ChromiumPage
from config import (
    CHALLENGE_TITLES,
    CHALLENGE_SELECTORS,
    ACCESS_DENIED_TITLES,
    ACCESS_DENIED_SELECTORS,
    TURNSTILE_SELECTORS,
    MAX_RETRIES,
)

logger = logging.getLogger("cloudflare-bypass.bypasser")
class AccessDeniedException(Exception):
    """Exceção levantada quando o acesso é bloqueado pela Cloudflare."""
    pass
class CloudflareBypasserEvolved:
    """Bypass de Cloudflare com detecção robusta e clique via shadow DOM."""

    def __init__(self, page: ChromiumPage, max_retries: int = MAX_RETRIES):
        self.page = page
        self.max_retries = max_retries
        self.all_challenge_selectors = CHALLENGE_SELECTORS + TURNSTILE_SELECTORS

    # ---------- Helpers de detecção ----------
    def _title_contains(self, candidates: list[str]) -> bool:
        try:
            title = self.page.title.lower()
        except Exception:
            return False
        return any(candidate in title for candidate in candidates)

    def _element_exists(self, selectors: list[str], timeout: float = 0.5) -> bool:
        if not selectors:
            return False
        selector_str = ', '.join(selectors)
        try:
            return len(self.page.eles(selector_str, timeout=timeout)) > 0
        except Exception:
            return False

    def is_challenge_page(self) -> bool:
        # 1. Script do Cloudflare (infalível se presente)
        if self._element_exists(['script[src*="/cdn-cgi/challenge-platform/"]'], timeout=1.0):
            return True
        # 2. Título de desafio (português/inglês)
        if self._title_contains(CHALLENGE_TITLES):
            return True
        # 3. Seletores visuais (inclui .lds‑ring, iframe Turnstile, etc.)
        if self._element_exists(self.all_challenge_selectors, timeout=1.5):
            return True
        return False

    def is_access_denied(self) -> bool:
        if self._title_contains(ACCESS_DENIED_TITLES):
            logger.error(f"Acesso negado detectado via título: {self.page.title}")
            return True
        if self._element_exists(ACCESS_DENIED_SELECTORS):
            logger.error("Acesso negado detectado via seletor CSS.")
            return True
        return False

    def _dump_debug_html(self, tag: str):
        """Salva o HTML atual para diagnóstico (mesmo formato dos page_*.html antigos)."""
        try:
            os.makedirs('debug_html', exist_ok=True)
            fname = os.path.join('debug_html', f'{tag}_{time.strftime("%Y%m%d_%H%M%S")}.html')
            with open(fname, 'w', encoding='utf-8') as f:
                f.write(self.page.html or '')
            logger.info(f"HTML salvo para diagnóstico: {fname}")
        except Exception as e:
            logger.debug(f"Falha ao salvar HTML de diagnóstico: {e}")

    # ---------- Interação precisa (estilo CloudflareBypassForScraping) ----------
    def locate_cf_turnstile_button(self):
        try:
            eles = self.page.eles("tag:input")
            for ele in eles:
                if "name" in ele.attrs.keys() and "type" in ele.attrs.keys():
                    if "turnstile" in ele.attrs["name"] and ele.attrs["type"] == "hidden":
                        button = ele.parent().shadow_root.child()("tag:body").shadow_root("tag:input")
                        return button
            return None
        except Exception as e:
            logger.debug(f"Erro ao localizar botão Turnstile via shadow DOM: {e}")
            return None

    def click_verify(self) -> bool:
        # Tenta encontrar o botão Turnstile via shadow DOM
        button = self.locate_cf_turnstile_button()
        if button:
            try:
                logger.info("Checkbox do Turnstile encontrado via shadow DOM. Clicando...")
                button.click()
                time.sleep(3)  # Aumentado de 2s para 3s - Cloudflare precisa de tempo para processar
                # Verifica se o token foi gerado imediatamente
                token_input = self.page.ele("input[name='cf-turnstile-response']", timeout=2)
                if token_input and (token_input.attr("value") or ""):
                    logger.info("Token Turnstile gerado após clique sombra.")
                    return True
                logger.info("Clique via shadow DOM realizado - aguardando token...")
            except Exception as e:
                logger.debug(f"Falha ao clicar no checkbox Turnstile: {e}")
        else:
            logger.info("Botão Turnstile não encontrado via shadow DOM.")

        # Fallback: simular usuário humano com Tab+Space
        logger.info("Tentando Tab+Space...")
        try:
            body = self.page.ele('tag:body', timeout=1)
            if body:
                body.click()
                time.sleep(0.3 + random.uniform(0, 0.5))  # Delay humano antes de Tab
            for i in range(3):  # Tentativas múltiplas
                # Tab with human-like delay
                self.page.actions.key_down('TAB').wait(0.05 + random.uniform(0, 0.1)).key_up('TAB')
                self.page.actions.wait(0.1 + random.uniform(0, 0.2))
                # Space with human-like delay
                self.page.actions.key_down('SPACE').wait(0.05 + random.uniform(0, 0.1)).key_up('SPACE')
                self.page.actions.wait(1.0 + random.uniform(0, 0.5))
                # Verifica se o token apareceu após o clique
                token_input = self.page.ele("input[name='cf-turnstile-response']", timeout=1)
                if token_input and (token_input.attr("value") or ""):
                    logger.info("Token Turnstile obtido após Tab+Space!")
                    return True
                # Verifica se o desafio desapareceu
                if not self.is_challenge_page():
                    logger.info("Desafio sumiu após Tab+Space!")
                    return True
                # Delay humano entre tentativas
                if i < 2:
                    time.sleep(1 + random.uniform(0, 2))
            logger.warning("Tab+Space não obteve token após 3 tentativas.")
            return False
        except Exception as e:
            logger.debug(f"Falha no Tab+Space: {e}")
            return False

    def get_turnstile_token(self) -> str:
        try:
            token_input = self.page.ele("input[name='cf-turnstile-response']", timeout=1)
            if not token_input:
                return None
            current = token_input.attr("value") or ""
            if current:
                return current
            self.click_verify()
            start = time.time()
            while time.time() - start < 8:
                token = token_input.attr("value") or ""
                if token:
                    logger.info("Token Turnstile obtido!")
                    return token
                time.sleep(0.5)
            logger.error("Timeout ao obter token Turnstile")
            return None
        except Exception as e:
            logger.error(f"Erro ao obter token Turnstile: {e}")
            return None

    def _wait_real_page(self, timeout: float = 20.0) -> bool:
        start = time.time()
        progress_logged = {}
        while time.time() - start < timeout:
            is_ch = self.is_challenge_page()
            title_contains = self._title_contains(CHALLENGE_TITLES)
            has_selectors = self._element_exists(self.all_challenge_selectors, timeout=0.3)
            html_len = len(self.page.html or "")
            
            # Log de progresso a cada 5s
            progress_key = int(time.time() - start)
            if progress_key > 0 and progress_key % 5 == 0 and progress_key not in progress_logged:
                logger.debug(f"_wait_real_page: is_challenge={is_ch}, title_has_challenge={title_contains}, has_selectors={has_selectors}, html_len={html_len}")
                progress_logged[progress_key] = True
            
            if is_ch:
                # Ainda no desafio - aguarde um pouco e continue
                time.sleep(0.5)
                continue
            
            if title_contains or has_selectors:
                # Título ou seletores indicam desafio, mas is_challenge_page retornou False
                # Isso pode acontecer em transição. Aguarde um pouco.
                time.sleep(1)
                continue
            
            # Nenhum sinal de desafio detectado
            logger.info(f"_wait_real_page: desafio resolvido! (html_len={html_len}, decorrido={time.time()-start:.1f}s)")
            return True
        
        logger.warning(f"Timeout aguardando página real após desafio. Último estado: is_challenge={self.is_challenge_page()}, title={self.page.title!r}")
        return False

    def bypass(self) -> bool:
        if self.is_access_denied():
            title = ''
            try:
                title = self.page.title or ''
            except Exception:
                pass
            self._dump_debug_html('access_denied')
            logger.error(f"Acesso negado pela Cloudflare. [url={self.page.url} | titulo={title!r}]")
            raise AccessDeniedException(
                "O IP foi bloqueado pela Cloudflare (Access Denied). Tente usar um proxy diferente. "
                f"[url={self.page.url} | titulo={title!r}]"
            )

        # Aguarda o Cloudflare injetar o HTML do desafio (under-attack pode deixar página em branco)
        time.sleep(1.0)

        # --- FASE 1: Detecção inicial (até 12s) ---
        start = time.time()
        while time.time() - start < 12.0:
            if self.is_access_denied():
                return False

            title = self.page.title or ''
            html_len = len(self.page.html or "")

            # Página em branco com título "Just a moment..." = under-attack / fingerprint
            if html_len < 500 and self._title_contains(CHALLENGE_TITLES):
                logger.info(f"Página em branco com título de desafio detectada (under-attack). Aguardando fingerprint... [titulo={title!r}]")
                break

            # Atalho seguro para páginas normais
            if html_len > 500 and not self._title_contains(CHALLENGE_TITLES):
                if not self._element_exists(
                    ['script[src*="/cdn-cgi/challenge-platform/"]'], timeout=0.5
                ):
                    time.sleep(0.2)
                    if not self.is_challenge_page():
                        logger.info("Nenhum desafio detectado. Página carregada diretamente.")
                        return True

            # Verificação completa para capturar desafios reais
            if self.is_challenge_page():
                break

            time.sleep(0.3)

        if not self.is_challenge_page():
            logger.info("Nenhum desafio detectado após espera inicial.")
            return True

        has_turnstile = self._element_exists(TURNSTILE_SELECTORS, timeout=0.5)
        if has_turnstile:
            logger.info("Desafio Turnstile identificado.")
        else:
            # --- FASE 2: Under-attack / fingerprint — espera passiva prolongada ---
            logger.info("Desafio padrão/under-attack detectado. Aguardando resolução automática (fingerprint) por até 45s...")
            passive_start = time.time()
            passive_logged = {}
            while time.time() - passive_start < 45.0:
                if self.is_access_denied():
                    return False
                if not self.is_challenge_page():
                    logger.info("Desafio resolvido automaticamente (fingerprint).")
                    return True
                # Log de progresso a cada 10s
                progress_key = int(time.time() - passive_start)
                if progress_key > 0 and progress_key % 10 == 0 and progress_key not in passive_logged:
                    title = self.page.title or ''
                    html_len = len(self.page.html or "")
                    logger.debug(f"Fingerprint waiting: {progress_key}s - is_challenge={self.is_challenge_page()}, title={title!r}, html_len={html_len}")
                    passive_logged[progress_key] = True
                time.sleep(1)

        # --- FASE 3: Tentativas ativas de clique/token ---
        # Antes de tentar cliques, aguarde o elemento Turnstile aparecer (até 10s)
        # Isso dá tempo para o desafio under-attack se resolver ou o Turnstile carregar
        logger.info("Aguardando elemento Turnstile aparecer (até 10s)...")
        turnstile_wait_start = time.time()
        has_turnstile_wait = False
        while time.time() - turnstile_wait_start < 10.0:
            has_turnstile = self._element_exists(TURNSTILE_SELECTORS, timeout=1.0)
            if not has_turnstile:
                # Verifica se o desafio sumiu (fingerprint resolvesu)
                if not self.is_challenge_page():
                    logger.info("Desafio resolvido durante aguardo do Turnstile.")
                    return True
                time.sleep(0.5)
                continue
            logger.info("Elemento Turnstile detectado. Prosseguindo com bypass...")
            has_turnstile_wait = True
            break
        if not has_turnstile_wait:
            logger.warning("Timeout aguardando Turnstile - tentando bypass de qualquer maneira.")

        # --- Diagnóstico pré-interação: verificar se a página realmente precisa de interação ---
        # Se após 45s o título ainda indica desafio mas o HTML tem conteúdo real, pode ser que
        # o fingerprint já tenha resolvido mas os indicadores persistem temporariamente
        pre_click_title = self.page.title or ''
        pre_click_html_len = len(self.page.html or '')
        is_still_challenge = self.is_challenge_page()
        
        logger.debug(f"Pré-interação: title={pre_click_title!r}, html_len={pre_click_html_len}, is_challenge_page={is_still_challenge}")
        
        # Se o page HTML já tem conteúdo significativo (> 5000 bytes), o fingerprint pode ter resolvido
        # Mesmo com título ainda mostrando "Just a moment", não devemos clicar se não precisar
        if pre_click_html_len > 5000 and is_still_challenge:
            logger.info(f"Página tem conteúdo significativo ({pre_click_html_len} bytes) mas título ainda indica desafio. "
                       "Fingerprint pode ter resolvido - evitando interação desnecessária.")
            # Verificar se o token já está presente
            token_input = self.page.ele("input[name='cf-turnstile-response']", timeout=1)
            if token_input and (token_input.attr("value") or ""):
                logger.info("Token Turnstile já presente no HTML após fingerprint - desafio resolvido!")
                return True
            # Se há conteúdo real e token, considerar página carregada
            if token_input:
                logger.info("Desafio resolvido (fingerprint + conteúdo real). Retornando sucesso.")
                return True
            # Se não tem token nem conteúdo significativo após 45s, então tentar interação
            logger.warning("Mesmo com conteúdo, ainda não há token - prosseguindo com bypass cuidadoso.")
        
        for attempt in range(self.max_retries):
            logger.info(f"Tentativa de bypass {attempt + 1}/{self.max_retries}")

            if has_turnstile:
                token = self.get_turnstile_token()
                if token and self._wait_real_page(timeout=25.0):
                    logger.info("Desafio Turnstile resolvido!")
                    return True
            else:
                self.click_verify()
                if self._wait_real_page(timeout=25.0):
                    logger.info("Desafio resolvido!")
                    return True

            if self.is_access_denied():
                return False
            time.sleep(3)

        logger.error(f"Falha após {self.max_retries} tentativas.")
        return False

    def clear_session(self):
        logger.info("Limpando sessão do navegador...")
        try:
            cookies = self.page.cookies()
            for cookie in cookies:
                try:
                    self.page.delete_cookie(cookie['name'])
                except Exception:
                    pass
            self.page.run_js("""
                try {
                    localStorage.clear();
                    sessionStorage.clear();
                    ['cf_chl_opt','cf_chl_prog','turnstile','__cfuid'].forEach(k => {
                        localStorage.removeItem(k);
                        sessionStorage.removeItem(k);
                    });
                    ['__cf_bm','__cfduid','cf_clearance','__cflb','__cfruid'].forEach(n => {
                        document.cookie = n + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
                        document.cookie = n + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=" + location.hostname;
                        document.cookie = n + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/;domain=." + location.hostname;
                    });
                } catch(e) {}
            """)
            logger.info("Sessão limpa!")
        except Exception as e:
            logger.error(f"Erro ao limpar sessão: {e}")