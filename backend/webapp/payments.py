"""
Plans and payments for the five ways CineCut earns.

Everything is free for now (CINECUT_PAYMENTS_FREE=1, the default): every plan costs ₹0, choosing one completes at
once and starts a 30-day subscription, and the list price is shown for later. In free mode every feature is open to
everyone, subscribed or not.

Razorpay takes over for rupee prices when you set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (and RAZORPAY_WEBHOOK_SECRET
for webhooks) and CINECUT_PAYMENTS_FREE=0: orders are created with Razorpay's Orders API, the checkout signature is
checked with HMAC-SHA256, and each order starts its subscription exactly once.

Viewers in the United States pay in dollars and in the United Kingdom in pounds, through Stripe Checkout, once
STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are set: UK prices include VAT, US prices have sales tax added at checkout
(Stripe Tax, CINECUT_STRIPE_TAX=1, the default, which must also be switched on in the Stripe account). The webhook
signature (Stripe-Signature: HMAC-SHA256 of "timestamp.body") is checked before any order is marked paid. A paid mode
with no provider set up refuses to take orders instead of giving plans away.
"""
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from backend.webapp import db

PLANS: List[Dict[str, Any]] = [
    {"id": "library_plus", "stream": "library", "owner": "user", "name": "Library Plus", "list_price_inr": 79, "range": "₹49–99 a month",
     "features": ["Every classic in the library, as video or audio", "Hindi and English narration", "Listen mode with speed control",
                  "Continue where you left off"]},
    {"id": "creator", "stream": "creator", "owner": "user", "name": "Creator", "list_price_inr": 799, "minutes": 300, "range": "₹799 a month",
     "features": ["300 source minutes a month of your own videos", "Condensed cuts and 9:16 reels with captions", "Hindi and regional dubbing"]},
    {"id": "creator_pro", "stream": "creator", "owner": "user", "name": "Creator Pro", "list_price_inr": 1999, "minutes": 1000, "range": "₹1,999 a month",
     "features": ["1,000 source minutes a month", "Everything in Creator", "Priority processing"]},
    {"id": "institute", "stream": "institute", "owner": "org", "org_kind": "institute", "name": "Institute", "list_price_inr": 14999,
     "range": "₹10,000–25,000 a month", "features": ["A private lecture shelf for your students", "Study cuts and own-words explainers of your lectures",
                                                    "Quizzes, and Hindi or regional versions", "Invite students with a code"]},
    {"id": "company", "stream": "company", "owner": "org", "org_kind": "company", "name": "Company training", "list_price_inr": 300, "per_seat": True,
     "range": "₹250–500 per employee a month", "features": ["Training videos and manuals as 5-minute explainers", "Employees' own languages",
                                                           "Quizzes and completion reports"]},
    {"id": "partner_api", "stream": "api", "owner": "user", "name": "Partner API and licensing", "list_price_inr": 0,
     "range": "Revenue share, or ₹2–5 per minute", "features": ["Catalogue feed with credits and licences", "Play recipes and narration audio",
                                                               "Create explainers of public-domain books", "Usage report for revenue share"]},
]
PRICES = {  # list prices outside India, in cents or pence (the rupee list price stays on each plan above)
    "library_plus": {"USD": 499, "GBP": 399}, "creator": {"USD": 1900, "GBP": 1500}, "creator_pro": {"USD": 4900, "GBP": 3900},
    "institute": {"USD": 29900, "GBP": 24900}, "company": {"USD": 600, "GBP": 500}, "partner_api": {"USD": 0, "GBP": 0}}
CURRENCY_BY_COUNTRY = {"IN": "INR", "US": "USD", "GB": "GBP"}
SYMBOL = {"INR": "₹", "USD": "$", "GBP": "£"}
TAX_NOTE = {"INR": "Prices include GST.", "GBP": "Prices include UK VAT.", "USD": "Sales tax is added at checkout where it applies."}
PERIOD_DAYS = 30
RAZORPAY_API = "https://api.razorpay.com/v1"
STRIPE_API = "https://api.stripe.com/v1"


def free_mode() -> bool:
    return os.environ.get("CINECUT_PAYMENTS_FREE", "1") != "0"


