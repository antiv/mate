# EU AI Act support in MATE

**MATE cannot make you compliant, and this document does not claim it can.**
Obligations under the AI Act attach to the *provider* and the *deployer* of an AI
system. MATE is a tool you use to build and run one. What it does is make the
defaults point the right way and produce evidence you can hand to someone who
asks for it.

Nothing here is legal advice. Have the wording reviewed before you rely on it.

## What applies, and when

| Obligation | Article | Applies from |
|---|---|---|
| Prohibited practices | Art. 5 | 2 February 2025 |
| AI literacy | Art. 4 | 2 February 2025 |
| General-purpose AI models | Art. 51–55 | 2 August 2025 |
| Transparency, deployer-facing | Art. 50 | **2 August 2026** |
| Marking of synthetic content | Art. 50(2) | **2 December 2026** |
| High-risk, standalone (Annex III) | Art. 8–15, 72–73 | 2 December 2027 |
| High-risk, embedded (Annex I) | Art. 8–15 | 2 August 2028 |

The high-risk dates were moved by the Digital Omnibus, in force 27 July 2026,
from August 2026 to December 2027. The Art. 50 dates were **not** changed. These
have moved once and can move again — check before relying on them.

## Art. 50: telling people they are talking to an AI

A person interacting with an AI system must be informed of that, unless it is
already obvious to a reasonably well-informed person.

MATE shows a disclosure on every public chat surface — the embeddable widget and
a standalone build — because those are the cases where it is *least* obvious: the
widget is embedded in someone else's site and styled to match it.

### Configuring it

Two fields on each agent, in the agent modal:

| Field | Effect |
|---|---|
| **AI Disclosure** | The wording shown. Leave empty for the default, or write your own to translate it |
| **Reason for not disclosing** | Filling this in **hides** the notice and records why |

There is deliberately no on/off switch. Disclosure is on unless the waiver field
holds a reason, so the decision to switch it off and the record of why it was
switched off are the same field and cannot come apart. A reason shorter than ten
characters is refused — "n/a" is not a justification anyone could review later.

Setting or clearing a waiver writes its own `audit_logs` entry
(`agent.disclosure_waived` / `agent.disclosure_restored`), separate from the
generic `agent.update`, so the decision is findable.

### Where it appears

- **Widget** — a persistent line above the input, not part of the greeting, so it
  stays visible once the conversation starts.
- **Standalone builds** — always shown. There is no dashboard behind a standalone
  export, so `MATE_AI_DISCLOSURE` can translate the wording but cannot remove it.
- **Work Room** — not shown. It sits behind a dashboard login, where the person
  chatting knows what MATE is; this is the "already obvious" case in Art. 50(1).
  If you disagree with that reading for your deployment, raise it — it is a
  judgement call, not a technical limit.

### Deliberate failure behaviour

If the agent row cannot be read when a widget renders, MATE shows the **default
disclosure** rather than nothing. A widget that quietly stops disclosing because
a query failed is precisely the outcome the Article is written against.

The disclosure is read from the agent on every render and is never stored in
`widget_config`. That blob is editable through the widget admin API by whoever
embeds the widget, and the notice is not theirs to remove.

## Art. 50(2): marking generated content

See below once implemented — synthetic content must be marked in a
machine-readable form from 2 December 2026.

## What MATE does not do

- It does not perform a conformity assessment or produce a declaration of conformity.
- It does not decide whether your system is high-risk.
- An append-only audit log is *evidence toward* Art. 12 record-keeping. It is not
  compliance, and MATE's own wording was corrected to stop implying otherwise.
