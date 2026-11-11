#!/usr/bin/env python3
"""Intent-aware PayPal sandbox checkout. No payment or AI outcome is simulated."""
import json, os, re, secrets, threading, urllib.error, urllib.parse, urllib.request
from decimal import Decimal, InvalidOperation
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(os.getenv("PORT", "8000"))
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MERCHANT_ID = os.getenv("PAYPAL_MERCHANT_ID", "")
PAYPAL_BASE = "https://api-m.sandbox.paypal.com" if os.getenv("PAYPAL_ENV", "sandbox") == "sandbox" else "https://api-m.paypal.com"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
analyses, orders = {}, {}
lock = threading.Lock()

def paypal(method, path, body=None, request_id=None):
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise RuntimeError("PayPal sandbox credentials are not configured. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to .env.")
    basic = (PAYPAL_CLIENT_ID + ":" + PAYPAL_CLIENT_SECRET).encode()
    req = urllib.request.Request(PAYPAL_BASE + "/v1/oauth2/token", data=b"grant_type=client_credentials", headers={"Authorization":"Basic " + __import__("base64").b64encode(basic).decode(),"Content-Type":"application/x-www-form-urlencoded"}, method="POST")
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
    headers = {"Authorization":"Bearer " + token,"Content-Type":"application/json","PayPal-Request-Id":request_id or secrets.token_hex(16)}
    req = urllib.request.Request(PAYPAL_BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as res: return json.loads(res.read())
    except urllib.error.HTTPError as e:
        try: detail=json.loads(e.read())
        except Exception: detail={}
        raise ValueError("PayPal returned HTTP %s (%s)." % (e.code, detail.get("name", "API error")))

def validate(data):
    intent = str(data.get("intent", "")).strip()
    items = data.get("items")
    if not intent or len(intent) > 300: raise ValueError("Describe what you meant to buy (up to 300 characters).")
    if not isinstance(items, list) or not 1 <= len(items) <= 8: raise ValueError("Add between 1 and 8 items.")
    clean=[]
    for item in items:
        name=str(item.get("name", "")).strip()[:100]
        try: price=Decimal(str(item.get("price", ""))).quantize(Decimal("0.01")); qty=int(item.get("quantity", 1))
        except (InvalidOperation, ValueError): raise ValueError("Each item needs a valid price and quantity.")
        if not name or price <= 0 or price > 10000 or qty < 1 or qty > 20: raise ValueError("Check item names, prices (under $10,000), and quantities (1–20).")
        clean.append({"name":name,"price":price,"quantity":qty})
    total=sum((x["price"]*x["quantity"] for x in clean), Decimal("0"))
    if total > Decimal("10000"): raise ValueError("Total must be at most $10,000 for this sandbox demo.")
    return intent, clean, total.quantize(Decimal("0.01"))

def verify_order_matches_review(remote, analysis, expected_payee_id=""):
    """Fail closed unless the PayPal order is the exact approved cart snapshot."""
    if remote.get("status") != "APPROVED":
        raise ValueError("PayPal order is not approved; capture blocked.")
    if remote.get("intent") != "CAPTURE":
        raise ValueError("PayPal order intent differs from reviewed checkout; capture blocked.")
    units = remote.get("purchase_units") or []
    if len(units) != 1:
        raise ValueError("PayPal order must contain exactly one reviewed purchase unit.")
    unit = units[0]
    if unit.get("reference_id") != "intent-checked-cart":
        raise ValueError("PayPal purchase reference differs from reviewed cart; capture blocked.")
    amount = unit.get("amount") or {}
    if amount.get("value") != analysis["total"] or amount.get("currency_code") != "USD":
        raise ValueError("PayPal order amount differs from reviewed total. Capture blocked.")
    payee = unit.get("payee") or {}
    actual_payee = payee.get("merchant_id")
    if not actual_payee:
        raise ValueError("PayPal order did not return a merchant identity; capture blocked.")
    if expected_payee_id and actual_payee != expected_payee_id:
        raise ValueError("PayPal order payee differs from configured merchant; capture blocked.")
    expected = sorted((x["name"], str(x["quantity"]), str(x["price"])) for x in analysis["items"])
    actual = []
    for item in unit.get("items") or []:
        price = (item.get("unit_amount") or {}).get("value")
        currency = (item.get("unit_amount") or {}).get("currency_code")
        if currency != "USD":
            raise ValueError("PayPal line item currency differs from reviewed cart; capture blocked.")
        actual.append((item.get("name", ""), str(item.get("quantity", "")), str(price or "")))
    if sorted(actual) != expected:
        raise ValueError("PayPal line items differ from reviewed cart. Capture blocked.")
    return actual_payee

def capture_is_complete(result):
    """An order status alone is insufficient; every returned capture must complete."""
    if result.get("status") != "COMPLETED":
        return False
    units = result.get("purchase_units") or []
    captures = [capture for unit in units for capture in ((unit.get("payments") or {}).get("captures") or [])]
    return bool(captures) and all(c.get("status") == "COMPLETED" for c in captures)

def extract_budget_constraint(intent):
    """Return an explicit numeric cap and the exact clause that supplied it."""
    patterns=(r"\b(?:under|below|less than|at most|no more than|up to|maximum(?: of)?|max(?:imum)?(?: spend)?|budget(?: of)?|within)\s*(?:(?:us|usd)\s*)?\$?\s*[\d,]+(?:\.\d{1,2})?\b",r"\$\s*[\d,]+(?:\.\d{1,2})?\s*(?:or less|max(?:imum)?)\b")
    for pattern in patterns:
        match=re.search(pattern,intent,re.I)
        if match:
            amount=re.search(r"[\d,]+(?:\.\d{1,2})?",match.group(0))
            try: return Decimal(amount.group(0).replace(",","")).quantize(Decimal("0.01")),match.group(0)
            except (InvalidOperation,AttributeError): return None,None
    return None,None

def extract_budget_limit(intent):
    return extract_budget_constraint(intent)[0]

def quote_occurs(quote,source):
    if not isinstance(quote,str) or len(quote.strip())<2: return False
    normalize=lambda value: re.sub(r"\s+"," ",value).casefold().strip()
    return normalize(quote) in normalize(source)

def suggest_budget_repair(intent,items,limit):
    """Find a smallest removal set that meets the cap and retains a relevant item."""
    if limit is None or len(items)<2: return None
    tokens=lambda value:set(re.findall(r"[a-z0-9]+",value.casefold()))
    ignored={"the","a","an","for","to","with","my","buy","get","want","need","under","below","less","than","at","most","no","more","up","maximum","max","budget","usd","dollars"}
    intent_terms={word for word in tokens(intent)-ignored if not word.isdigit()}
    if not intent_terms: return None
    relevance=[len((tokens(item["name"])-ignored)&intent_terms) for item in items]
    candidates=[]
    for mask in range(1,1<<len(items)):
        removed=[i for i in range(len(items)) if mask&(1<<i)]
        kept=[i for i in range(len(items)) if not mask&(1<<i)]
        if not kept: continue
        new_total=sum((items[i]["price"]*items[i]["quantity"] for i in kept),Decimal("0"))
        if new_total>limit or not any(relevance[i] for i in kept): continue
        loss=sum(relevance[i] for i in removed)
        retained=sum(relevance[i] for i in kept)
        candidates.append(((len(removed),loss,-retained,tuple(removed)),removed,kept,new_total))
    if not candidates: return None
    _,removed,kept,new_total=min(candidates,key=lambda candidate:candidate[0])
    return {"kind":"budget","removeIndexes":removed,"removeItems":[items[i]["name"] for i in removed],"keepItems":[items[i]["name"] for i in kept],"newTotal":str(new_total.quantize(Decimal("0.01"))),"saved":str((sum((items[i]["price"]*items[i]["quantity"] for i in removed),Decimal("0"))).quantize(Decimal("0.01")))}

def suggest_semantic_repair(intent,items,findings):
    """Offer removal only for explicitly flagged lines, and only if intent-relevant goods remain."""
    flagged={finding.get("item") for finding in findings if finding.get("severity") in ("review","high")}
    removed=[i for i,item in enumerate(items) if item["name"] in flagged]
    kept=[i for i in range(len(items)) if i not in removed]
    if not removed or not kept: return None
    tokens=lambda value:set(re.findall(r"[a-z0-9]+",value.casefold()))
    ignored={"the","a","an","for","to","with","my","buy","get","want","need","under","below","less","than","at","most","no","more","up","maximum","max","budget","usd","dollars"}
    intent_terms=tokens(intent)-ignored
    if not intent_terms or not any((tokens(items[i]["name"])-ignored)&intent_terms for i in kept): return None
    new_total=sum((items[i]["price"]*items[i]["quantity"] for i in kept),Decimal("0"))
    saved=sum((items[i]["price"]*items[i]["quantity"] for i in removed),Decimal("0"))
    return {"kind":"mismatch","removeIndexes":removed,"removeItems":[items[i]["name"] for i in removed],"keepItems":[items[i]["name"] for i in kept],"newTotal":str(new_total.quantize(Decimal("0.01"))),"saved":str(saved.quantize(Decimal("0.01")))}

def apply_budget_check(review,intent,items,total):
    """An explicit buyer budget is a deterministic hard constraint, not an LLM judgment."""
    limit,budget_quote=extract_budget_constraint(intent)
    review["budgetLimit"]=str(limit) if limit is not None else None
    review["budgetExceeded"]=bool(limit is not None and total > limit)
    if review["budgetExceeded"]:
        over=(total-limit).quantize(Decimal("0.01"))
        review["risk"]="high"
        review["summary"]=(str(review.get("summary", "Review complete."))+" Your stated spending limit is exceeded; payment is locked until the cart is within the limit.")[:500]
        findings=review.get("findings",[])
        findings.insert(0,{"item":"Cart total · $%s vs. $%s limit"%(total,limit),"reason":"The total is $%s above the numeric limit you gave. This amount check is deterministic."%over,"severity":"high","evidence":{"intent_quote":budget_quote,"cart_quote":"Calculated total: $%s from the listed item prices and quantities."%total}})
        review["findings"]=findings[:3]
        review["repairSuggestion"]=suggest_budget_repair(intent,items,limit)
    elif review.get("findings"):
        review["repairSuggestion"]=suggest_semantic_repair(intent,items,review["findings"])
    return limit

def assert_order_allowed(analysis):
    if not analysis or analysis.get("used"):
        raise ValueError("Review expired or already used. Analyze the cart again.")
    if not analysis.get("ai_verified"):
        raise ValueError("A live AI review is required for checkout. Offline baseline is preview-only.")
    if analysis.get("budget_exceeded"):
        raise ValueError("Cart exceeds the budget you stated. Adjust the cart and run a fresh review before checkout.")
    if analysis.get("needs_clarification"):
        raise ValueError("A review finding did not have verifiable source text. Clarify the intent and run a fresh review before checkout.")

def validate_ai_findings(findings,intent,items):
    """Keep only claims tied to literal text in this exact intent/cart snapshot."""
    item_names={item["name"] for item in items}
    verified=[]
    unsupported=False
    for finding in findings[:3]:
        evidence=finding.get("evidence") if isinstance(finding,dict) else None
        item=finding.get("item") if isinstance(finding,dict) else None
        if (not isinstance(evidence,dict) or finding.get("severity") not in ("info","review","high") or item not in item_names or not isinstance(finding.get("reason"),str) or not quote_occurs(evidence.get("intent_quote"),intent) or not quote_occurs(evidence.get("cart_quote"),item)):
            unsupported=True
            continue
        verified.append({"item":item[:100],"reason":finding["reason"][:500],"severity":finding["severity"],"evidence":{"intent_quote":evidence["intent_quote"].strip()[:160],"cart_quote":evidence["cart_quote"].strip()[:120]}})
    return verified,unsupported

def assess(intent, items):
    """AI findings need source quotes; deterministic money checks stay outside the model."""
    if OPENAI_API_KEY:
        system="You are CartSentry, a cautious purchase-intent reviewer. Compare the buyer's request with cart item names. You cannot approve payments, change amounts, or infer facts not supplied. Return JSON only: {\"summary\":string,\"risk\":\"low\"|\"review\"|\"high\",\"findings\":[{\"item\":string,\"reason\":string,\"severity\":\"info\"|\"review\"|\"high\",\"evidence\":{\"intent_quote\":string,\"cart_quote\":string}}],\"confidence\":number}. Each finding MUST quote an exact, case-insensitive substring from the buyer intent and an exact substring from the named cart item. Never paraphrase evidence. If no quote supports a concern, omit it. Max 3 findings."
        payload={"model":MODEL,"temperature":0,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":system},{"role":"user","content":json.dumps({"intent":intent,"items":[{"name":x["name"],"quantity":x["quantity"]} for x in items]})}]}
        req=urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization":"Bearer "+OPENAI_API_KEY,"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as res: out=json.loads(res.read())
            raw=json.loads(out["choices"][0]["message"]["content"])
            if raw.get("risk") not in ("low","review","high") or not isinstance(raw.get("findings"),list) or not isinstance(raw.get("confidence"),(int,float)) or not 0<=raw["confidence"]<=1: raise ValueError("Invalid AI response")
            findings,unsupported=validate_ai_findings(raw["findings"],intent,items)
            result={"summary":str(raw.get("summary","Review complete."))[:500],"risk":raw["risk"],"findings":findings,"confidence":raw["confidence"],"source":"AI model · "+MODEL,"needsClarification":unsupported}
            if unsupported:
                result["risk"]="review"
                result["summary"]=(result["summary"]+" A concern lacked a verifiable quote from the request and cart. Please clarify before checkout.")[:500]
            return result
        except Exception:
            return {"summary":"AI review is temporarily unavailable; checkout remains locked until a fresh review succeeds.","risk":"review","findings":[],"confidence":0,"source":"AI unavailable"}
    # Deterministic offline review is explicitly marked as a baseline, never represented as AI.
    words=lambda s:set(re.findall(r"[a-z0-9]+",s.lower()))
    intent_words=words(intent)-{"the","a","an","for","to","with","my","buy","get","want","need"}
    findings=[]
    for item in items:
        overlap=intent_words & words(item["name"])
        if intent_words and not overlap:
            findings.append({"item":item["name"],"reason":"No shared terms with the stated purchase intent; review that this is the item you meant to buy.","severity":"review","evidence":{"intent_quote":intent,"cart_quote":item["name"]}})
    risk="review" if findings else "low"
    return {"summary":"Offline keyword baseline: %s. Add OPENAI_API_KEY to enable semantic intent review." % ("cart needs a second look" if findings else "no obvious keyword mismatch found"),"risk":risk,"findings":findings[:3],"confidence":0.45,"source":"Offline keyword baseline (not AI)","needsClarification":False}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT / "static"),**kw)
    def log_message(self, fmt,*args): print("%s - %s"%(self.address_string(),fmt%args))
    def send_json(self,status,obj):
        data=json.dumps(obj).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(data))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path=="/api/config": self.send_json(200,{"paypalClientId":PAYPAL_CLIENT_ID,"aiEnabled":bool(OPENAI_API_KEY),"model":MODEL if OPENAI_API_KEY else None,"sandbox": "sandbox" in PAYPAL_BASE}); return
        return super().do_GET()
    def do_POST(self):
        if self.path not in ("/api/analyze","/api/create-order","/api/capture-order"): self.send_json(404,{"error":"Not found"}); return
        try:
            data=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))
            if self.path=="/api/analyze":
                intent,items,total=validate(data); review=assess(intent,items); budget_limit=apply_budget_check(review,intent,items,total)
                if review.get("source")=="AI unavailable": raise ValueError("AI review failed; retry before checkout.")
                aid=secrets.token_urlsafe(24)
                with lock: analyses[aid]={"intent":intent,"items":items,"total":str(total),"budget_limit":str(budget_limit) if budget_limit is not None else None,"budget_exceeded":bool(review.get("budgetExceeded")),"needs_clarification":bool(review.get("needsClarification")),"ai_verified":review.get("source", "").startswith("AI model"),"used":False}
                self.send_json(200,{"analysisId":aid,"total":str(total),"review":review,"items":[{"name":x["name"],"quantity":x["quantity"]} for x in items]}); return
            if self.path=="/api/create-order":
                aid=data.get("analysisId")
                with lock: a=analyses.get(aid)
                assert_order_allowed(a)
                pu={"reference_id":"intent-checked-cart","description":"CartSentry reviewed checkout","amount":{"currency_code":"USD","value":a["total"]},"items":[{"name":x["name"],"quantity":str(x["quantity"]),"unit_amount":{"currency_code":"USD","value":str(x["price"])}} for x in a["items"]]}
                result=paypal("POST","/v2/checkout/orders",{"intent":"CAPTURE","purchase_units":[pu],"application_context":{"brand_name":"IntentReceipt Sandbox","user_action":"PAY_NOW"}},"cs-"+aid[:30])
                if not result.get("id"): raise ValueError("PayPal did not return an order ID.")
                with lock: a["used"]=True; orders[result["id"]]=aid
                self.send_json(200,{"id":result["id"]}); return
            if self.path=="/api/capture-order":
                oid=str(data.get("orderID", ""))
                with lock: aid=orders.get(oid); a=analyses.get(aid) if aid else None
                if not a: raise ValueError("Unknown order. Start a fresh reviewed checkout.")
                # Re-read and verify state, merchant, total, and every item before capture.
                remote=paypal("GET","/v2/checkout/orders/"+urllib.parse.quote(oid,safe=""))
                payee_id = verify_order_matches_review(remote, a, PAYPAL_MERCHANT_ID)
                result=paypal("POST","/v2/checkout/orders/"+urllib.parse.quote(oid,safe="")+"/capture",{},"cap-"+oid[:28])
                cap=((result.get("purchase_units") or [{}])[0].get("payments",{}).get("captures") or [{}])[0]
                complete=capture_is_complete(result)
                self.send_json(200,{"id":result.get("id"),"status":result.get("status"),"captureStatus":cap.get("status"),"captureId":cap.get("id"),"amount":cap.get("amount"),"payeeId":payee_id,"paymentComplete":complete}); return
        except ValueError as e: self.send_json(400,{"error":str(e)})
        except urllib.error.URLError as e: self.send_json(502,{"error":"Could not reach payment or AI service. Check network and retry."})
        except Exception as e: self.send_json(502,{"error":str(e)[:240]})

if __name__=="__main__":
    print("IntentReceipt on http://localhost:%s"%PORT)
    ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
