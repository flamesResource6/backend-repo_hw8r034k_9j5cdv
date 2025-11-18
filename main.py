import os
import random
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Models ----------
class CreateRoundRequest(BaseModel):
    round_id: str = Field(..., description="Human readable round id")
    entry_fee_lamports: int = Field(..., ge=0)
    treasury_address: str = Field(..., description="SOL receiving wallet")
    network: str = Field("devnet", description="devnet | testnet | mainnet-beta")


class EnterRequest(BaseModel):
    wallet_address: str
    tx_signature: str


# ---------- Helpers ----------
NETWORK_ENDPOINTS = {
    "devnet": "https://api.devnet.solana.com",
    "testnet": "https://api.testnet.solana.com",
    "mainnet-beta": "https://api.mainnet-beta.solana.com",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_round(round_id: str) -> dict:
    docs = list(db["lotteryround"].find({"round_id": round_id}).limit(1))
    if not docs:
        raise HTTPException(status_code=404, detail="Round not found")
    r = docs[0]
    r["_id"] = str(r["_id"])  # make JSON serializable
    return r


def list_entries(round_id: str) -> List[dict]:
    entries = list(db["entry"].find({"round_id": round_id}))
    for e in entries:
        e["_id"] = str(e["_id"])
    return entries


def verify_signature_on_chain(network: str, tx_signature: str, expected_wallet: Optional[str] = None, expected_treasury: Optional[str] = None) -> bool:
    """
    Minimal verification using Solana JSON-RPC.
    - Confirms the signature is found and not an error
    - Optionally checks if both wallet and treasury appear in account keys
    Note: This is a lightweight demo verification and not production-grade.
    """
    endpoint = NETWORK_ENDPOINTS.get(network, NETWORK_ENDPOINTS["devnet"])
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [tx_signature, {"encoding": "json"}],
        }
        resp = requests.post(endpoint, json=payload, timeout=10)
        data = resp.json()
        if "result" not in data or data["result"] is None:
            return False
        result = data["result"]
        meta = result.get("meta")
        if meta and meta.get("err") is not None:
            return False
        # Basic account presence checks (best-effort)
        tx = result.get("transaction", {})
        message = tx.get("message", {})
        account_keys = message.get("accountKeys", [])
        if expected_wallet and expected_wallet not in account_keys:
            return False
        if expected_treasury and expected_treasury not in account_keys:
            return False
        return True
    except Exception:
        return False


# ---------- Routes ----------
@app.get("/")
def read_root():
    return {"message": "Solana Lottery Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


@app.post("/api/rounds")
def create_round(body: CreateRoundRequest):
    # Ensure unique round_id
    existing = db["lotteryround"].find_one({"round_id": body.round_id})
    if existing:
        raise HTTPException(status_code=400, detail="round_id already exists")
    doc = {
        "round_id": body.round_id,
        "is_active": True,
        "entry_fee_lamports": body.entry_fee_lamports,
        "treasury_address": body.treasury_address,
        "network": body.network,
        "winner_address": None,
        "drawn_at": None,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    inserted_id = db["lotteryround"].insert_one(doc).inserted_id
    doc["_id"] = str(inserted_id)
    return doc


@app.get("/api/rounds")
def list_rounds():
    rounds = list(db["lotteryround"].find().sort("created_at", -1))
    for r in rounds:
        r["_id"] = str(r["_id"])
    return rounds


@app.get("/api/rounds/{round_id}")
def get_round_detail(round_id: str):
    return get_round(round_id)


@app.get("/api/rounds/{round_id}/entries")
def get_round_entries(round_id: str):
    get_round(round_id)  # ensure exists
    return list_entries(round_id)


@app.post("/api/rounds/{round_id}/enter")
def enter_round(round_id: str, body: EnterRequest):
    r = get_round(round_id)
    if not r.get("is_active", False):
        raise HTTPException(status_code=400, detail="Round is closed")

    # Prevent duplicate tx entries
    existing = db["entry"].find_one({"tx_signature": body.tx_signature})
    if existing:
        raise HTTPException(status_code=400, detail="This transaction was already submitted")

    verified = verify_signature_on_chain(r.get("network", "devnet"), body.tx_signature, body.wallet_address, r.get("treasury_address"))

    entry_doc = {
        "round_id": round_id,
        "wallet_address": body.wallet_address,
        "tx_signature": body.tx_signature,
        "verified": bool(verified),
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    inserted_id = db["entry"].insert_one(entry_doc).inserted_id
    entry_doc["_id"] = str(inserted_id)
    return entry_doc


@app.post("/api/rounds/{round_id}/verify/{tx_signature}")
def reverify_entry(round_id: str, tx_signature: str):
    r = get_round(round_id)
    entry = db["entry"].find_one({"round_id": round_id, "tx_signature": tx_signature})
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    verified = verify_signature_on_chain(r.get("network", "devnet"), tx_signature, entry.get("wallet_address"), r.get("treasury_address"))
    db["entry"].update_one({"_id": entry["_id"]}, {"$set": {"verified": bool(verified), "updated_at": now_utc()}})
    entry = db["entry"].find_one({"_id": entry["_id"]})
    entry["_id"] = str(entry["_id"])
    return entry


@app.post("/api/rounds/{round_id}/draw")
def draw_winner(round_id: str):
    r = get_round(round_id)
    if not r.get("is_active", False):
        raise HTTPException(status_code=400, detail="Round already closed")

    verified_entries = list(db["entry"].find({"round_id": round_id, "verified": True}))
    if not verified_entries:
        raise HTTPException(status_code=400, detail="No verified entries to draw from")

    winner = random.choice(verified_entries)

    db["lotteryround"].update_one(
        {"round_id": round_id},
        {"$set": {"winner_address": winner.get("wallet_address"), "drawn_at": now_utc(), "is_active": False, "updated_at": now_utc()}},
    )

    updated = get_round(round_id)
    return {"round": updated, "winner": {"wallet_address": winner.get("wallet_address"), "tx_signature": winner.get("tx_signature")}}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
