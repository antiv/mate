"""
Native browser automation tools using Python Playwright (Async API).
Exposes tools for navigation, interaction, form-filling, and screenshots.
"""

import os
import json
import logging
import time
import asyncio
import hashlib
import ipaddress
import re
import socket
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

from google.adk.tools.tool_context import ToolContext
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


# The browser runs on the server, so without this an agent — steered by a web
# page or a chat message — or anyone driving the interactive view could reach the
# internal ADK server, the database host or cloud metadata. Checked for every
# request the browser makes, redirects and in-page fetches included.
_SAFE_SCHEMES = ("http", "https")
_PASSTHROUGH_SCHEMES = ("data", "blob", "about")


def _private_network_allowed() -> bool:
    return os.getenv("BROWSER_ALLOW_PRIVATE_NETWORK", "false").lower() in ("true", "1", "yes")


def _is_internal_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr.is_multicast or addr.is_unspecified)


async def url_block_reason(url: str) -> Optional[str]:
    """Why the browser may not load this URL, or None when it may."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in _PASSTHROUGH_SCHEMES:
        return None
    if scheme not in _SAFE_SCHEMES:
        return f"scheme '{scheme}' is not allowed"
    if _private_network_allowed():
        return None
    host = parsed.hostname
    if not host:
        return "no host"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror:
        return None  # unresolvable: the browser will fail to load it anyway
    for info in infos:
        if _is_internal_address(info[4][0]):
            return f"{host} resolves to an internal address"
    return None


async def _guard_route(route) -> None:
    reason = await url_block_reason(route.request.url)
    if reason:
        logger.warning("Browser blocked %s: %s", route.request.url[:200], reason)
        await route.abort("blockedbyclient")
    else:
        await route.continue_()


def _profile_dir_name(user_id: str) -> str:
    """
    A directory name for the user's browser profile. Widget visitor ids come from
    the client, so one containing a path separator or '..' is hashed rather than
    joined into the path. Plain ids keep their old directory and saved logins.
    """
    if re.fullmatch(r"[A-Za-z0-9_@.+-]+", user_id or "") and ".." not in user_id:
        return f"user_{user_id}"
    return "user_" + hashlib.sha256((user_id or "").encode("utf-8")).hexdigest()[:32]


class SessionBrowser:
    """Manages the lifetime and context of a single user's browser session (Async)."""
    
    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.last_activity = time.time()
        self.profile_dir = os.path.abspath(os.path.join("data/browser_profiles", _profile_dir_name(user_id)))

    async def get_page(self) -> Page:
        """Acquires and initializes the active Playwright page, launching the browser if needed."""
        self.last_activity = time.time()
        
        if self.page and not self.page.is_closed():
            try:
                # Check connection status
                _ = self.page.url
                return self.page
            except Exception:
                logger.warning("Cached page connection is stale. Re-initializing browser.")
                await self.close()

        # Ensure profile directory exists
        os.makedirs(self.profile_dir, exist_ok=True)

        logger.info(f"Initializing browser for user {self.user_id}, session {self.session_id}")
        self.playwright = await async_playwright().start()
        
        # Check BROWSER_MODE
        browser_mode = os.getenv("BROWSER_MODE", "headless").lower()
        
        if browser_mode == "cdp":
            cdp_url = os.getenv("BROWSER_CDP_URL", "http://localhost:9222")
            logger.info(f"Connecting to browser via CDP URL: {cdp_url}")
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                if self.browser.contexts:
                    self.context = self.browser.contexts[0]
                else:
                    self.context = await self.browser.new_context()
                
                if self.context.pages:
                    self.page = self.context.pages[0]
                else:
                    self.page = await self.context.new_page()
            except Exception as e:
                logger.error(f"CDP connection failed: {e}. Falling back to headless launch.")
                browser_mode = "headless"

        if browser_mode != "cdp":
            # Native launch (headless or local persistent context)
            headless_env = os.getenv("BROWSER_HEADLESS", "true").lower() in ("true", "1", "yes")
            logger.info(f"Launching persistent Chromium. Headless: {headless_env}, Profile: {self.profile_dir}")
            
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=headless_env,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            
            # Load cookies if they exist in user's profile directory
            cookies_file = os.path.join(self.profile_dir, "cookies.json")
            if os.path.exists(cookies_file):
                try:
                    with open(cookies_file, "r") as f:
                        cookies = json.load(f)
                        await self.context.add_cookies(cookies)
                        logger.info(f"Loaded {len(cookies)} cookies from profile directory")
                except Exception as e:
                    logger.warning(f"Could not load cookies: {e}")

            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = await self.context.new_page()

        await self.context.route("**/*", _guard_route)

        # Set default timeouts
        self.page.set_default_timeout(30000)  # 30 seconds
        return self.page

    async def close(self):
        """Cleanly releases all browser and Playwright resources."""
        logger.info(f"Closing browser session resources for user {self.user_id}")
        if self.page:
            try:
                await self.page.close()
            except Exception:
                pass
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None


