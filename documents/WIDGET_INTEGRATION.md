# MATE Embeddable Chat Widget

Add an AI chat assistant to any website with a single `<script>` tag. The widget connects to your MATE instance (self-hosted, cloud, or localhost) and communicates with a specific root agent.

---

## Quick Start

### 1. Generate a Widget API Key

1. Open the **MATE Dashboard** → **Agent Management**
2. Select your project and find a **root agent**
3. Click the **`</>`** (Widget Keys) button in the agent's action row
4. Click **Generate Key**, optionally set a label and allowed origins
5. Click **Embed Code** to get the snippet

### 2. Add the Snippet to Your Website

Paste the embed code before the closing `</body>` tag:

```html
<script
  src="https://your-mate-instance.com/widget/mate-widget.js"
  data-key="wk_abc123..."
  data-server="https://your-mate-instance.com"
></script>
```

That's it. A floating chat button will appear on your page.

---

## Embed Code Options

All configuration is done via `data-*` attributes on the script tag:

| Attribute | Required | Default | Description |
|---|---|---|---|
| `data-key` | Yes | — | Widget API key (starts with `wk_`) |
| `data-server` | Yes | — | URL of your MATE instance (e.g. `https://mate.example.com` or `http://localhost:8000`) |
| `data-position` | No | `bottom-right` | Button position: `bottom-right` or `bottom-left` |
| `data-theme` | No | `auto` | Chat theme: `light`, `dark`, or `auto` (follows user's OS preference) |
| `data-button-color` | No | `#2563eb` | Custom CSS color for the floating button |
| `data-button-text` | No | — | Text to show on the button instead of the chat icon |
| `data-width` | No | `400` | Chat panel width in pixels |
| `data-height` | No | `600` | Chat panel height in pixels |

### Example with all options:

```html
<script
  src="https://mate.example.com/widget/mate-widget.js"
  data-key="wk_abc123..."
  data-server="https://mate.example.com"
  data-position="bottom-left"
  data-theme="dark"
  data-button-color="#10b981"
  data-button-text="Ask AI"
  data-width="420"
  data-height="550"
></script>
```

---

## JavaScript API

The widget exposes a global `window.MateWidget` object for programmatic control:

```javascript
// Open the chat panel
MateWidget.open();

// Close the chat panel
MateWidget.close();

// Toggle open/close
MateWidget.toggle();
```

### Example: Open chat on a custom button click

```html
<button onclick="MateWidget.open()">Chat with us</button>
```

---

## Admin Panel

Each widget key has an associated admin panel where you can manage the agent without accessing the full MATE dashboard.

### Access

```
https://your-mate-instance.com/widget/admin?key=wk_abc123...
```

The admin panel link is also available in the dashboard when viewing embed code.

### Features

| Tab | What you can do |
|---|---|
| **Agent Settings** | Edit the agent's instruction, model, and description |
| **Memory Blocks** | Create, edit, and delete memory blocks that provide dynamic context to the agent |
| **Files** | Upload and manage files in file search stores for RAG (Retrieval-Augmented Generation) |

---

## Widget API Key Management

### From the Dashboard

- Navigate to **Agent Management** → select a root agent → click the **`</>`** icon
- **Generate**: Create a new key with optional label and origin restrictions
- **Deactivate/Activate**: Temporarily disable a key without deleting it
- **Delete**: Permanently revoke a key

### From the API

```bash
# List keys for a project
curl -u admin:mate https://mate.example.com/dashboard/api/widget-keys?project_id=1

# Generate a new key
curl -u admin:mate -X POST https://mate.example.com/dashboard/api/widget-keys \
  -H "Content-Type: application/json" \
  -d '{"project_id": 1, "agent_name": "my_root_agent", "label": "Production"}'

# Deactivate a key
curl -u admin:mate -X PUT https://mate.example.com/dashboard/api/widget-keys/1 \
  -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# Delete a key
curl -u admin:mate -X DELETE https://mate.example.com/dashboard/api/widget-keys/1

# Get embed code
curl -u admin:mate https://mate.example.com/dashboard/api/widget-keys/1/embed-code
```

---

## Security

### Origin Restrictions

When generating a widget key, you can specify allowed origins. If set, the widget will only work on pages served from those origins. Leave blank to allow all origins.

**Recommended**: Always set allowed origins for production keys.

### Key Rotation

1. Generate a new key
2. Update the embed code on your site(s)
3. Deactivate or delete the old key

### User Scoping

Widget users are automatically scoped with a prefix (`widget_{key_id}_{user_id}`) to prevent conflicts with dashboard users. Each widget visitor gets a unique anonymous user ID stored in their browser's localStorage.

### CORS

The MATE auth server allows all origins by default (`Access-Control-Allow-Origin: *`). If you need stricter CORS, configure allowed origins both in the widget key and in the server's CORS middleware.

---

## Self-Hosted vs Cloud

The widget works identically with any MATE instance:

| Setup | `data-server` value |
|---|---|
| Local development | `http://localhost:8000` |
| Self-hosted | `https://mate.yourdomain.com` |
| MATE Cloud | `https://your-instance.mate.cloud` (or whatever your cloud URL is) |

The only requirement is that the `data-server` URL points to the MATE auth server (port 8000 by default).

### HTTPS Requirement

If your website is served over HTTPS, the MATE instance must also be HTTPS. Browsers block mixed content (HTTPS page loading HTTP iframe/scripts). For local development, HTTP is fine.

---

## Theming

The widget supports three theme modes:

- **`auto`** (default): Detects the user's OS dark/light mode preference
- **`light`**: Always use light theme
- **`dark`**: Always use dark theme

The parent page can also send a theme change message to the widget iframe:

```javascript
// Change widget theme programmatically
const iframe = document.getElementById('mate-widget-iframe');
iframe.contentWindow.postMessage({ type: 'mate-theme', theme: 'dark' }, '*');
```

### Custom Button Color

Use `data-button-color` to match your site's branding:

```html
<script src="..." data-button-color="#8b5cf6" ...></script>
```

---

## Chat Widget API Endpoints

These endpoints are used internally by the widget. They authenticate via the `X-Widget-Key` header or `key` query parameter.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/widget/chat?key=...` | Chat page (loaded in iframe) |
| POST | `/widget/api/chat` | SSE streaming chat (proxy to ADK) |
| GET | `/widget/api/config` | Widget configuration |
| GET | `/widget/admin?key=...` | Admin panel page |
| GET | `/widget/api/agent` | Get agent config |
| PUT | `/widget/api/agent` | Update agent config |
| GET | `/widget/api/memory-blocks` | List memory blocks |
| POST | `/widget/api/memory-blocks` | Create memory block |
| PUT | `/widget/api/memory-blocks/{id}` | Update memory block |
| DELETE | `/widget/api/memory-blocks/{id}` | Delete memory block |
| GET | `/widget/api/files` | List file stores and files |
| POST | `/widget/api/files/upload` | Upload file |
| DELETE | `/widget/api/files/{id}` | Delete file |

---

## Troubleshooting

### Widget button doesn't appear

- Check browser console for errors
- Verify `data-key` and `data-server` are correct
- Ensure the MATE auth server is running and accessible

### "Invalid widget key" error

- The key may be deactivated or deleted — check the dashboard
- Verify the key string matches exactly (no extra spaces)

### Chat doesn't respond

- Check that the ADK server is running (port 8001)
- Verify the agent name in the widget key matches an actual root agent
- Check MATE server logs for errors

### CORS errors

- If your site is HTTPS, ensure MATE is also HTTPS
- Check that the widget key's allowed origins include your site's origin
- The MATE auth server should have `Access-Control-Allow-Origin: *` by default

### Widget overlaps page content

- Adjust `data-position` to `bottom-left` if it conflicts with other elements
- Use `data-width` and `data-height` to resize the chat panel
