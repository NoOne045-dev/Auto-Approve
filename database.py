"""
database.py — Async MongoDB database driver using Motor.
Clean, scalable document storage for users, channels, stats, and captchas.
"""

import datetime
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import config
from config import LOGGER

UTC = datetime.timezone.utc


class MongoDatabase:
    """
    Motor Async MongoDB wrapper with auto-indexing and fast querying.
    """

    def __init__(self, uri: str, db_name: str = "AutoApproveBot"):
        self.uri = uri
        self.db_name = db_name
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def connect(self) -> None:
        if not self.uri:
            LOGGER.error("CRITICAL: MONGO_URI is missing in environment or .env file!")
            return
        
        LOGGER.info(f"Connecting to MongoDB database '{self.db_name}'...")
        self.client = AsyncIOMotorClient(self.uri)
        self.db = self.client[self.db_name]

        # Ensure indexes for fast lookup and uniqueness
        await self.db.users.create_index("user_id", unique=True)
        await self.db.chats.create_index("chat_id", unique=True)
        await self.db.captchas.create_index([("user_id", 1), ("chat_id", 1)], unique=True)
        LOGGER.info("Connected to MongoDB successfully and verified indexes.")

    async def close(self) -> None:
        if self.client:
            self.client.close()
            LOGGER.info("Closed MongoDB connection.")

    # ================= User Operations =================
    async def upsert_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        is_premium: bool = False,
    ) -> None:
        now = datetime.datetime.now(UTC)
        await self.db.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_premium": is_premium,
                    "is_blocked": False,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.users.find_one({"user_id": user_id})

    async def users_count(self) -> int:
        return await self.db.users.count_documents({})

    async def all_user_ids(self) -> List[int]:
        docs = await self.db.users.find({"is_blocked": {"$ne": True}}, {"user_id": 1}).to_list(length=None)
        return [d["user_id"] for d in docs if "user_id" in d]

    async def set_blocked(self, user_id: int, blocked: bool = True) -> None:
        await self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"is_blocked": blocked, "updated_at": datetime.datetime.now(UTC)}},
        )

    # ================= Channel / Chat Operations =================
    async def get_chat(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.chats.find_one({"chat_id": chat_id})

    async def set_chat(self, chat_id: int, data: Dict[str, Any]) -> None:
        data["chat_id"] = chat_id
        data["updated_at"] = datetime.datetime.now(UTC)
        await self.db.chats.update_one(
            {"chat_id": chat_id},
            {"$set": data, "$setOnInsert": {"created_at": datetime.datetime.now(UTC)}},
            upsert=True,
        )

    async def update_chat_key(self, chat_id: int, key: str, value: Any) -> None:
        await self.db.chats.update_one(
            {"chat_id": chat_id},
            {"$set": {key: value, "updated_at": datetime.datetime.now(UTC)}},
            upsert=True,
        )

    async def all_chats(self, owner_id: Optional[int] = None) -> List[Dict[str, Any]]:
        query = {}
        if owner_id:
            query = {"$or": [{"owner_id": owner_id}, {"admins": owner_id}]}
        return await self.db.chats.find(query).to_list(length=None)

    async def delete_chat(self, chat_id: int) -> bool:
        res = await self.db.chats.delete_one({"chat_id": chat_id})
        return res.deleted_count > 0

    async def bump_stat(self, chat_id: int, approved: bool = True) -> None:
        field = "stats.approved" if approved else "stats.rejected"
        await self.db.chats.update_one(
            {"chat_id": chat_id},
            {"$inc": {field: 1}},
            upsert=True,
        )

    # ================= Captcha Operations =================
    async def set_captcha(
        self,
        user_id: int,
        chat_id: int,
        answer: str,
        kind: str,
        msg_id: Optional[int] = None,
        invite_link: Optional[str] = None,
    ) -> None:
        doc = {
            "user_id": user_id,
            "chat_id": chat_id,
            "answer": answer,
            "kind": kind,
            "msg_id": msg_id,
            "invite_link": invite_link,
            "created_at": datetime.datetime.now(UTC),
        }
        await self.db.captchas.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": doc},
            upsert=True,
        )

    async def get_captcha(self, user_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        return await self.db.captchas.find_one({"user_id": user_id, "chat_id": chat_id})

    async def del_captcha(self, user_id: int, chat_id: int) -> None:
        await self.db.captchas.delete_one({"user_id": user_id, "chat_id": chat_id})

    # ================= Global Bot Stats =================
    async def global_stats(self) -> Dict[str, Any]:
        users = await self.users_count()
        chats = await self.db.chats.count_documents({})
        
        pipeline = [
            {
                "$group": {
                    "_id": None,
                    "approved": {"$sum": "$stats.approved"},
                    "rejected": {"$sum": "$stats.rejected"},
                }
            }
        ]
        agg = await self.db.chats.aggregate(pipeline).to_list(length=1)
        approved = agg[0]["approved"] if agg else 0
        rejected = agg[0]["rejected"] if agg else 0

        return {
            "users": users,
            "chats": chats,
            "approved": approved,
            "rejected": rejected,
        }


# Singleton database instance
db = MongoDatabase(uri=config.MONGO_URI, db_name=config.DATABASE_NAME)
