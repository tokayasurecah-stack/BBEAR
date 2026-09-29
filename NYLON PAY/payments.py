import uuid, os, threading
from nylonpay import create_nylon_pay
from dotenv import load_dotenv

load_dotenv()

# reference -> {email, network, phone, amount, status, raw}
_pending: dict = {}


def get_client():
    return create_nylon_pay(
        api_key=os.getenv("NYLON_API_KEY"),
        api_secret=os.getenv("NYLON_API_SECRET"),
    )


def _wait_in_background(reference: str, payment):
    """
    Runs in a daemon thread. Blocks on payment.wait() until NylonPay
    resolves the payment (user enters PIN, or it times out), then
    updates the in-memory entry. This is how the SDK is meant to be used.
    """
    try:
        print(f"[nylon] waiting on {reference}...")
        result = payment.wait()  # blocks until PIN entered or timeout
        print(f"[nylon] {reference} resolved: {result}")

        # Figure out success/failure from whatever NylonPay returns
        if isinstance(result, dict):
            status_raw = str(result.get("status", "")).lower()
        else:
            status_raw = str(result).lower()

        success_states = ("successful", "success", "completed", "paid", "confirmed")
        failure_states = ("failed", "cancelled", "canceled", "expired", "rejected")

        if any(s in status_raw for s in success_states):
            _pending[reference]["status"] = "success"
        elif any(s in status_raw for s in failure_states):
            _pending[reference]["status"] = "failed"
        else:
            # Unrecognized status — assume failed, log for debugging
            _pending[reference]["status"] = "failed"

        _pending[reference]["raw"] = result

    except Exception as e:
        print(f"[nylon] {reference} error: {e}")
        _pending[reference]["status"] = "failed"
        _pending[reference]["raw"] = {"error": str(e)}


def initiate(amount: int, phone: str, network: str, email: str) -> dict:
    """Start a payment. Spawn a background thread that blocks on wait()."""
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
            "email": email,
            "network": network,
            "phone": phone,
            "amount": amount,
            "status": "pending",
        }

        # Spawn a daemon thread that waits for the user to finish the PIN
        threading.Thread(
            target=_wait_in_background,
            args=(ref, payment),
            daemon=True,
        ).start()

        return {"reference": ref, "status": "pending"}

    except Exception as e:
        _pending[ref] = {"email": email, "status": "failed", "error": str(e)}
        return {"reference": ref, "status": "failed", "error": str(e)}


def check_status(reference: str) -> dict:
    """Non-blocking: just reads the in-memory status updated by the thread."""
    entry = _pending.get(reference)
    if not entry:
        return {"status": "unknown"}
    return {"status": entry["status"], "raw": entry.get("raw")}
