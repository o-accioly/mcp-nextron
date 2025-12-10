from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Any, List

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import inspect
import atexit
import logging
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page, TimeoutError as PWTimeout
from mcp.server.transport_security import TransportSecuritySettings


# Carregar variáveis de ambiente (.env)
load_dotenv()

# ==========================
# Logging
# ==========================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("nextron-mcp")

# Debug: Verificar variáveis de ambiente
_email = os.getenv("EMAIL")
_password = os.getenv("PASSWORD")
if _email:
    logger.info("EMAIL configurado: %s", _email)
else:
    logger.warning("EMAIL não encontrado nas variáveis de ambiente!")

if _password:
    logger.info("PASSWORD configurado: [MASKED] (len=%d)", len(_password))
else:
    logger.warning("PASSWORD não encontrado nas variáveis de ambiente!")

BASE_URL = "https://connect.nextron.ai/"

#
# Instruções/seletores úteis (fornecidos pelo usuário):
#
# Pagina de login
#  - Email: input[name="email"]
#  - Senha: input[name="password"]
#  - Entrar: button[type="submit"]
#
# Pagina de cadastro de cliente: BASE_URL + hub/sales/onboardings/save
#  - Etapa 1.1: Cadastrar contato
#  - Distribuidora: #mui-41844491 (pode variar — tentar por role/texto como fallback)
#  - Nome completo: input[name="contact_name"]
#  - Email: input[name="email"]
#  - Telefone: input[name="telephone"]
#  - Gerar proposta (abrir modal): botão com texto "Gerar proposta"
#  - No popup: Valor da conta de luz: input[name="average_consumption_estimate_in_brl"]
#  - Botão de submit com texto "Gerar Proposta"
#  - Snackbar de erro: .MuiSnackbar-root (aguardar 5s e capturar texto se presente)
#  - Sucesso: URL permanece em hub/sales/onboardings/save
#
# Pagina de clientes: BASE_URL + sales/onboardings
#  - Abrir filtros: button[aria-label="Exibir filtros"]
#  - Selecionar filtro por coluna: combobox -> option "email"
#  - Preencher filtro: input[placeholder='Filtrar valor']
#  - Aguardar 5 segundos
#  - Linhas: .MuiDataGrid-virtualScrollerRenderZone div.MuiDataGrid-row
#  - Cada linha contém colunas: Nome|Email|Status|Distribuidora|Origem|Criado em|Atualizado em
#  - data-id na div da linha => link: BASE_URL + sales/onboardings/{data-id}


# ==========================
# Infra MCP e navegação web
# ==========================


