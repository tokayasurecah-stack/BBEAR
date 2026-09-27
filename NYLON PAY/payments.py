import uuid, os
from nylonpay import create_nylon_pay
from dotenv import load_dotenv

load_dotenv()

# In-memory cache: reference -> {user_email, network, phone, amount, status, raw}
# In production use Redis or a DB table. Keep it simple here.
_pending: dict = {}

def get_client():
    return create_nylon_pay(
        api_key=os.getenv("NYLON_API_KEY"),
        api_secret=os.getenv("NYLON_API_SECRET"),
    )

def initiate(amount: int, phone: str, network: str, email: str) -> dict:
    """Start a payment. Do NOT block. Return reference + initial state."""
    ref = str(uuid.uuid4())
    try:
        client = get_client()
        payment = client.collect_payment(
            amount=amount,
            currency="UGX",
            customer={"name": email.split("@")[0], "phone_number": phone},
            description=f"{network} subscription - {email}",
            reference=ref,
        )
        _pending[ref] = {
            "email": email, "network": network, "phone": phone,
            "amount": amount, "status": "pending",
            "payment_obj": payment,   # keep handle so we can poll
        }
        return {"reference": ref, "status": "pending"}
    except Exception as e:
        _pending[ref] = {"email": email, "status": "failed", "error": str(e)}
        return {"reference": ref, "status": "failed", "error": str(e)}


def check_status(reference: str) -> dict:
    """Non-blocking check of a payment's current state."""
    entry = _pending.get(reference)
    if not entry:
        return {"status": "unknown"}

    # Already resolved
    if entry["status"] in ("success", "failed"):
        return {"status": entry["status"]}

    # Ask NylonPay for status (this should be a quick, non-blocking call)
    try:
        payment = entry["payment_obj"]
        # NylonPay SDK usually exposes something like .status() or .check()
        # Adapt to whatever the SDK actually offers.
        raw = payment.status() if hasattr(payment, "status") else {}
        s = (raw.get("status") if isinstance(raw, dict) else str(raw)).lower()

        if s in ("successful", "success", "completed", "paid"):
            entry["status"] = "success"
            entry["raw"] = raw
        elif s in ("failed", "cancelled", "expired"):
            entry["status"] = "failed"
            entry["raw"] = raw
        # else stays pending
    except Exception as e:
        # Keep as pending on transient errors; don't fail the user
        entry["error"] = str(e)

    return {"status": entry["status"], "raw": entry.get("raw")}