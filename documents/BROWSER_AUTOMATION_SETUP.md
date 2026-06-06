# Browser Automation & Live Interactive View

This document provides setup, configuration, and architectural details for the native Python Browser Automation and Live Interactive View (screencasting) in MATE.

---

## Overview

MATE includes a native, Playwright-powered browser engine that allows agents to:
1. Surf the web for real-time research.
2. Automate user journeys (filling forms, clicking buttons, and interacting with pages).
3. Capture screenshots and save them as session artifacts.
4. Interact securely with authenticated sites (like LinkedIn or Reddit) via an **Interactive Live View** modal.

The system is designed with **multi-user session isolation** and can be deployed on headless remote servers or locally on developer machines.

---

## Key Architecture Components

### 1. Isolated User Profiles
To prevent session mix-ups and ensure total privacy, each user has a dedicated browser profile directory on the server:
`data/browser_profiles/user_{user_id}/`

When the browser is active, cookies, browser storage, and authenticated states are loaded and stored solely within this user-specific directory. This isolates User A's sessions from User B.

### 2. Multi-turn Page Persistence
Browser contexts are cached per `(user_id, session_id)` inside the `BrowserSessionManager` singleton. This ensures that:
* Page state, open tab, and session authentication are preserved across multiple consecutive agent tool calls.
* An idle cleanup thread automatically closes inactive browser instances after 10 minutes of inactivity to free system resources.

### 3. VNC-Style Screencasting & Interaction
For sites requiring multi-factor authentication (MFA), password entries, or CAPTCHAs, MATE features a **Live Browser View** in the dashboard.
* **WebSocket Endpoint (`/api/browser/interactive`)**: Creates a real-time connection to the Playwright browser context.
* **Canvas Projection**: Streams the browser viewport as optimized JPEG base64 frames to a `<canvas>` element in the dashboard modal.
* **Event Forwarding**: Translates user mouse clicks, scrolls, and key presses on the canvas into Playwright driver events (`page.mouse.click`, `page.keyboard.type`, etc.).
* **Security**: The user logs in directly on the original page. Passwords are never seen, processed, or saved by MATE.

---

## Exposed Browser Tools

Agents with the `browser` tool enabled have access to the following Python functions (registered via `ToolFactory`):

### 1. `browser_navigate(url)`
Navigates the browser to the specified absolute URL, waits for the page to load, takes an artifact screenshot, and returns a token-efficient text summary of the page content.

### 2. `browser_click(selector)`
Performs a mouse click on the element matching the CSS selector (e.g. `button#login`), waits for page updates, and returns the updated summary and a new screenshot.

### 3. `browser_fill_form(selector, value)`
Fills an input field matching the CSS selector (e.g. `input[name="username"]`) with the provided string value.

### 4. `browser_screenshot()`
Captures the current browser viewport screenshot and saves it as a MATE session artifact, returning a downloadable URL.

### 5. `browser_get_html()`
Returns the raw HTML source code of the active page.

---

## Deployment & Setup

### Requirements

Add the `playwright` package to your Python virtual environment:
```bash
pip install playwright>=1.44.0
```

### 1. Standard/Local Server Setup
Run the Playwright CLI to download the Chromium browser binaries:
```bash
python -m playwright install chromium
```

If running on a headless Linux server, install the required OS dependencies (requires root privileges):
```bash
python -m playwright install-deps
```

### 2. Docker Setup
Playwright is fully integrated into MATE's `Dockerfile`. The build script automatically downloads browser binaries and system packages during image creation:
```dockerfile
# Install Python dependencies
RUN uv pip install --no-cache-dir --system -r ./requirements.txt

# Install Playwright browser binaries and system dependencies (as root)
RUN python -m playwright install --with-deps chromium
```

---

## Configuration

To enable browser tools on a custom agent, add the `"browser"` key to the agent's `tool_config` JSON string in the database:

### Simple Enablement
```json
{
  "browser": true,
  "memory_blocks": true
}
```

### Environment Variables
You can configure the browser mode inside the `.env` file:

```bash
# Browser Mode: headless (default) | cdp
BROWSER_MODE=headless

# Run headless (true) or open visual browser window locally (false)
BROWSER_HEADLESS=true

# Remote CDP debugger URL (only if BROWSER_MODE=cdp)
BROWSER_CDP_URL=http://localhost:9222
```

---

## Usage Guide (End-User)

### How to Authenticate & Post (LinkedIn / Reddit)

1. Import the **Browser Research Assistant** template from the **Templates** tab in MATE Dashboard.
2. Go to the **Work Room** and select the imported agent.
3. Click the **Live Browser** button in the top chat header bar. A browser console modal will open.
4. Enter `linkedin.com` or `reddit.com` in the address bar and click **Go**.
5. Manually log in, entering your credentials and resolving any CAPTCHAs or MFA prompts in the live view.
6. Once logged in, close the modal.
7. Prompt your agent:
   > "Otvori LinkedIn, proveri najnoviji feed i napravi objavu o MATE agentima."
8. The agent will load your persistent authenticated session and post autonomously!
