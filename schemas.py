"""
Database Schemas

Lottery system schemas for MongoDB using Pydantic models.
Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class LotteryRound(BaseModel):
    """
    Collection: "lotteryround"
    Represents a lottery round lifecycle
    """
    round_id: str = Field(..., description="Human readable round id, e.g., R-2025-001")
    is_active: bool = Field(True, description="Whether the round is open for entries")
    entry_fee_lamports: int = Field(..., ge=0, description="Entry fee in lamports")
    treasury_address: str = Field(..., description="Solana treasury public key receiving entry fees")
    network: str = Field("devnet", description="Solana cluster: devnet/testnet/mainnet-beta")
    winner_address: Optional[str] = Field(None, description="Wallet address of the winner")
    drawn_at: Optional[datetime] = Field(None, description="When winner was drawn")

class Entry(BaseModel):
    """
    Collection: "entry"
    Represents a participant entry for a given round
    """
    round_id: str = Field(..., description="Associated lottery round id")
    wallet_address: str = Field(..., description="Participant's wallet public key")
    tx_signature: str = Field(..., description="Submitted Solana transaction signature proving payment")
    verified: bool = Field(False, description="Whether the on-chain payment was verified")
