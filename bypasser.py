# bypasser.py
import json
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

# Snapshot leve do estado da página: UMA Runtime.evaluate por poll em vez de
# 4-6 chamadas CDP (page.html serializa o DOM inteiro a cada consulta, o que
# pesa muito numa VPS com CPU fraca). 'htmlLen' é calculado DENTRO do browser
# (outerHTML.length), sem transferir o HTML por websocket.
# Seletores são passados como array JSON (__SEL__) e unidos no JS com ','.
# challengeScript: detecta APENAS scripts de desafio ATIVO (pasta h/ ou turnstile),
# NÃO analytics/beacon (/cdn-cgi/challenge-platform/scripts/jsd/main.js).
JS_STATE = (
    "const d = document;"
    "const st = {};"
    "st.title = d.title || '';"
    "st.htmlLen = d.documentElement ? d.documentElement.outerHTML.length : 0;"
    # Scripts de desafio ativo: /cdn-cgi/challenge-platform/h/... ou turnstile
    # Exclui analytics: /cdn-cgi/challenge-platform/scripts/jsd/main.js
    'const cs = d.querySelector(\'script[src*="/cdn-cgi/challenge-platform/"]\');'
    'st.challengeScript = cs && (cs.src.includes("/h/") || cs.src.includes("turnstile"));'
    # __SEL__ será substituído por JSON array: ["sel1", "sel2", ...]
    # No JS fazemos: __SEL__.join(",") para criar seletor composto válido
    'st.suspect = !!d.querySelector(__SEL__.join(","));'
    'const ti = d.querySelector(\'input[name="cf-turnstile-response"]\');'
    "st.token = (ti && ti.value) || '';"
    "st.turnstileInput = !!ti;"
    "st.turnstileIframe = !!d.querySelector(\"iframe[src*='challenges.cloudflare.com']\");"
    "return JSON.stringify(st);"
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
        # Seletores como lista para JSON encoding no _page_state
        self._js_selector = self.all_challenge_selectors

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

    # ---------- Snapshot leve (VPS com CPU fraca) ----------
    def _page_state(self) -> dict:
        """Estado da página com UMA chamada JS (barato em CPU e I/O de CDP).

        Substitui o padrão antigo (is_challenge_page + _title_contains +
        _element_exists + page.html = 4-6 roundtrips com serialização DOM)
        por uma única Runtime.evaluate a cada iteração. Um snapshot típico
        tem ~80 bytes de resposta, contra dezenas de KB do page.html.
        """
        # Constrói o JS com o seletor como JSON array (evita problemas de aspas)
        js = JS_STATE.replace('__SEL__', json.dumps(self._js_selector))
        try:
            raw = self.page.run_js(js)
            if isinstance(raw, str):
                return json.loads(raw)
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning(f"_page_state JS error: {e}")
            return {}

    def _state_is_challenge(self, st: dict) -> bool:
        """Detecta desafio a partir do snapshot já obtido (sem novas consultas)."""
        if st.get('challengeScript') or st.get('suspect'):
            return True
        title = (st.get('title') or '').lower()
        return any(t in title for t in CHALLENGE_TITLES)

    def is_challenge_page(self) -> bool:
        st = self._page_state()
        if st:
            return self._state_is_challenge(st)
        # Fallback quando a chamada JS falha (mesma semântica do original)
        if self._title_contains(CHALLENGE_TITLES):
            return True
        return self._element_exists(self.all_challenge_selectors, timeout=1.5)

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
        """Espera a página real aparecer após o desafio (1 snapshot JS leve/iteração)."""
        start = time.time()
        positive_streak = 0
        while time.time() - start < timeout:
            try:
                st = self._page_state()
            except Exception:
                time.sleep(1.0)
                continue

            title = (st.get('title') or '').lower()
            has_challenge_dom = st.get('challengeScript') or st.get('suspect')
            challenge = (
                has_challenge_dom
                or any(t in title for t in CHALLENGE_TITLES)
            )

            html_len = st.get('htmlLen', 0)

            # Fast-path: HTML grande (+5KB) E sem elementos de desafio no DOM = página real
            if html_len > 5000 and not has_challenge_dom:
                logger.info(
                    f"_wait_real_page: página real detectada via HTML "
                    f"(html_len={html_len}, decorrido={time.time() - start:.1f}s)"
                )
                return True

            if challenge:
                positive_streak = 0
                time.sleep(1.0)
                continue

            # 1 positive streak basta (era 2 = 2s mínimo)
            if html_len > 500:
                positive_streak += 1
                if positive_streak >= 1:
                    logger.info(
                        f"_wait_real_page: página real detectada "
                        f"(html_len={html_len}, decorrido={time.time() - start:.1f}s)"
                    )
                    return True
            else:
                positive_streak = 0

            time.sleep(1.0)

        last_title = ''
        try:
            last_title = self.page.title or ''
        except Exception:
            pass
        logger.warning(f"Timeout aguardando página real após desafio. [titulo={last_title[:60]!r}]")
        return False

    def bypass(self) -> bool:
        import time as _time
        _bypass_start = _time.time()
        _phase_start = _bypass_start
        def _log_phase(name: str):
            nonlocal _phase_start
            now = _time.time()
            logger.info(f"[TIMING] {name}: {now - _phase_start:.2f}s (total: {now - _bypass_start:.2f}s)")
            _phase_start = now

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

        _log_phase("INICIO bypass")

        # Aguarda o Cloudflare injetar o HTML do desafio (under-attack pode deixar página em branco)
        time.sleep(1.0)

        # --- FASE 1: Detecção inicial (até ~10s) — snapshot JS leve ---
        _log_phase("FASE1 inicio")
        start = time.time()
        blank_logged = False
        while time.time() - start < 10.0:
            if self.is_access_denied():
                return False

            st = self._page_state()
            st_challenge = self._state_is_challenge(st)
            html_len = st.get('htmlLen', 0)
            has_challenge_dom = st.get('challengeScript') or st.get('suspect')

            # DEBUG: log valores a cada iteração
            logger.info(f"FASE1 loop: html_len={html_len}, has_challenge_dom={has_challenge_dom}, st_challenge={st_challenge}, title={st.get('title')!r}, challengeScript={bool(st.get('challengeScript'))}, suspect={bool(st.get('suspect'))}")

            # Página em branco com título de desafio = under-attack / fingerprint
            if st_challenge and html_len < 500:
                if not blank_logged:
                    blank_logged = True
                    logger.info(f"Página em branco com título de desafio (under-attack). Aguardando fingerprint... [titulo={st.get('title')!r}]")
                time.sleep(1.0)
                continue

            # Fast-path: HTML grande (+5KB) E sem elementos de desafio no DOM = página real
            # (título pode estar stale durante transição/redirect)
            if html_len > 5000 and not has_challenge_dom:
                logger.info(f"Página real detectada via HTML ({html_len} bytes) — ignorando título stale.")
                return True

            # Fast-path JSON/API: resposta pequena (>100 bytes) mas SEM indicadores de desafio NO DOM
            # (evita esperar 10s em endpoints que retornam JSON direto)
            # IMPORTANTE: usa has_challenge_dom (DOM real), NÃO st_challenge (inclui título)
            if html_len > 100 and not has_challenge_dom:
                logger.info(f"Resposta JSON/API detectada ({html_len} bytes) — sem desafio Cloudflare.")
                return True

            # Fallback original: HTML médio + sem desafio no DOM
            if html_len > 2500 and not has_challenge_dom:
                logger.info(f"Nenhum desafio detectado. Página carregada diretamente ({html_len} bytes).")
                return True

            # Se chegou aqui: ou página ainda carrega (html_len <= 2500)
            # ou há indicadores de desafio NO DOM (has_challenge_dom)
            # Em ambos os casos, há desafio ativo → quebrar para FASE 2/3
            if has_challenge_dom or st_challenge:
                break

            # Página em branco (html_len <= 2500) sem challenge DOM → aguarda carregar
            time.sleep(0.5)

        # Loop de 10s expirou sem detectar desafio claro → verificação final leve
        st = self._page_state()
        if not (st.get('challengeScript') or st.get('suspect') or self._state_is_challenge(st)):
            html_len = st.get('htmlLen', 0)
            # Página muito pequena (< 500 bytes) sem desafio = carga falha, não sucesso
            # Pode ser: proxy bloqueando conteúdo, JS não executou, SSL error, etc.
            if html_len < 500:
                logger.warning(
                    f"Página carregada mas quase vazia (html_len={html_len}) sem indicadores de desafio. "
                    f"Provável falha de carga (proxy/SSL/JS). Não trata como sucesso."
                )
                # Tenta recarregar uma vez antes de falhar
                try:
                    self.page.refresh()
                    time.sleep(2.0)
                    st2 = self._page_state()
                    html_len2 = st2.get('htmlLen', 0)
                    if html_len2 > 500:
                        logger.info(f"Recarregamento funcionou: html_len={html_len2}")
                        return True
                    logger.error(f"Recarregamento não resolveu: html_len={html_len2}")
                except Exception as e:
                    logger.error(f"Erro ao tentar recarregar: {e}")
                return False
            
            logger.info("Nenhum desafio detectado após espera inicial.")
            _log_phase("FASE1 fim - sem desafio")
            return True

        _log_phase("FASE1 fim - desafio detectado")

        has_turnstile = self._element_exists(TURNSTILE_SELECTORS, timeout=0.5)
        if has_turnstile:
            logger.info("Desafio Turnstile identificado.")
            _log_phase("Turnstile detectado")
        else:
            # --- FASE 2: Under-attack / fingerprint — espera passiva leve ---
            _log_phase("FASE2 inicio")
            logger.info("Desafio padrão/under-attack detectado. Aguardando resolução automática (fingerprint) por até 45s...")
            passive_start = time.time()
            passive_key = -1
            while time.time() - passive_start < 45.0:
                if self.is_access_denied():
                    return False
                st = self._page_state()
                html_len = st.get('htmlLen', 0)
                has_challenge_dom = st.get('challengeScript') or st.get('suspect')

                # Fast-path: HTML grande (+5KB) E sem elementos de desafio no DOM
                if html_len > 5000 and not has_challenge_dom:
                    logger.info(f"Página real detectada via HTML ({html_len} bytes) — fingerprint resolvido.")
                    return True

                # Fallback original
                if not self._state_is_challenge(st) and html_len > 500:
                    logger.info("Desafio resolvido automaticamente (fingerprint).")
                    return True

                progress_key = int(time.time() - passive_start) // 10
                if progress_key > passive_key:
                    passive_key = progress_key
                    logger.info(
                        f"Aguardando fingerprint... [{int(time.time() - passive_start)}s | "
                        f"titulo={(st.get('title') or '')[:40]!r} | html_len={html_len}]"
                    )
                time.sleep(1.0)

        _log_phase("FASE2 fim - fast-path check")

        # --- Fast-path pós-FASE 2: página já carregou sem Turnstile ---
        # Se o fingerprint resolveu e não há Turnstile, NÃO espere 10s à toa.
        st = self._page_state()
        html_len = st.get('htmlLen', 0)
        has_challenge_dom = st.get('challengeScript') or st.get('suspect')
        if html_len > 5000 and not has_challenge_dom:
            logger.info(f"Página real já carregada ({html_len} bytes) — pulando espera Turnstile (10s).")
            _log_phase("FASE2 fast-path return")
            return True

        # --- FASE 3: Tentativas ativas de clique/token ---
        _log_phase("FASE3 inicio")
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

        # --- Diagnóstico pré-interação (snapshot leve; NÃO serializa DOM) ---
        # Se o fingerprint já resolveu mas o título ainda mostra o desafio
        # (transição/redirect), evitamos clique desnecessário que dispara refresh.
        pre = self._page_state()
        pre_challenge = self._state_is_challenge(pre)
        pre_len = pre.get('htmlLen', 0)
        pre_token = pre.get('token') or ''
        logger.debug(f"Pré-interação: title={pre.get('title')!r}, html_len={pre_len}, challenge={pre_challenge}, token={bool(pre_token)}")

        if pre_token:
            logger.info("Token Turnstile já presente após fingerprint - desafio resolvido!")
            return True
        if pre_len > 5000 and not pre_challenge:
            logger.info(f"Página real já carregada ({pre_len} bytes) - evitando interação desnecessária.")
            return True

        for attempt in range(self.max_retries):
            logger.info(f"Tentativa de bypass {attempt + 1}/{self.max_retries}")

            if has_turnstile:
                token = self.get_turnstile_token()
                if token and self._wait_real_page(timeout=25.0):
                    logger.info("Desafio Turnstile resolvido!")
                    _log_phase("SUCESSO Turnstile")
                    return True
            else:
                self.click_verify()
                if self._wait_real_page(timeout=25.0):
                    logger.info("Desafio resolvido!")
                    _log_phase("SUCESSO click_verify")
                    return True

            if self.is_access_denied():
                _log_phase("FALHA access_denied")
                return False
            time.sleep(3)

        logger.error(f"Falha após {self.max_retries} tentativas.")
        _log_phase("FALHA max_retries")
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