def currency_for(country: Optional[str]) -> str:
    return CURRENCY_BY_COUNTRY.get((country or "IN").upper(), "USD")


def provider(currency: str = "INR") -> str:
    """free (everything at 0), razorpay (rupees), stripe (dollars, pounds) or unavailable (paid mode, nothing set up)."""
    if free_mode():
        return "free"
    if currency == "INR" and os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"):
        return "razorpay"
    if currency in ("USD", "GBP") and os.environ.get("STRIPE_SECRET_KEY"):
        return "stripe"
    return "unavailable"


def list_minor(plan: Dict[str, Any], currency: str) -> int:
    if currency == "INR":
        return int(plan["list_price_inr"]) * 100
    return int(PRICES.get(plan["id"], {}).get(currency, 0))


def fmt(minor: int, currency: str) -> str:
    sym = SYMBOL.get(currency, "")
    return f"{sym}{minor / 100:,.0f}" if currency == "INR" else f"{sym}{minor / 100:,.2f}"


def plan_by_id(plan_id: str) -> Optional[Dict[str, Any]]:
    return next((p for p in PLANS if p["id"] == plan_id), None)


def price_minor(plan: Dict[str, Any], currency: str = "INR", seats: int = 1) -> int:
    if provider(currency) == "free":
        return 0
    return list_minor(plan, currency) * (max(1, seats) if plan.get("per_seat") else 1)


def price_paise(plan: Dict[str, Any], seats: int = 1) -> int:
    return price_minor(plan, "INR", seats)


