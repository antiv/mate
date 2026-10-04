---
title: Slack
summary: Let people talk to an agent by mentioning it in a Slack channel or messaging it directly.
audience: user
order: 95
covers:
  - server/slack_routes.py
  - templates/dashboard/integrations.html
---

# Slack

Connect an agent to a Slack workspace and people can ask it questions by mentioning
it in a channel or by sending it a direct message. Integrations are in
**Studio → Integrations**. Slack is the platform available today; the others in the
list are marked as coming soon.

You need a MATE address that Slack can reach from the internet, and three values
from Slack: the **Team ID**, a **Bot User OAuth Token** and the **Signing Secret**.

## 1. Create the Slack app

1. At <https://api.slack.com/apps>, choose **Create New App → From scratch** and
   pick your workspace.
2. On **Basic Information**, copy the **Signing Secret**.
3. On **OAuth & Permissions**, under **Bot Token Scopes**, add `app_mentions:read`
   and `chat:write`.
4. Click **Install to Workspace**, then copy the **Bot User OAuth Token**. It starts
   with `xoxb-`.
5. Find the **Team ID** of your workspace. It starts with `T`.

## 2. Create the integration in MATE

1. Check the agent's **Allowed Roles** first. Slack users are given the `user` role,
   so the list must include `user`.
2. On the Integrations page, copy the **Slack Event Request URL** shown at the top.
3. Click **New Integration**, choose **Slack**, the project and the root agent, and
   enter the Team ID, the bot token and the signing secret.
4. Leave **Active** ticked and click **Create**.

When you edit the integration later, leave the two secret fields empty to keep the
stored values.

## 3. Point Slack at MATE

1. In the Slack app, open **Event Subscriptions** and switch **Enable Events** on.
2. Paste the Request URL. Slack checks it immediately and shows **Verified**; MATE
   has to be running and reachable for this.
3. Under **Subscribe to bot events**, add `app_mention`.
4. Save, and reinstall the app if Slack asks.

## 4. Try it

Invite the bot to a channel with `/invite @YourBot`, then write
`@YourBot hello`. The agent answers in a thread under your message.

## How conversations work

- In a channel, the agent answers only when it is mentioned. Each Slack thread is one
  conversation: mention the bot again in the same thread and it remembers what was
  said there.
- Each Slack user is a separate MATE user, so usage and roles are tracked per
  person. They appear on the **Users** page with ids starting `slack_`.
- The **Mention-only** box is saved with the integration but does not currently
  change this behaviour.

## Direct messages

To let people message the bot privately, change three things in the Slack app:

1. **App Home → Show Tabs**: enable the **Messages Tab** and allow users to send
   messages from it.
2. **OAuth & Permissions**: add the `im:history` scope.
3. **Event Subscriptions**: add the `message.im` bot event.

Reinstall the app when Slack asks. A direct message needs no mention, and the whole
direct-message channel is one continuous conversation.

## Buttons in answers

Some agents answer with cards that have buttons. MATE turns these into Slack
messages with real buttons. For the buttons to do anything, switch on interactivity
in the Slack app:

1. Open **Interactivity & Shortcuts** and turn **Interactivity** on.
2. As **Request URL**, enter the same address as the events URL but ending in
   `/integrations/slack/interactions`.
3. Save.

Clicking a button then sends its value to the agent as if the person had typed it,
and the reply appears in the same thread. No extra scopes are needed. Buttons that
open a link work without this step.

## Trying it on your own machine

Slack can only call an address on the public internet over HTTPS. To test against a
MATE running locally, open a tunnel to port 8000 with a tool such as `ngrok` or
`cloudflared`, and use the tunnel's HTTPS address in the Request URL.

## If nothing happens

- **Slack does not verify the URL.** MATE is not reachable from the internet at that
  address, or the signing secret is wrong.
- **The bot does not answer.** The integration is not **Active**, the Team ID does not
  match (the server log names the team id it received), or the agent's Allowed Roles
  does not include `user`.