mcp = FastMCP(
    "nextron-mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)
logger.info("Inicializando MCP server 'nextron-mcp'")

# Compat: some FastMCP versions don't provide `on_shutdown`. Provide a fallback
# decorator that registers the given handler to run on process exit.
if not hasattr(mcp, "on_shutdown"):
    def _fallback_on_shutdown(func=None):
        def _register(handler):
            if inspect.iscoroutinefunction(handler):
                def _runner():
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            try:
                                loop.create_task(handler())
                            except Exception:
                                pass
                        else:
                            loop.run_until_complete(handler())
                    except Exception:
                        # Best-effort during shutdown
                        pass
                atexit.register(_runner)
            else:
                atexit.register(lambda: (
                    handler()
                ))
            return handler

        # Support both @mcp.on_shutdown and @mcp.on_shutdown()
        return _register if func is None else _register(func)

    # Monkey-patch the instance to avoid AttributeError at import time
    setattr(mcp, "on_shutdown", _fallback_on_shutdown)

# Alias defensivo: tratar typo on_shotdown -> on_shutdown, se alguém usar
if not hasattr(mcp, "on_shotdown"):
    try:
        setattr(mcp, "on_shotdown", getattr(mcp, "on_shutdown"))
    except Exception:
        pass

@dataclass
class Session:
    context: BrowserContext
    page: Page
    lock: asyncio.Lock
    email: Optional[str] = None


class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._global_lock = asyncio.Lock()

    async def _ensure_browser(self) -> None:
        if self._browser:
            return

        async with self._global_lock:
            if self._browser:
                return

            logger.info("Iniciando Playwright")
            self._playwright = await async_playwright().start()

            # Chromium é normalmente mais estável para sites modernos
            logger.info("Lançando navegador Chromium (headless)")
            self._browser = await self._playwright.chromium.launch(headless=True, args=["--no-sandbox"])

    async def new_session(self) -> str:
        await self._ensure_browser()

        assert self._browser is not None

        context = await self._browser.new_context()
        page = await context.new_page()

        sid = uuid.uuid4().hex
        self._sessions[sid] = Session(context=context, page=page, lock=asyncio.Lock())
        logger.info("Sessão criada: %s", sid)

        return sid

    def get(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            raise ValueError("session_id inválido. Crie uma nova sessão com new_session ou forneça um válido.")
        return self._sessions[session_id]

    async def close(self, session_id: str) -> bool:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            logger.warning("Tentativa de fechar sessão inexistente: %s", session_id)
            return False
        logger.info("Fechando sessão: %s", session_id)
        try:
            await sess.page.close()
        except Exception:
            pass
        try:
            await sess.context.close()
        except Exception:
            pass
        return True

    async def shutdown(self) -> None:
        logger.info("Shutdown iniciado: fechando %d sessão(ões)", len(self._sessions))
        for sid in list(self._sessions.keys()):
            try:
                await self.close(sid)
            except Exception:
                pass
        if self._browser:
            try:
                logger.info("Fechando navegador")
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                logger.info("Parando Playwright")
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


SESSIONS = SessionManager()


async def ensure_logged_in(sess: Session) -> None:
    # Pega credenciais do .env se não fornecidas
    user = os.getenv("EMAIL")
    pwd = os.getenv("PASSWORD")

    logger.info("Verificando login com credenciais: %s", user)

    if not user or not pwd:
        raise ValueError("Credenciais não encontradas. Defina EMAIL e PASSWORD no .env ou envie via parâmetros.")

    page = sess.page
    # Ir para uma página protegida para verificar se redireciona para login
    PROPOSAL_URL = f"{BASE_URL}hub/sales/onboardings/save"
    logger.info("Verificando autenticação via acesso a: %s", PROPOSAL_URL)
    await page.goto(PROPOSAL_URL, wait_until="domcontentloaded")

    if page.url != PROPOSAL_URL:
        logger.info("Redirecionado para login (URL: %s). Iniciando processo de autenticação.", page.url)

        # Preenche formulário de login
        logger.info("Realizando login para usuário %s", user)

        await page.wait_for_selector('input[name="email"]', timeout=20_000)
        await page.fill('input[name="email"]', user)
        await page.fill('input[name="password"]', pwd)
        await page.click('button[type="submit"]')

    # Aguarda redirecionamento pós-login
    try:
        await page.wait_for_url(lambda url: url.startswith(BASE_URL) and "login" not in url, timeout=30_000)
    except PWTimeout:
        # Em alguns casos a app mantém a mesma URL base; validar presença de um elemento comum pós-login
        # Se falhar, lança erro claro
        logger.error("Falha no login: timeout aguardando redirecionamento")
        raise RuntimeError("Falha no login: timeout aguardando redirecionamento")

    sess.email = user


async def gerar_proposta_impl(
    sess: Session,
    distribuidora: Optional[str],
    nome_completo: str,
    email: str,
    telefone: str,
    valor_conta_brl: float,
) -> Dict[str, Any]:
    page = sess.page

    logger.info("URL atual: %s", page.url)
    logger.info("Acessando página de cadastro de cliente")
    logger.info("URL de cadastro de cliente: %s", f"{BASE_URL}hub/sales/onboardings/save")
    
    # Abrir página de cadastro de cliente
    await page.goto(f"{BASE_URL}hub/sales/onboardings/save")
    
    # Preencher dados básicos
    logger.info("Preenchendo dados básicos")
    logger.info("URL atual: %s", page.url)

    await page.fill('input[name="contact_name"]', nome_completo)
    logger.info("Nome completo: %s", nome_completo)

    await page.fill('input[name="email"]', email)
    logger.info("Email: %s", email)

    await page.fill('input[name="telephone"]', telefone)
    logger.info("Telefone: %s", telefone)

    # Distribuidora (o ID pode variar; tentar seletor direto e fallback por combobox)
    if distribuidora:
        try:
            await page.fill('#mui-41844491', distribuidora)
        except Exception:
            # Fallback: tentar por role combobox e digitar
            try:
                combo = page.get_by_role("combobox").nth(0)
                await combo.click()
                await combo.fill(distribuidora)
                # Se houver opção por texto
                await page.locator(f"text={distribuidora}").first.click(timeout=5_000)
            except Exception:
                pass

    # Abrir modal de gerar proposta
    logger.info("Abrindo modal 'Gerar proposta'")
    await page.get_by_role("button", name=lambda n: n and "Gerar proposta" in n).click()

    # Aguardar input no popup e preencher valor da conta de luz
    await page.wait_for_selector('input[name="average_consumption_estimate_in_brl"]', timeout=20_000)
    await page.fill('input[name="average_consumption_estimate_in_brl"]', str(valor_conta_brl))

    # Encontrar botão submit com texto "Gerar Proposta"
    # Procurar buttons type=submit e filtrar por texto
    buttons = page.locator('button[type="submit"]').filter(has_text="Gerar Proposta")
    if await buttons.count() == 0:
        # Fallback por nome de role
        buttons = page.get_by_role("button", name=lambda n: n and "Gerar Proposta" in n)
    await buttons.first.click()
    logger.info("Botão 'Gerar Proposta' clicado, aguardando snackbar/resultado")

    # Aguardar 5s e verificar snackbar de erro
    await page.wait_for_timeout(5_000)
    snackbar = page.locator('.MuiSnackbar-root')
    if await snackbar.count() > 0:
        txt = (await snackbar.inner_text()).strip()
        logger.warning("Snackbar de erro durante gerar proposta: %s", txt)
        return {"ok": False, "mensagem": txt}

    # Verificar URL de sucesso
    if "hub/sales/onboardings/save" in page.url:
        logger.info("Proposta criada com sucesso")
        return {"ok": True, "mensagem": "Proposta criada com sucesso", "url": page.url}
    else:
        logger.warning("URL inesperada após gerar proposta: %s", page.url)
        return {"ok": False, "mensagem": "URL inesperada após gerar proposta", "url": page.url}


async def buscar_cliente_impl(sess: Session, email: str) -> Dict[str, Any]:
    page = sess.page
    logger.info("Abrindo página de clientes para buscar email=%s", email)
    await page.goto(f"{BASE_URL}sales/onboardings", wait_until="domcontentloaded")

    # Abrir filtros
    await page.click('button[aria-label="Exibir filtros"]')

    # Selecionar coluna email
    try:
        await page.get_by_role("combobox").select_option("email")
    except Exception:
        # Fallback: tentar clicar e selecionar por texto
        combo = page.get_by_role("combobox").first
        await combo.click()
        await page.locator("text=email").first.click()

    # Preencher valor do filtro
    await page.fill("input[placeholder='Filtrar valor']", email)

    # Aguardar processamento
    await page.wait_for_timeout(5_000)

    rows = page.locator('.MuiDataGrid-virtualScrollerRenderZone div.MuiDataGrid-row')
    count = await rows.count()
    logger.info("Busca retornou %d linha(s)", count)
    resultados: List[Dict[str, Any]] = []
    for i in range(count):
        row = rows.nth(i)
        data_id = await row.get_attribute('data-id')
        # Obter texto completo e tentar separar colunas
        txt = (await row.inner_text()).strip()
        # Heurística: separar por nova linha e/ou pipe visual
        cols = [c.strip() for c in txt.replace("\n", "|").split("|") if c.strip()]
        registro: Dict[str, Any] = {"raw": txt, "data_id": data_id, "link": f"{BASE_URL}sales/onboardings/{data_id}" if data_id else None}
        # Mapear colunas se possível
        keys = ["nome", "email", "status", "distribuidora", "origem", "criado_em", "atualizado_em"]
        for idx, key in enumerate(keys):
            if idx < len(cols):
                registro[key] = cols[idx]
        resultados.append(registro)

    return {"ok": True, "total": len(resultados), "resultados": resultados}


# ==========================
# Definição das Tools MCP
# ==========================


@mcp.tool()
async def new_session() -> dict:
    """Cria uma nova sessão/instância de navegação isolada para operações paralelas.

    Retorna um objeto com `session_id` que deve ser usado nas outras tools.
    """
    sid = await SESSIONS.new_session()
    logger.info("Tool new_session -> %s", sid)
    return {"session_id": sid}


@mcp.tool()
async def close_session(session_id: str) -> dict:
    """Fecha e limpa a sessão indicada."""
    ok = await SESSIONS.close(session_id)
    logger.info("Tool close_session(session_id=%s) -> ok=%s", session_id, ok)
    return {"ok": ok}

@mcp.tool()
async def gerar_proposta(
    session_id: str,
    nome_completo: str,
    email: str,
    telefone: str,
    valor_conta_brl: str,
    distribuidora: str = "",
) -> dict:
    """Cria uma proposta para um cliente na página de onboarding.

    Parâmetros:
      - session_id: ID da sessão criada por `new_session`
      - nome_completo, email, telefone: dados do cliente
      - valor_conta_brl: valor médio da conta de luz (BRL) - envie como string ex "150.00"
      - distribuidora: opcional, tenta preencher o campo de distribuidora
    """

    try:
        val_float = float(valor_conta_brl)
    except ValueError:
        return {"ok": False, "mensagem": f"Valor da conta inválido: {valor_conta_brl}"}

    sess = SESSIONS.get(session_id)

    async with sess.lock:
        await ensure_logged_in(sess)

        logger.info(
            "Tool gerar_proposta(session_id=%s, nome=%s, email=%s, telefone=%s, valor=%.2f, distribuidora=%s)",
            session_id, nome_completo, email, telefone, val_float, distribuidora,
        )

        return await gerar_proposta_impl(sess, distribuidora or None, nome_completo, email, telefone, val_float)


@mcp.tool()
async def buscar_cliente(session_id: str, email: str) -> dict:
    """Busca clientes pelo email na listagem e retorna linhas encontradas."""
    sess = SESSIONS.get(session_id)
    async with sess.lock:
        await ensure_logged_in(sess)
        logger.info("Tool buscar_cliente(session_id=%s, email=%s)", session_id, email)
        return await buscar_cliente_impl(sess, email)


@mcp.tool()
async def health() -> dict:
    """Retorna status do servidor MCP."""
    return {"status": "ok"}


# ==========================
# Inicialização/Encerramento
# ==========================

import atexit
import signal


def _shutdown_sync() -> None:
    """Tenta encerrar sessões/playwright de forma graciosa ao finalizar o processo."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Se o loop estiver rodando, não podemos bloquear aqui com await.
            # Fazemos uma melhor tentativa sem interromper: cria uma tarefa fire-and-forget.
            try:
                loop.create_task(SESSIONS.shutdown())
            except Exception:
                pass
        else:
            loop.run_until_complete(SESSIONS.shutdown())
    except Exception:
        # Em shutdown é aceitável falhar silenciosamente
        pass


def _handle_signal(signum, frame):
    try:
        logger.info("Sinal recebido: %s. Iniciando shutdown...", signum)
        _shutdown_sync()
    finally:
        # Encerrar o processo após tentar cleanup
        try:
            signal.signal(signum, signal.SIG_DFL)
        except Exception:
            pass


# Registrar handlers de encerramento
atexit.register(_shutdown_sync)
try:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
except Exception:
    # Pode falhar em ambientes que não suportam signals (ex.: Windows antigo)
    pass


def main() -> None:
    # Executa o servidor MCP via stdio ou SSE dependendo da configuração
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "sse":
        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_PORT", "8000"))
        mcp.settings.host = host
        mcp.settings.port = port
        logger.info("Iniciando loop MCP (SSE) em %s:%s", host, port)
        mcp.run(transport="sse")
    else:
        logger.info("Iniciando loop MCP (stdio)")
        mcp.run()


if __name__ == "__main__":
    main()