def public_plans(country: Optional[str] = "IN") -> List[Dict[str, Any]]:
    cur = currency_for(country)
    free_now = provider(cur) == "free"
    out = []
    for p in PLANS:
        lst, now = list_minor(p, cur), price_minor(p, cur)
        rng = p["range"] if cur == "INR" or not lst else f"{fmt(lst, cur)} {'per employee ' if p.get('per_seat') else ''}a month"
        out.append(dict(p, currency=cur, price_minor=now, list_minor=lst, price_display=fmt(now, cur), list_display=fmt(lst, cur) if lst else "",
                        free_now=free_now, price_inr=price_paise(p) // 100, range=rng if p["id"] != "partner_api" else p["range"].split(",")[0]))
    return out


def has_access(user_id: int, stream: str, org_id: Optional[int] = None) -> bool:
    if free_mode():
        return True
    if org_id:
        return any(s["stream"] == stream for s in db.active_subscriptions("org", org_id))
    return any(s["stream"] == stream for s in db.active_subscriptions("user", user_id))


def _activate(order: Dict[str, Any]) -> None:
    plan = plan_by_id(order["plan_id"])
    if plan:
        db.add_subscription(order["owner_type"], order["owner_id"], plan["id"], plan["stream"], order.get("seats") or 1,
                            PERIOD_DAYS, order["id"])


def checkout(user_id: int, plan_id: str, org_id: Optional[int] = None, seats: int = 1, country: Optional[str] = "IN",
             base_url: str = "") -> Dict[str, Any]:
    plan = plan_by_id(plan_id)
    if not plan:
        raise ValueError("Unknown plan.")
    if plan["owner"] == "org":
        if not org_id:
            raise ValueError("Create or choose a workspace first; this plan belongs to an institute or company.")
        if db.role_in(org_id, user_id) not in ("owner", "admin"):
            raise ValueError("Only a workspace owner or admin can choose its plan.")
        org = db.get_org(org_id)
        if plan.get("org_kind") and org and org["kind"] != plan["org_kind"]:
            raise ValueError(f"This plan is for a {plan['org_kind']} workspace.")
        owner_type, owner_id = "org", org_id
    else:
        owner_type, owner_id = "user", user_id
    seats = max(1, int(seats or 1))
    cur = currency_for(country)
    amount = price_minor(plan, cur, seats)
    prov = provider(cur)
    if amount and prov == "unavailable":
        raise RuntimeError(f"Payments in {cur} are not set up yet.")
    oid = db.create_order(user_id, plan_id, owner_type, owner_id, seats, amount, prov, cur)
    if amount == 0:
        order, newly = db.mark_paid(oid, "free")
        if newly:
            _activate(order)
        return {"status": "active", "order_id": oid, "plan": plan["name"], "days": PERIOD_DAYS}
    if prov == "stripe":
        data = {"mode": "payment", "client_reference_id": oid, "metadata[order]": oid, "success_url": f"{base_url}/app/#/account",
                "cancel_url": f"{base_url}/app/#/plans", "line_items[0][quantity]": "1",
                "line_items[0][price_data][currency]": cur.lower(), "line_items[0][price_data][unit_amount]": str(amount),
                "line_items[0][price_data][product_data][name]": f"CineCut {plan['name']} (30 days)",
                "line_items[0][price_data][tax_behavior]": "inclusive" if cur == "GBP" else "exclusive"}
        if os.environ.get("CINECUT_STRIPE_TAX", "1") != "0":
            data["automatic_tax[enabled]"] = "true"
        res = httpx.post(f"{STRIPE_API}/checkout/sessions", auth=(os.environ["STRIPE_SECRET_KEY"], ""), data=data, timeout=30)
        if res.status_code >= 300:
            raise RuntimeError(f"Stripe could not start the checkout ({res.status_code}).")
        sess = res.json()
        db.set_provider_order(oid, sess["id"])
        return {"status": "created", "order_id": oid, "stripe": {"url": sess["url"]}}
    key_id, secret = os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]
    res = httpx.post(f"{RAZORPAY_API}/orders", auth=(key_id, secret), timeout=30,
                     json={"amount": amount, "currency": "INR", "receipt": oid, "notes": {"plan": plan_id, "order": oid}})
    if res.status_code >= 300:
        raise RuntimeError(f"Razorpay could not create the order ({res.status_code}).")
    rp = res.json()
    db.set_provider_order(oid, rp["id"])
    return {"status": "created", "order_id": oid, "razorpay": {"key": key_id, "order_id": rp["id"], "amount": amount,
                                                                 "currency": "INR", "name": "CineCut", "description": plan["name"]}}


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout(razorpay_order_id: str, razorpay_payment_id: str, signature: str) -> Optional[Dict[str, Any]]:
    secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not secret or not hmac.compare_digest(_sign(secret, f"{razorpay_order_id}|{razorpay_payment_id}".encode()), signature or ""):
        return None
    order = db.get_order(provider_order_id=razorpay_order_id)
    if not order:
        return None
    order, newly = db.mark_paid(order["id"], razorpay_payment_id)
    if newly:
        _activate(order)
    return order


def handle_webhook(body: bytes, signature: str) -> bool:
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret or not hmac.compare_digest(_sign(secret, body), signature or ""):
        return False
    event = json.loads(body.decode("utf-8"))
    if event.get("event") in ("payment.captured", "order.paid"):
        entity = (event.get("payload", {}).get("payment") or {}).get("entity") or {}
        order = db.get_order(provider_order_id=entity.get("order_id", ""))
        if order:
            order, newly = db.mark_paid(order["id"], entity.get("id", ""))
            if newly:
                _activate(order)
    return True


def handle_stripe_webhook(body: bytes, signature: str, tolerance: int = 300) -> bool:
    """Stripe Checkout webhook: the Stripe-Signature header (t=timestamp, v1=HMAC-SHA256 of "timestamp.body" with the
    endpoint secret) must match and be recent; a paid checkout session then marks its order paid, once."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    fields = [x.strip().split("=", 1) for x in signature.split(",") if "=" in x]
    ts = next((v for k, v in fields if k == "t"), "")
    sigs = [v for k, v in fields if k == "v1"]
    if not ts.isdigit() or not sigs or abs(time.time() - int(ts)) > tolerance:
        return False
    expected = _sign(secret, ts.encode() + b"." + body)
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        return False
    event = json.loads(body.decode("utf-8"))
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        obj = (event.get("data") or {}).get("object") or {}
        if obj.get("payment_status") == "paid":
            oid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("order")
            order, newly = db.mark_paid(oid, obj.get("payment_intent") or obj.get("id") or "")
            if order and newly:
                _activate(order)
    return True
