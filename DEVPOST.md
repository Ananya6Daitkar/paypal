# MeantToBe — Devpost submission draft

## Elevator pitch (under 200 characters)

MeantToBe checks whether your cart matches what you meant to buy, flags unsupported or over-budget items with evidence, then verifies the approved PayPal order before capture.

## Inspiration

Online checkout asks buyers to approve a total, but that total can hide a cart that no longer matches what they intended to buy. AI-assisted shopping makes this harder to notice: a system can assemble or alter a cart faster than a person can inspect every line. We wanted a buyer-controlled checkpoint that makes intent, cart contents, and payment approval visible together.

## What it does

MeantToBe compares a buyer's plain-language request with the cart, checks explicit spending limits, and shows evidence for any concern. Its semantic AI findings must quote text from both the buyer's request and an item name; the server rejects unsupported evidence and asks for clarification. If a cart exceeds an explicit budget, MeantToBe can suggest a smallest removal that keeps an intent-relevant item. The buyer decides whether to apply the edit and must run a fresh review. Before capture, the server retrieves the approved PayPal Sandbox order and checks its status, seller, currency, total, and line items against the reviewed snapshot. The buyer approves in PayPal; the AI cannot change the cart or authorize payment.

## How we built it

The interface is built with HTML, CSS, and browser JavaScript. A Python standard-library HTTP server handles the review and checkout endpoints. OpenAI Chat Completions provides constrained JSON intent analysis when an API key is configured. Server-side validation checks evidence quotes and explicit numeric budgets independently of the model. PayPal JavaScript SDK renders the buyer approval step, while PayPal Orders v2 creates, retrieves, verifies, and captures the Sandbox order. The PayPal secret remains on the server. The offline keyword baseline is labeled as a baseline and cannot enable checkout.

## Challenges we ran into

The main challenge was keeping AI suggestions useful without granting the model control over money or silently changing a purchase. We constrained the model's output, required literal evidence quotes, validate those quotes against the submitted intent and cart, and fail closed when evidence is unsupported. We also had to verify the order returned by PayPal after buyer approval, rather than trusting the browser's approval callback or an amount shown in the UI.

## Accomplishments that we're proud of

- A buyer-visible intent-to-payment checkpoint with a distinctive “Intent Receipt” presentation.
- Server-side comparison of the approved PayPal order against the exact reviewed cart snapshot.
- AI findings that carry auditable quotes and are suppressed when those quotes do not match the input.
- Explicit budget enforcement and a buyer-approved repair suggestion that requires a fresh review.
- A checkout boundary where only the buyer approves payment; AI has no payment tools.

## What we learned

AI confidence is not proof. A payment safeguard needs evidence that a person can inspect and deterministic checks that remain authoritative when model output is wrong, incomplete, or unavailable. We also learned to make stale reviews visible: changing intent or cart contents must invalidate the prior decision.

## What's next for MeantToBe

Complete and document credentialed end-to-end Sandbox and live AI runs, test against a broader set of aligned and mismatched carts, measure false positives and missed mismatches, and study the flow with shoppers before making claims about real-world impact. Later versions could support merchant-provided product metadata and more precise buyer-defined constraints while preserving explicit buyer approval.

## Built with

PayPal JavaScript SDK, PayPal Orders v2 REST API, PayPal Sandbox, OpenAI Chat Completions API, Python, HTML, CSS, JavaScript.
