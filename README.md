# MeantToBe — The Intent Receipt

**Your payment should remember what you meant.** MeantToBe turns a buyer's plain-language intent into a visible review checkpoint, compares it with cart line items, and checks the approved PayPal Sandbox order against the exact reviewed snapshot before capture. AI findings must cite literal text from both the request and item name; the server suppresses unsupported claims and requires clarification. If an explicit budget is exceeded, a deterministic subset search can propose a smallest cart removal that retains an intent-relevant item. The buyer chooses whether to apply it, and the edited cart requires a fresh review. AI cannot edit the cart, set the price, or authorize payment. The four-stage “Intent Receipt” makes that boundary visible: intent → review → buyer approval → PayPal order verification.

## Status

This repository contains a runnable prototype. The local interface and deterministic checkout safeguards can run without credentials. Live PayPal Sandbox and AI calls require your own credentials and have **not yet been exercised** in this workspace. The UI labels the offline keyword baseline honestly; it is not represented as AI. Do not present it as a completed PayPal payment.

## Run locally

Requires Python 3.10+ (standard library only).

```sh
cp .env.example .env
# Add credentials to .env, then export them in your shell (or use your preferred env loader).
set -a; source .env; set +a
python3 server.py
```

Open http://localhost:8000. For the review-only local experience, PayPal credentials are optional. Without `OPENAI_API_KEY`, MeantToBe uses a transparent keyword baseline and identifies it as such. Without PayPal credentials, no checkout button is offered. To exercise checkout end to end, provide sandbox REST app client ID and secret and use a sandbox buyer account. The browser SDK gets only the client ID; secrets stay on the server.

Never put credentials in source control. This prototype has in-memory review/order state and is intended for local judging, not production deployment. Restarting the process clears it.

## What happens

1. The buyer writes a short intent and reviews the line items and total.
2. The server validates item names, quantities, decimal prices, and total. It independently extracts explicit numeric caps such as “under $30”; when the total exceeds that cap, it adds a deterministic finding and refuses to create a PayPal order until the cart changes. A small subset search can suggest removing the fewest items while retaining a line item with lexical relevance to the request; it never applies the edit automatically. If configured, an AI model returns constrained JSON with a risk summary, findings, evidence quotes, and confidence. The server accepts a semantic finding only when its intent quote and cart quote occur in the exact submitted snapshot. Unsupported findings require clarification; AI does not control money fields. If the model call fails, checkout is withheld until a successful fresh review. The offline keyword baseline is preview-only and cannot create a PayPal order.
3. The buyer checks a confirmation box and approves the PayPal order in PayPal's UI.
4. The server creates a CAPTURE order via PayPal Orders v2. After the buyer's PayPal approval, the server fetches the order and checks `APPROVED` status, `CAPTURE` intent, purchase reference, merchant ID, USD currency, total, and every line item's name, quantity, and unit price against the reviewed cart. Any mismatch blocks capture. The capture response is shown, and the UI reports payment complete only when the order and capture statuses are both `COMPLETED`.

Payment flow: OAuth client credentials → `POST /v2/checkout/orders` → buyer approval via PayPal JS SDK → `GET /v2/checkout/orders/{id}` state, merchant, amount, and line-item verification → `POST /v2/checkout/orders/{id}/capture` → capture-status verification. All calls use Sandbox by default. Set `PAYPAL_MERCHANT_ID` to require a match with the expected Sandbox business merchant ID. This app does not configure webhooks; it verifies completion synchronously from PayPal's capture response. No transaction outcome is simulated.

## AI disclosure

- **Tool/model:** optional OpenAI Chat Completions API; model configured by `OPENAI_MODEL` (default `gpt-4.1-mini`).
- **Task:** semantic comparison of the buyer's plain-language intent and item names; return JSON `summary`, `risk`, `findings[]`, and `confidence`.
- **Constraints:** no tool access, no price/recipient/amount decisions, no authorization. Python independently validates inputs, explicit numeric spending caps, and PayPal's order amount. The client must explicitly confirm and approve at PayPal. Model confidence is shown as an uncalibrated estimate, not a guarantee.
- **Failure:** an AI outage or malformed response stops the checkout path. With no key, the product displays a plainly labeled deterministic keyword baseline, not an AI claim.
- **Not yet measured:** model quality, uncertainty calibration, or baseline-vs-AI improvement. There is no evaluation dataset yet.

## PayPal technology

PayPal JavaScript SDK Buttons and PayPal Orders v2 REST API (`CAPTURE` intent), authenticated with OAuth 2.0 client credentials. Configure credentials in the PayPal Developer Dashboard Sandbox. PayPal's official docs say Orders v2 create/capture endpoints are available for standard checkout; partner-only authorization flows are intentionally not used here.

## Tests

```sh
python3 -m unittest -v
python3 -m py_compile server.py
```

These local checks cover deterministic input validation, preview baseline labeling, live-order mismatch blocking, payee checking, and capture-state reporting. Real OAuth, Orders v2 API calls, capture, AI behavior, browser PayPal approval, webhook delivery, mobile-device usability, and public deployment require separate credentialed verification; report those as untested until run.

## Submission checklist

- [x] Complete source and MIT license
- [x] Setup and environment template
- [x] AI and PayPal technology disclosures
- [ ] Successful live PayPal Sandbox create → approval → server verification → capture, with response evidence
- [ ] Successful semantic AI run plus saved input/output evidence and adversarial cases
- [ ] Public source repository and public demo or verified reproducible judging setup
- [ ] Under-three-minute public YouTube demo with actual app footage
- [ ] Devpost description, screenshots, test account instructions, and final submission

Hackathon deadline shown on official rules: **Nov 12, 2026, 12:00 PM Pacific = Nov 13, 2026, 1:30 AM India time**. Check the official rules page again before submission.