class BrowserSessionManager:
    """Manages multiple SessionBrowser instances dynamically per user and session (Async)."""
    
    def __init__(self):
        self._sessions: Dict[Tuple[str, str], SessionBrowser] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task_started = False

    async def get_session(self, user_id: str, session_id: str) -> SessionBrowser:
        async with self._lock:
            # Start background task dynamically in the active event loop
            if not self._cleanup_task_started:
                try:
                    asyncio.create_task(self._cleanup_loop())
                    self._cleanup_task_started = True
                except Exception as e:
                    logger.debug(f"Failed to start browser cleanup loop task: {e}")
            
            key = (user_id, session_id)
            if key not in self._sessions:
                self._sessions[key] = SessionBrowser(user_id, session_id)
            return self._sessions[key]

    async def close_session(self, user_id: str, session_id: str):
        async with self._lock:
            key = (user_id, session_id)
            if key in self._sessions:
                await self._sessions[key].close()
                del self._sessions[key]

    async def _cleanup_loop(self):
        """Periodically cleans up idle browser sessions to free server memory."""
        while True:
            await asyncio.sleep(60)  # Run check every minute
            now = time.time()
            expired_keys = []
            async with self._lock:
                for key, session in list(self._sessions.items()):
                    # Idle timeout: 10 minutes
                    if now - session.last_activity > 600:
                        expired_keys.append(key)
                for key in expired_keys:
                    try:
                        logger.info(f"Releasing idle browser session for user {key[0]} / session {key[1]}")
                        if key in self._sessions:
                            await self._sessions[key].close()
                            del self._sessions[key]
                    except Exception as e:
                        logger.error(f"Error cleaning up idle browser session {key}: {e}")


# Global instance
browser_manager = BrowserSessionManager()


def _get_context_values(tool_context: ToolContext) -> Tuple[str, str, str]:
    """Extract app_name, user_id, and session_id from ToolContext."""
    if not tool_context:
        return 'unknown', 'default', 'default'
        
    app_name = getattr(tool_context, 'app_name', None)
    if not app_name and hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        app_name = getattr(tool_context._invocation_context, 'app_name', None)
    if not app_name:
        app_name = 'unknown'
    
    user_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        user_id = getattr(tool_context._invocation_context, 'user_id', None)
    if not user_id:
        user_id = getattr(tool_context, 'user_id', None)
    if not user_id and hasattr(tool_context, 'session') and tool_context.session:
        user_id = getattr(tool_context.session, 'user_id', None)
    if not user_id:
        user_id = 'default'
    
    session_id = None
    if hasattr(tool_context, '_invocation_context') and tool_context._invocation_context:
        if hasattr(tool_context._invocation_context, 'session') and tool_context._invocation_context.session:
            session_id = getattr(tool_context._invocation_context.session, 'id', None)
    if not session_id:
        session_id = getattr(tool_context, 'session_id', None)
    if not session_id:
        session_id = 'default'
    
    return app_name, user_id, session_id


