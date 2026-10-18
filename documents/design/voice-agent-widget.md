# Design: voice agent in the widget

Status: proposal for 1.5.0, to be built later. Tracking: #178, feature 4.

## Problem

A visitor can only type to an agent. Voice would let a support or sales agent take
a spoken conversation on any site that embeds the widget.

ADK already has the server side. `/run_live` is a WebSocket that streams audio in
and audio out. The auth server forwards it (`server/proxy_routes.py`). Nothing uses
it yet:

- **The widget can't connect.** The proxy accepts a dashboard session, a bearer
  token or Basic auth, but not a widget key, and the widget has no microphone or
  audio playback.
- **Most agents can't talk.** Only Gemini models served by the Gemini Live API open
  a live connection. Agents on LiteLLM models (OpenRouter, OpenAI, Anthropic and
  others) fail with "Live connection is not supported".
- **MATE would see no tokens, no cost and no budget for voice.** Voice usage reaches
  the session as events, but no `after_model` callback sees it, so nothing reaches
  `token_usage_logs`.

## Goals

- A microphone button in the widget: talk to the agent and hear it answer, with
  the transcript shown as chat bubbles.
- A per-agent switch with its own voice model, so a text agent keeps its text model.
- The same protection as text chat:
  - widget key and origin checks;
  - guardrails;
  - token and dollar tracking;
  - budgets;
  - the Art. 50 AI disclosure.
- A cap on call length.

## Non-goals (first version)

- **The LangGraph runtime.** It has no live mode; voice is offered only on ADK.
- **Video, camera input and avatars.** `/run_live` supports them; they are left for
  later.
- **Phone calls** (Twilio, SIP).
- **Recording audio.** Only the transcript is kept, as session events, the same as
  text. `save_live_blob` stays off.
- **Voice in the Work Room.** It can use the existing proxy in a second step.

## How it works today

| Piece | Where | What it does |
|---|---|---|
| Live endpoint | ADK `cli/api_server.py`, `/run_live` | WebSocket. Takes `app_name`, `user_id`, `session_id` and `modalities` (default `AUDIO`). The session must exist. The client sends `LiveRequest` JSON; the server sends events as JSON |
| `LiveRequest` | ADK `live/live_request_queue.py` | `content` (typed text), `blob` (audio: `mime_type`, base64 `data`), `activity_start`/`activity_end`, `audio_stream_end`, `close`, `state_delta` |
| Proxy | `server/proxy_routes.py` `websocket_run_live` | auth (token, dashboard session, Basic), optional rate-limit check at connect, then forwards text frames both ways unchanged |
| Transcription | ADK `RunConfig` | input and output audio transcription are on by default |
| Guardrails on voice | ADK `live/_live_llm_flow.py` | `before_model` callbacks screen typed text and each finished input transcription; `after_model` callbacks screen the model's output transcription as it builds up. A block ends the turn and reconnects |
| Usage | ADK `models/gemini_llm_connection.py` | the Live API's usage arrives as `usage_metadata` on events, not through `after_model` |
| Live models | ADK `models/google_llm.py` | only `Gemini.connect()` is implemented. `BaseLlm.connect()`, which `LiteLlm` inherits, raises `NotImplementedError` |
| Widget chat | `server/widget_routes.py` `/widget/api/chat` | widget key, origin check, user scoped as `widget_{key id}_{user}`, session created server-side, SSE to `/run_sse` |
| Widget iframe | `static/js/widget/mate-widget.js` | `allow="clipboard-write"` only; there's no microphone permission |

## Proposal

### 1. A voice model per agent

The agent config gets a `voice` section (stored in `tool_config`, edited in the
agent form):

```json
{
  "voice": {
    "enabled": true,
    "model": "<a Gemini Live model>",
    "voice_name": "Aoede",
    "language": "sr-RS",
    "max_minutes": 10
  }
}
```

**The model is per run, not per agent.** A live connection takes the model from
the agent itself, and `RunConfig` can't change it. So the agent manager builds a
second app for voice-enabled agents, `<agent>__voice`: the same instructions,
tools and sub-agents, with the voice model on every agent in the tree. A sub-agent
left on a text model would fail the moment the conversation is transferred to it.

