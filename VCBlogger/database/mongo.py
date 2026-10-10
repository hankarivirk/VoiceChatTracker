"""MongoDB primary-store connection management for VCBlogger."""
from typing import Optional, Any

from ..config import MONGO_URI, DATABASE_NAME, POSTGRES_ARCHIVE_URL, REDIS_URL, select_db_provider
from ..utils.logging import logger

_client: Optional[Any] = None
_db: Optional[Any] = None


class InMemoryCollection:
    """Small in-memory adapter used only by unit tests (never as production fallback)."""
    def __init__(self, name: str):
        self.name = name
        self.docs = []

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in (query or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                for op, value in expected.items():
                    if op == "$ne" and (actual == value or (isinstance(actual, list) and value in actual)): return False
                    if op == "$lt" and not (actual is not None and actual < value): return False
                    if op == "$lte" and not (actual is not None and actual <= value): return False
                    if op == "$gt" and not (actual is not None and actual > value): return False
                    if op == "$gte" and not (actual is not None and actual >= value): return False
                    if op == "$in" and actual not in value: return False
                    if op == "$exists" and ((key in doc) != bool(value)): return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, query: dict):
        for doc in self.docs:
            if self._matches(doc, query):
                return dict(doc)
        return None

    def find(self, query: dict = None):
        query = query or {}
        results = [dict(d) for d in self.docs if self._matches(d, query)]
        class Cursor:
            def __init__(self, items): self._items = items
            def sort(self, key, direction=1):
                self._items.sort(key=lambda x: x.get(key, 0), reverse=(direction == -1)); return self
            def limit(self, n): self._items = self._items[:n]; return self
            def skip(self, n): self._items = self._items[n:]; return self
            async def to_list(self, length=None): return self._items if length is None else self._items[:length]
            def __aiter__(self): self._iter = iter(self._items); return self
            async def __anext__(self):
                try: return next(self._iter)
                except StopIteration: raise StopAsyncIteration
        return Cursor(results)

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        target = None
        for doc in self.docs:
            if self._matches(doc, query):
                target = doc
                break
        if target is None and upsert:
            target = dict(query)
            self.docs.append(target)
        if target is not None:
            def set_path(doc, key, value, increment=False):
                parts = key.split(".")
                node = doc
                for part in parts[:-1]: node = node.setdefault(part, {})
                last = parts[-1]
                if increment: node[last] = node.get(last, 0) + value
                else: node[last] = value
            for operator, fields in update.items():
                if operator == "$set":
                    for k, v in fields.items(): set_path(target, k, v)
                elif operator == "$inc":
                    for k, v in fields.items(): set_path(target, k, v, increment=True)
                elif operator == "$push":
                    for k, v in fields.items(): target.setdefault(k, []).append(v)
                elif operator == "$addToSet":
                    for k, v in fields.items():
                        items = target.setdefault(k, [])
                        values = v.get("$each", []) if isinstance(v, dict) and "$each" in v else [v]
                        for item in values:
                            if item not in items: items.append(item)
        return target

    async def insert_one(self, doc: dict):
        d = dict(doc)
        if "_id" not in d: d["_id"] = str(len(self.docs) + 1)
        existing = next((x for x in self.docs if x.get("_id") == d["_id"]), None)
        if existing is not None:
            raise ValueError("Duplicate _id in in-memory test collection")
        self.docs.append(d)
        class Result:
            inserted_id = d["_id"]
        return Result()

    async def delete_one(self, query: dict):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, query):
                del self.docs[i]
                return True
        return False

    async def delete_many(self, query: dict):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not self._matches(doc, query or {})]
        class Result:
            deleted_count = before - len(self.docs)
        return Result()

    async def count_documents(self, query: dict = None):
        query = query or {}
        return sum(1 for d in self.docs if self._matches(d, query))


class InMemoryDatabase:
    """Tiny mock database; enabled explicitly only when VCBLOGGER_TEST_MODE=1."""
    def __init__(self, name: str):
        self.name = name
        self.collections = {}
    def __getitem__(self, name: str):
        if name not in self.collections: self.collections[name] = InMemoryCollection(name)
        return self.collections[name]