async def _save_screenshot_artifact(page: Page, tool_context: ToolContext) -> Optional[Dict[str, Any]]:
    """Capture page screenshot and save it securely as a MATE session artifact."""
    if not tool_context:
        return None
    try:
        screenshot_bytes = await page.screenshot(type="png")
        
        # Save as artifact via ToolContext
        from google.genai import types
        part = types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
        
        timestamp = int(time.time())
        filename = f"browser_screenshot_{timestamp}.png"
        saved_version = await tool_context.save_artifact(filename, part)
                
        app_name, user_id, session_id = _get_context_values(tool_context)
        artifact_path = f"/apps/{app_name}/users/{user_id}/sessions/{session_id}/artifacts/{filename}/versions/{saved_version}"
        
        # Generate absolute URL if artifact services are configured
        from .image_tools import _construct_public_url
        public_url = _construct_public_url(app_name, user_id, session_id, filename, saved_version)
        
        # Fallback relative widget path if no cloud storage config
        if not public_url:
            public_url = f"/api/widget/artifacts/{app_name}/{user_id}/{session_id}/{filename}/{saved_version}"
            
        return {
            "filename": filename,
            "version": saved_version,
            "artifact_path": artifact_path,
            "public_url": public_url
        }
    except Exception as e:
        logger.warning(f"Could not capture and save browser screenshot: {e}", exc_info=True)
        return None


async def _generate_page_summary(page: Page, screenshot_info: Optional[dict] = None) -> str:
    """Generate a clean, token-efficient text summary of the current page's inner content."""
    try:
        title = await page.title()
    except Exception:
        title = "Unknown"
        
    try:
        url = page.url
    except Exception:
        url = "Unknown"
        
    try:
        # Extract text content while removing styles, scripts, SVGs, and navigation footers
        text = await page.evaluate("""() => {
            const el = document.body;
            if (!el) return '';
            const clone = el.cloneNode(true);
            const toRemove = clone.querySelectorAll('script, style, iframe, noscript, svg, nav, footer');
            toRemove.forEach(n => n.remove());
            return clone.innerText || clone.textContent || '';
        }""")
        
        # Strip redundant white spaces
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        cleaned_text = '\n'.join(lines)
        
        if len(cleaned_text) > 6000:
            cleaned_text = cleaned_text[:6000] + "\n... [Content truncated to fit agent token context limit] ..."
    except Exception as e:
        cleaned_text = f"Error extracting page text content: {e}"
        
    summary = f"Title: {title}\nURL: {url}\n\nContent:\n{cleaned_text}\n"
    
    if screenshot_info:
        summary += f"\nScreenshot captured: {screenshot_info['filename']} (view at: {screenshot_info['public_url']})"
        
    return summary


# ---------- Tool Functions Exposed to Agents (Async) ----------

async def browser_navigate(url: str, tool_context: ToolContext = None) -> str:
    """
    Navigate the browser to the specified URL.
    
    Loads the web page, waits for it to render, captures a screenshot, and extracts its text contents.
    Use this tool to open any webpage for research, form filling, or verification.
    
    Args:
        url: The absolute target URL to navigate to (e.g. 'https://www.google.com').
        tool_context: The invocation context (automatically injected by ADK).
        
    Returns:
        A structured text summary of the loaded page and a screenshot download URL.
    """
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            
        app_name, user_id, session_id = _get_context_values(tool_context)
        session_browser = await browser_manager.get_session(user_id, session_id)
        page = await session_browser.get_page()
        
        logger.info(f"Browser navigating to: {url}")
        await page.goto(url, wait_until="load")
        await page.wait_for_timeout(1000)  # Short pause for animations/dynamic components
        
        screenshot_info = await _save_screenshot_artifact(page, tool_context)
        return await _generate_page_summary(page, screenshot_info)
        
    except Exception as e:
        logger.error(f"Failed to navigate to {url}: {e}", exc_info=True)
        return f"Error: Failed to navigate to {url}. Details: {str(e)}"


async def browser_click(selector: str, tool_context: ToolContext = None) -> str:
    """
    Click on an element matching the given CSS selector.
    
    Clicks a button, link, or input field on the current page, waits for navigation/rendering,
    and returns the updated page summary.
    
    Args:
        selector: The CSS selector of the element to click (e.g. 'button#submit', 'a.next-page', 'input[type="checkbox"]').
        tool_context: The invocation context (automatically injected by ADK).
        
    Returns:
        The updated page text summary and a new screenshot URL.
    """
    try:
        app_name, user_id, session_id = _get_context_values(tool_context)
        session_browser = await browser_manager.get_session(user_id, session_id)
        page = await session_browser.get_page()
        
        logger.info(f"Browser clicking element: {selector}")
        await page.click(selector)
        await page.wait_for_timeout(1500)  # Wait for page updates or navigation
        
        screenshot_info = await _save_screenshot_artifact(page, tool_context)
        return await _generate_page_summary(page, screenshot_info)
        
    except Exception as e:
        logger.error(f"Failed to click element '{selector}': {e}", exc_info=True)
        return f"Error: Failed to click element '{selector}'. Details: {str(e)}"


