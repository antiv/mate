"""
FastAPI WebSocket routes for the Live Interactive Browser view.
Allows real-time interaction with the headless browser on the server.
"""

import logging
import base64
import asyncio
import time
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from shared.utils.tools.browser_tools import browser_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/browser", tags=["Browser"])


@router.websocket("/interactive")
async def websocket_browser_interactive(
    websocket: WebSocket,
    session_id: Optional[str] = "default",
    user_id: Optional[str] = "default"
):
    """
    WebSocket endpoint for real-time interactive browser session.
    Streams page screenshot frames to the client and accepts input events (click, keyboard, scroll).
    """
    # Try to extract authenticated user_id from session if available
    try:
        user_session = websocket.session.get("user") if hasattr(websocket, "session") else None
        if user_session:
            user_id = user_session.get("user_id") or user_session.get("email") or user_id
    except Exception as e:
        logger.debug(f"Failed to read session inside websocket: {e}")

    await websocket.accept()
    logger.info(f"WebSocket browser connection established for user: {user_id}, session: {session_id}")

    try:
        # Fetch the active session browser instance
        session_browser = await browser_manager.get_session(user_id, session_id)
        # Verify browser can start / initialize
        _ = await session_browser.get_page()
    except Exception as e:
        logger.error(f"Failed to acquire browser page: {e}", exc_info=True)
        await websocket.send_json({"type": "error", "message": f"Browser initialization failed: {str(e)}"})
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Flag to keep track of active connection state
    is_active = True

    async def get_current_active_page():
        """Dynamically retrieves the current active non-closed page, favoring popups/last opened pages."""
        try:
            p = await session_browser.get_page()
            active_page = p
            if session_browser.context:
                pages = session_browser.context.pages
                if pages:
                    for candidate in reversed(pages):
                        if not candidate.is_closed():
                            active_page = candidate
                            break
            return active_page
        except Exception as err:
            logger.debug(f"Failed to resolve active page: {err}")
            return None

    async def screencast_loop():
        """Periodically captures and streams screenshot frames to the client."""
        nonlocal is_active
        last_url = ""
        
        while is_active:
            try:
                page = await get_current_active_page()
                if not page or page.is_closed():
                    await asyncio.sleep(0.5)
                    continue
                    
                # Take jpeg screenshot for compact frame sizes
                screenshot_bytes = await page.screenshot(type="jpeg", quality=65)
                base64_frame = base64.b64encode(screenshot_bytes).decode("utf-8")
                
                # Check for URL updates to sync input field
                current_url = page.url
                
                await websocket.send_json({
                    "type": "frame",
                    "data": base64_frame,
                    "url": current_url,
                    "title": await page.title()
                })
                
                # Dynamic poll rate: 250ms normally, 1s if URL hasn't changed (idle)
                await asyncio.sleep(0.35 if current_url != last_url else 0.7)
                last_url = current_url
                
            except WebSocketDisconnect:
                break
            except Exception as err:
                logger.debug(f"Screencast frame error: {err}")
                await asyncio.sleep(1.0)

    # Spawn screencasting in the background
    screencast_task = asyncio.create_task(screencast_loop())

    try:
        while is_active:
            # Receive input events from the client
            event = await websocket.receive_json()
            event_type = event.get("type")
            
            page = await get_current_active_page()
            if not page or page.is_closed():
                await websocket.send_json({"type": "error", "message": "Browser page is closed."})
                await asyncio.sleep(0.5)
                continue

            # Update session activity timestamp using wall clock time to match the cleanup loop
            session_browser.last_activity = time.time()

            try:
                if event_type == "click":
                    x = event.get("x")
                    y = event.get("y")
                    if x is not None and y is not None:
                        await page.mouse.click(x, y)
                        
                elif event_type == "type":
                    text = event.get("text", "")
                    await page.keyboard.type(text)
                    
                elif event_type == "keypress":
                    key = event.get("key", "")
                    if key:
                        await page.keyboard.press(key)
                        
                elif event_type == "scroll":
                    direction = event.get("direction", "down")
                    delta = 180 if direction == "down" else -180
                    await page.mouse.wheel(0, delta)
                    
                elif event_type == "navigate":
                    url = event.get("url", "")
                    if url:
                        if not url.startswith(("http://", "https://")):
                            url = "https://" + url
                        await page.goto(url, wait_until="load")

                elif event_type == "clear_site_data":
                    current_url = page.url
                    if current_url and current_url != "about:blank":
                        from urllib.parse import urlparse
                        parsed = urlparse(current_url)
                        domain = parsed.netloc
                        # Clear cookies for this domain and subdomains
                        if session_browser.context:
                            await session_browser.context.clear_cookies(domain=domain)
                            if domain.startswith("www."):
                                base_domain = domain[4:]
                                await session_browser.context.clear_cookies(domain=base_domain)
                                await session_browser.context.clear_cookies(domain="." + base_domain)
                            else:
                                await session_browser.context.clear_cookies(domain="." + domain)
                        # Clear local/session storage on the page
                        try:
                            await page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
                        except Exception:
                            pass
                        # Reload the page to apply changes
                        await page.reload()
                        await websocket.send_json({"type": "info", "message": f"Cleared cookies and local storage for {domain}."})

                elif event_type == "reset_profile":
                    import os
                    import shutil
                    # Close current session resources
                    await session_browser.close()
                    # Delete profile directory
                    if os.path.exists(session_browser.profile_dir):
                        shutil.rmtree(session_browser.profile_dir)
                    # Re-initialize the page (this automatically spawns a fresh profile)
                    _ = await session_browser.get_page()
                    await websocket.send_json({"type": "info", "message": "Browser profile has been completely reset."})
            except Exception as cmd_err:
                logger.warning(f"Error executing browser command {event_type}: {cmd_err}")
                try:
                    await websocket.send_json({"type": "error", "message": f"Command failed: {str(cmd_err)}"})
                except Exception:
                    pass

    except WebSocketDisconnect:
        logger.info("WebSocket browser connection closed by client.")
    except Exception as e:
        logger.error(f"WebSocket browser error: {e}", exc_info=True)
    finally:
        is_active = False
        screencast_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass
