**Title:** MATE is now on Slack! Bring your Multi-Agent hierarchies, interactive Block Kit cards, and RBAC directly to your workspace (Discord up next!)

---

Running multi-agent hierarchies in production is hard. Getting them to interact where your team actually works (Slack) without building custom webhooks, web sockets, or message parsing layers is even harder. 

In this update, we’ve brought native **Slack Integration** to **MATE (Multi-Agent Tree Engine)**. You can now connect any root agent in your hierarchy directly to Slack in just a few minutes—completely configured from the MATE Dashboard with zero custom code or redeploys.

Here is how it works and what it brings to your workspace.

---

## ⚡ Zero-Code Setup via the Dashboard
You don't need to write custom handlers. Under MATE Dashboard → **Integrations**, you can spin up a new Slack connection by pasting:
1. Your Slack **Team ID**
2. **Bot User OAuth Token** (`xoxb-...`)
3. **Signing Secret** (to verify incoming request signatures)

Point your Slack app’s Event Subscriptions and Interactivity URLs to your MATE server, and you're ready to go.

## 🧵 Threaded Conversations & Direct Messages
Conversations map cleanly to Slack’s native structure:
* **Mention-only in channels:** The bot is quiet until you type `@YourBot question`. It replies by starting a thread or posting inside an existing thread. Each thread maps to a persistent MATE `session_id` so the agent maintains state across messages.
* **Direct Messages (DMs):** Open a DM with the bot and chat directly without needing mentions. Each DM is treated as one continuous, stateful conversation.

## 🎛️ From Rich Cards to Slack Block Kit
MATE agents support interactive UI cards (e.g. buttons for selecting options, custom templates). Instead of sending raw text wrappers or JSON strings to Slack, the integration layer **automatically translates MATE interactive cards into Slack Block Kit components**. 
* Buttons are rendered natively in Slack.
* When a user clicks a button (e.g. "🔍 Investigate" or "Approve"), Slack sends the interaction to MATE, which runs the agent with the selected value and posts the reply inline.

## 🔒 Governance: RBAC & Token Tracking in Slack
Your Slack users are mapped as MATE users (`user_id = slack_<slack_user_id>`). This means all of MATE’s enterprise governance applies natively:
* **RBAC:** If an agent in your hierarchy is restricted to `admin` or `manager` roles, Slack users without those roles will be blocked from triggering them.
* **Token Tracking:** Every message sent via Slack is tracked across prompt, response, thought, and tool tokens, giving you cost visibility per user and channel.

---

## 🔮 What’s Next: Discord Integration
We are planning a **Discord Integration** next! 

We want to bring the exact same developer experience to Discord:
* Mapping Discord threads and channels to stateful MATE sessions.
* Translating interactive UI components into native Discord Buttons and Select Menus.
* Applying the same RBAC role mapping and token budget constraints.

---

We'd love to hear your thoughts! How do you currently expose your agent hierarchies to your teams? Do you prefer Slack, Discord, or embedded web widgets? 

Check out the full setup guide in the [SLACK_INTEGRATION.md](https://github.com/antiv/mate/blob/main/documents/SLACK_INTEGRATION.md) document in our repo!