async def browser_fill_form(selector: str, value: str, tool_context: ToolContext = None) -> str:
    """
    Type text value into a form input matching the given CSS selector.
    
    Args:
        selector: The CSS selector of the input field to type into (e.g. 'input#username', 'textarea[name="comment"]').
        value: The string content to fill into the input field.
        tool_context: The invocation context (automatically injected by ADK).
        
    Returns:
        A confirmation message and the current page text summary with a new screenshot.
    """
    try:
        app_name, user_id, session_id = _get_context_values(tool_context)
        session_browser = await browser_manager.get_session(user_id, session_id)
        page = await session_browser.get_page()
        
        logger.info(f"Browser filling input '{selector}' with value length: {len(value)}")
        await page.fill(selector, value)
        await page.wait_for_timeout(500)
        
        screenshot_info = await _save_screenshot_artifact(page, tool_context)
        return f"Successfully filled '{selector}'.\n\n" + await _generate_page_summary(page, screenshot_info)
        
    except Exception as e:
        logger.error(f"Failed to fill input '{selector}': {e}", exc_info=True)
        return f"Error: Failed to fill input '{selector}'. Details: {str(e)}"


async def browser_screenshot(tool_context: ToolContext = None) -> str:
    """
    Capture a screenshot of the current page viewport.
    
    Args:
        tool_context: The invocation context (automatically injected by ADK).
        
    Returns:
        Information about the captured screenshot artifact, including its URL.
    """
    try:
        app_name, user_id, session_id = _get_context_values(tool_context)
        session_browser = await browser_manager.get_session(user_id, session_id)
        page = await session_browser.get_page()
        
        screenshot_info = await _save_screenshot_artifact(page, tool_context)
        if screenshot_info:
            return f"Screenshot captured successfully: {screenshot_info['filename']}\nView URL: {screenshot_info['public_url']}"
        return "Error: Failed to capture screenshot"
        
    except Exception as e:
        logger.error(f"Failed to capture browser screenshot: {e}", exc_info=True)
        return f"Error capturing screenshot: {str(e)}"


async def browser_get_html(tool_context: ToolContext = None) -> str:
    """
    Retrieve the raw HTML markup of the active web page.
    
    Args:
        tool_context: The invocation context (automatically injected by ADK).
        
    Returns:
        The raw HTML source code of the current page (caution: might be large).
    """
    try:
        app_name, user_id, session_id = _get_context_values(tool_context)
        session_browser = await browser_manager.get_session(user_id, session_id)
        page = await session_browser.get_page()
        
        return await page.content()
        
    except Exception as e:
        logger.error(f"Failed to retrieve page HTML content: {e}", exc_info=True)
        return f"Error retrieving HTML source: {str(e)}"


# ---------- Tool Config registration ----------

def create_browser_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create browser automation tools if enabled in the agent's tool config.
    
    Args:
        config: Agent configuration dictionary.
        
    Returns:
        List of browser tool functions.
    """
    tools = []
    agent_name = config.get('name', 'unknown')
    tool_config = config.get('tool_config')
    
    if tool_config:
        try:
            if isinstance(tool_config, str):
                tool_config_dict = json.loads(tool_config)
            else:
                tool_config_dict = tool_config
                
            browser_cfg = tool_config_dict.get('browser')
            enabled = False
            if isinstance(browser_cfg, bool):
                enabled = browser_cfg
            elif isinstance(browser_cfg, dict):
                enabled = browser_cfg.get('enabled', True)
                
            if enabled:
                tools.extend([
                    browser_navigate,
                    browser_click,
                    browser_fill_form,
                    browser_screenshot,
                    browser_get_html
                ])
                logger.info(f"Registered Playwright browser tools for agent '{agent_name}'")
                
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in tool_config for agent '{agent_name}'")
        except Exception as e:
            logger.error(f"Error registering browser tools for agent '{agent_name}': {e}", exc_info=True)
            
    return tools
