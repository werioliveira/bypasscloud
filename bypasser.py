# bypasser.py
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
        button = self.locate_cf_turnstile_button()
        if button:
            try:
                logger.info("Checkbox do Turnstile encontrado via shadow DOM. Clicando...")
                button.click()
                time.sleep(2)
                return True
            except Exception as e:
                logger.debug(f"Falha ao clicar no checkbox Turnstile: {e}")

        logger.info("Botão Turnstile não encontrado. Tentando Tab+Space...")
        try:
            body = self.page.ele('tag:body', timeout=1)
            if body:
                body.click()
                time.sleep(0.3)
            for _ in range(1):
                self.page.actions.key_down('TAB').wait(0.05).key_up('TAB')
                self.page.actions.wait(0.2)
            self.page.actions.wait(0.5)
            self.page.actions.key_down('SPACE').wait(0.05).key_up('SPACE')
            self.page.actions.wait(1.0)
            time.sleep(2)
            return True
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
        while time.time() - start < timeout:
            if self.is_challenge_page():
                time.sleep(1)
                continue
            try:
                self.page.wait.load_complete(timeout=8)
            except Exception:
                pass
            if not self._title_contains(CHALLENGE_TITLES) and \
               not self._element_exists(self.all_challenge_selectors, timeout=0.3):
                return True
            time.sleep(0.5)
        logger.warning("Timeout aguardando página real após desafio.")
        return False

    def bypass(self) -> bool:
        if self.is_access_denied():
            logger.error("Acesso negado pela Cloudflare.")
            raise AccessDeniedException("O IP foi bloqueado pela Cloudflare (Access Denied). Tente usar um proxy diferente.")

        time.sleep(0.5)  # Pequena margem para o Cloudflare injetar seu HTML

        start = time.time()
        while time.time() - start < 6.0:
            if self.is_access_denied():
                return False

            # Atalho seguro para páginas normais: se tem conteúdo e título OK
            if len(self.page.html or "") > 500 and not self._title_contains(CHALLENGE_TITLES):
                if not self._element_exists(
                    ['script[src*="/cdn-cgi/challenge-platform/"]'], timeout=0.5
                ):
                    # Confirmação dupla para evitar fechamento precoce
                    time.sleep(0.2)
                    if not self.is_challenge_page():
                        logger.info("Nenhum desafio detectado. Página carregada diretamente.")
                        return True

            # Verificação completa (mais demorada) para capturar desafios reais
            if self.is_challenge_page():
                break

            time.sleep(0.3)

        if not self.is_challenge_page():
            logger.info("Nenhum desafio detectado após espera curta.")
            return True

        has_turnstile = self._element_exists(TURNSTILE_SELECTORS, timeout=0.5)
        if has_turnstile:
            logger.info("Desafio Turnstile identificado.")

        for attempt in range(self.max_retries):
            logger.info(f"Tentativa de bypass {attempt + 1}/{self.max_retries}")

            if has_turnstile:
                token = self.get_turnstile_token()
                if token and self._wait_real_page():
                    logger.info("Desafio Turnstile resolvido!")
                    return True
            else:
                self.click_verify()
                if self._wait_real_page():
                    logger.info("Desafio resolvido!")
                    return True

            if self.is_access_denied():
                return False
            time.sleep(1)

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