**Consequences:**
- Voice and text sessions are separate. A visitor who switches from typing to
  talking starts a new conversation. Carrying the last few turns across is listed
  under [Open questions](#open-questions).
- `voice.model` must be a Gemini model. The form refuses anything else, and
  `voice.enabled` can't be set on an agent that runs on LangGraph.
- The voice model needs `GOOGLE_API_KEY`, or Vertex AI. The Settings page shows
  whether the key is set, as it does for the image model.

`voice_name` and `language` map to the speech config the Live API takes.

### 2. A widget endpoint: `/widget/api/live`

This is a new WebSocket, separate from the admin `/run_live` proxy, with the
widget chat's rules.

**On connect:**
- **The widget key** is read from a query parameter, because browsers can't set
  headers on a WebSocket.
- **The key** must be active, and its agent must have `voice.enabled`.
- **Origin:** the `Origin` header is checked against the key's allowlist and
  always enforced, whatever `WIDGET_ORIGIN_STRICT` says. A WebSocket isn't covered
  by CORS, so without this any site could use the key for voice, which is billed
  per minute.
- **User:** `widget_{key id}_{user}`, as in `/widget/api/chat`. The server creates
  the session; the browser never names another user.
- **Budget:** the agent's and the project's dollar budgets must have room left.
  The rate limit counts one request.

**While connected,** the server inspects every frame from the browser instead of
forwarding it unread. Only these get through:
- `blob` with `mime_type` `audio/pcm;rate=16000`, up to 64 KB a frame;
- `activity_start`, `activity_end`, `audio_stream_end`, `close`;
- `content` with text only, for typing during a call.

Everything else ends the connection. The one that matters is `state_delta`: ADK
applies it to the session state, so forwarding it would let a visitor write any
session state key. The existing admin proxy forwards it; that's acceptable for
admins, but not for anonymous visitors.

**Limits:**
- the call ends at `voice.max_minutes`, or after 60 seconds of silence;
- every 30 seconds, the dollar budget is checked against the spend recorded so far;
- when a limit is hit, the server sends a closing message and closes.

### 3. Usage, cost and budgets

A plugin on the ADK runtime (`on_event_callback`) writes `token_usage_logs` rows
from events that carry `usage_metadata` on a live run. It writes one row per usage
report, keyed by invocation id, like the model callbacks do for text. This also
covers voice outside the widget, through the admin proxy.

**Audio is priced differently from text.** The Live API reports tokens by modality
(`prompt_tokens_details`, `candidates_tokens_details`), and audio tokens cost
several times what text tokens do. `model_prices` has one input and one output
price per model.

- **First version:** price the voice model with its audio prices on the Model
  prices page, since a voice call is almost all audio. The Usage page labels voice
  calls.
- **Later:** a price per modality.

### 4. The widget

- **A microphone button** next to Send, shown when the key's agent has voice
  enabled. `/widget/public-config` says so.
- **Capture:** `getUserMedia`, then an AudioWorklet that converts to 16 kHz mono
  16-bit PCM and sends base64 `blob` frames about every 100 ms. The worklet is a
  file under `/static`, so the CSP allows it with no change.
- **Playback:** the agent's audio comes as `inline_data` PCM at 24 kHz. It is
  queued in an `AudioContext`. An `interrupted` event stops playback, so the
  visitor can talk over the agent.
- **Transcript:** input and output transcriptions are shown as user and agent
  bubbles, as in text chat. The rating buttons apply to the agent's transcript.
- **The embed script** (`mate-widget.js`) adds `microphone` to the iframe's `allow`.
  A host page whose own `Permissions-Policy` blocks the microphone can't offer voice.
  The widget then hides the button and logs why to the console.
- **AI disclosure (Art. 50):** the disclosure text is shown before the first call
  starts. The voice agent's instructions also tell it to say at the start of the
  call that it is an AI.

### 5. Audit and observability

- **Audit:** an audit entry for each call: `voice.call_ended`, with the widget key,
  the agent, the duration, why it ended (visitor, limit, budget, error), and the
  token totals.
- **Usage page:** a filter for voice calls.

## Security

- **Origin:** always enforced, as described in
  [section 2](#2-a-widget-endpoint-widgetapilive).
- **Allowlist of browser frames:** this is the main difference from the admin
  proxy. It removes `state_delta`, and an attacker can't push other request kinds
  into the run.
- **Limits:** frame size and rate, call length and silence timeout. One visitor
  can't hold a model connection open indefinitely.
- **No audio stored:** only transcripts, which already fall under the audit and
  retention rules for sessions.
- **The code executor:** `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` applies to the voice
  app as it does to the text one.

## Open questions

1. **Do MATE's model callbacks handle transcription-shaped calls?**
   - In live mode, `before_model` gets the transcription as the request content.
   - `after_model` gets a response whose text is in `output_transcription`, not in
     `content.parts`.
   - Check that `model_guardrail.py` and the RBAC callback read both, before
     relying on them for voice.
2. **Current Live model names and audio prices.** Look them up when building. They
   change often, so the form takes a free-text model name with suggestions.
3. **Session resumption.** With `enable_session_resumption`, the Live API can
   continue after a dropped connection (a mobile network change). Is it worth
   turning on in the first version?
4. **Text history in a voice call.** Give the voice app the last turns of the
   visitor's text session as context, or keep them apart? Keeping them apart is
   simpler and is what the first version does.
5. **One model, both modes.** Some Gemini models serve both `generateContent` and
   the Live API. For those, the second app is only needed if the voice model
   differs from the text one.

## Phases

1. **Server.**
   - the `voice` config and the `<agent>__voice` app;
   - the usage plugin;
   - `/widget/api/live` with the frame allowlist, origin check and limits.
   - It's testable with a small script that streams a WAV file.
2. **Widget.** The microphone, playback, transcript bubbles, the disclosure and the
   embed-script change.
3. **Dashboard.**
   - the agent form's voice section;
   - the Settings check for the key;
   - the Usage filter;
   - a price per modality.
4. **Later.** Voice in the Work Room, session resumption, and phone calls.

## Testing

- **Unit tests:**
  - the frame allowlist (each allowed kind; `state_delta` refused; an oversized
    blob refused);
  - the origin check on the WebSocket;
  - the usage plugin writing rows from usage events;
  - the voice app builder putting the voice model on every agent in the tree.
- **End to end with a real Gemini Live model:**
  - stream a recorded question, get audio and both transcriptions back;
  - check that `token_usage_logs` has priced rows for the call;
  - a guardrail that blocks a phrase in the spoken input ends the turn;
  - a budget of a few cents ends the call;
  - `max_minutes` ends the call.
- **Browsers:** Chrome, Firefox and Safari on desktop, and Safari on iOS. iOS needs
  a user tap before audio can play, so the call starts from the button press.