async def _ensure_indexes(db) -> None:
    """Create each index independently so one legacy conflict doesn't skip the rest."""
    specs = [
        ("groups", "group_id", {"unique": True}),
        ("users", "user_id", {"unique": True}),
        ("sessions", "session_id", {"unique": True}),
        ("sessions", [("group_id", 1), ("is_active", 1)], {}),
        ("sessions", [("group_id", 1), ("start_time", -1)], {}),
        ("incidents", "incident_id", {"unique": True}),
        ("incidents", [("group_id", 1), ("timestamp", -1)], {}),
        ("attendance", [("session_id", 1), ("user_id", 1)], {"unique": True}),
        ("attendance", [("user_id", 1), ("group_id", 1)], {}),
        ("attendance", "recorded_at", {}),
        ("voice_time_segments", "user_id", {}),
        ("voice_time_segments", [ ("group_id", 1), ("timestamp", -1)], {}),
        ("voice_time_segments", "timestamp", {}),
        ("blocked_users", "user_id", {"unique": True}),
    ]
    for collection, keys, options in specs:
        try:
            await db[collection].create_index(keys, **options)
        except Exception as exc:
            # Continue creating useful indexes when old data contains duplicates.
            logger.warning("MongoDB index setup skipped for %s %s: %s", collection, keys, exc)


async def get_db():
    """Select the first configured persistent provider unless DB_PROVIDER pins one."""
    global _client, _db
    if _db is not None:
        return _db
    import os
    if os.getenv("VCBLOGGER_TEST_MODE") == "1" and not any((
        MONGO_URI, POSTGRES_ARCHIVE_URL, REDIS_URL
    )):
        _db = InMemoryDatabase(DATABASE_NAME)
        return _db

    provider = select_db_provider()
    if not provider:
        raise RuntimeError("No database configured. Set MONGO_URI, POSTGRES_URL, or REDIS_URL.")
    try:
        if provider == "mongodb":
            if not MONGO_URI: raise RuntimeError("DB_PROVIDER=mongodb but MONGO_URI is empty.")
            from motor.motor_asyncio import AsyncIOMotorClient
            _client = AsyncIOMotorClient(
                MONGO_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                socketTimeoutMS=10000, retryWrites=True,
            )
            await _client.admin.command("ping")
            _db = _client[DATABASE_NAME]
            await _ensure_indexes(_db)
        elif provider == "postgres":
            if not POSTGRES_ARCHIVE_URL: raise RuntimeError("DB_PROVIDER=postgres but POSTGRES_URL is empty.")
            import asyncpg
            from .document_backends import PostgresDatabase
            pool = await asyncpg.create_pool(dsn=POSTGRES_ARCHIVE_URL, min_size=1, max_size=5, timeout=8)
            _db = PostgresDatabase(DATABASE_NAME, pool)
            await _db.init()
        elif provider == "redis":
            if not REDIS_URL: raise RuntimeError("DB_PROVIDER=redis but REDIS_URL is empty.")
            from redis.asyncio import Redis
            from .document_backends import RedisDatabase
            client = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
            await client.ping()
            _db = RedisDatabase(DATABASE_NAME, client)
        else:
            raise RuntimeError(f"Unsupported database provider: {provider}")
        logger.info("Connected to selected primary database provider: %s", provider)
        return _db
    except Exception as exc:
        logger.error("Primary database connection failed (%s): %s", provider, exc)
        try:
            if _client is not None: _client.close()
        except Exception: pass
        _client = None
        _db = None
        raise RuntimeError(f"Database provider '{provider}' is unavailable; refusing volatile production storage.") from exc


async def ping() -> bool:
    import os
    if _db is not None and os.getenv("VCBLOGGER_TEST_MODE") == "1":
        return True
    try:
        db = await get_db()
        provider = select_db_provider()
        if provider == "mongodb":
            await _client.admin.command("ping")
        elif provider == "postgres":
            async with _db.pool.acquire() as conn: await conn.fetchval("SELECT 1")
        elif provider == "redis":
            await _db.client.ping()
        return True
    except Exception:
        return False


async def close() -> None:
    global _client, _db
    try:
        if _client is not None: _client.close()
        elif _db is not None and hasattr(_db, "close"): await _db.close()
    except Exception as exc:
        logger.debug("Database close warning: %s", exc)
    _client = None
    _db = None
