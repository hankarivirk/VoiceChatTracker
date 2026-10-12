
from VCBlogger.database.document_backends import _matches, _apply, _get_path

def test_document_backend_matches_nested_query():
    doc = {"user_id": 7, "group_sessions": {"-1001": 120}}
    assert _matches(doc, {"user_id": 7})
    assert _matches(doc, {"group_sessions.-1001": 120})
    assert not _matches(doc, {"user_id": 8})

def test_document_backend_update_operators():
    doc = {"total_duration": 5, "labels": ["a"], "profile": {"name": "old"}}
    _apply(doc, {"$inc": {"total_duration": 3}, "$set": {"profile.name": "new"},
                 "$push": {"labels": "b"}, "$addToSet": {"labels": {"$each": ["b", "c"]}}})
    assert doc["total_duration"] == 8
    assert _get_path(doc, "profile.name") == "new"
    assert doc["labels"] == ["a", "b", "c"]

def test_document_backend_or_query():
    assert _matches({"group_id": 1}, {"$or": [{"group_id": 1}, {"group_id": 2}]})


def test_database_provider_auto_priority(monkeypatch):
    import VCBlogger.config as config
    monkeypatch.setattr(config, "DB_PROVIDER", "auto")
    monkeypatch.setattr(config, "MONGO_URI", "")
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "")
    monkeypatch.setattr(config, "REDIS_URL", "rediss://example")
    assert config.select_db_provider() == "redis"
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "postgresql://example")
    assert config.select_db_provider() == "postgres"
    monkeypatch.setattr(config, "MONGO_URI", "mongodb://example")
    assert config.select_db_provider() == "mongodb"

def test_database_provider_can_be_pinned(monkeypatch):
    import VCBlogger.config as config
    monkeypatch.setattr(config, "DB_PROVIDER", "postgres")
    monkeypatch.setattr(config, "MONGO_URI", "mongodb://example")
    monkeypatch.setattr(config, "POSTGRES_ARCHIVE_URL", "postgresql://example")
    assert config.select_db_provider() == "postgres"


def test_in_memory_delete_many_removes_matching_rows():
    import asyncio
    from VCBlogger.database.mongo import InMemoryCollection
    async def run():
        collection = InMemoryCollection("test")
        await collection.insert_one({"user_id": 1})
        await collection.insert_one({"user_id": 2})
        result = await collection.delete_many({"user_id": 1})
        assert result.deleted_count == 1
        assert await collection.count_documents({}) == 1
    asyncio.run(run())


def test_document_backend_exists_distinguishes_missing_from_explicit_null():
    assert _matches({"profile": {"username": None}}, {"profile.username": {"$exists": True}})
    assert _matches({"profile": {}}, {"profile.username": {"$exists": False}})
    assert not _matches({"profile": {"username": None}}, {"profile.username": {"$exists": False}})


def test_document_backend_in_and_nin_match_array_members():
    assert _matches({"blocked_ids": [7, 8]}, {"blocked_ids": {"$in": [8, 10]}})
    assert not _matches({"blocked_ids": [7, 8]}, {"blocked_ids": {"$in": [10]}})
    assert _matches({"blocked_ids": [7, 8]}, {"blocked_ids": {"$nin": [10]}})
    assert not _matches({"blocked_ids": [7, 8]}, {"blocked_ids": {"$nin": [8]}})


def test_document_backend_comparison_with_mixed_legacy_types_does_not_crash():
    assert not _matches({"timestamp": "unknown"}, {"timestamp": {"$gte": 100}})


def test_document_backend_query_upsert_does_not_persist_range_operators():
    import asyncio
    from VCBlogger.database.document_backends import BaseCollection

    class MemoryDatabase:
        def __init__(self):
            self.storage = {}

        async def _get(self, collection, doc_id):
            return self.storage.get((collection, doc_id))

        async def _all(self, collection):
            return [doc for (name, _), doc in self.storage.items() if name == collection]

        async def _put(self, collection, doc):
            from VCBlogger.database.document_backends import make_doc_id
            self.storage[(collection, make_doc_id(collection, doc))] = dict(doc)

        async def _delete(self, collection, doc):
            from VCBlogger.database.document_backends import make_doc_id
            self.storage.pop((collection, make_doc_id(collection, doc)), None)

    async def run():
        db = MemoryDatabase()
        collection = BaseCollection(db, "users")
        await collection.update_one(
            {"user_id": 99, "timestamp": {"$gte": 100}},
            {"$set": {"first_name": "Alex"}}, upsert=True,
        )
        row = await collection.find_one({"user_id": 99})
        assert row == {"user_id": 99, "first_name": "Alex"}
        assert await collection.count_documents({}) == 1

    asyncio.run(run())


def test_document_backend_updates_legacy_hash_key_without_leaving_duplicate():
    import asyncio
    from VCBlogger.database.document_backends import BaseCollection, make_doc_id

    class MemoryDatabase:
        def __init__(self):
            self.storage = {}

        async def _get(self, collection, doc_id):
            return self.storage.get((collection, doc_id))

        async def _all(self, collection):
            return [doc for (name, _), doc in self.storage.items() if name == collection]

        async def _put(self, collection, doc):
            self.storage[(collection, make_doc_id(collection, doc))] = dict(doc)

        async def _delete(self, collection, doc):
            self.storage.pop((collection, make_doc_id(collection, doc)), None)

    async def run():
        db = MemoryDatabase()
        original = {"label": "before"}
        db.storage[("custom", make_doc_id("custom", original))] = dict(original)
        collection = BaseCollection(db, "custom")
        await collection.update_one({"label": "before"}, {"$set": {"label": "after"}})
        assert await collection.count_documents({}) == 1
        updated = await collection.find_one({"label": "after"})
        assert updated["label"] == "after"

    asyncio.run(run())


def test_document_backend_logical_key_finds_and_updates_legacy_explicit_id_rows():
    import asyncio
    from VCBlogger.database.document_backends import BaseCollection, make_doc_id

    class MemoryDatabase:
        def __init__(self):
            self.storage = {}

        async def _get(self, collection, doc_id):
            return self.storage.get((collection, doc_id))

        async def _all(self, collection):
            return [doc for (name, _), doc in self.storage.items() if name == collection]

        async def _put(self, collection, doc):
            self.storage[(collection, make_doc_id(collection, doc))] = dict(doc)

        async def _delete(self, collection, doc):
            self.storage.pop((collection, make_doc_id(collection, doc)), None)

    async def run():
        db = MemoryDatabase()
        db.storage[("users", "_id:legacy")]= {"_id": "legacy", "user_id": 7, "first_name": "Old"}
        collection = BaseCollection(db, "users")
        await collection.update_one({"user_id": 7}, {"$set": {"first_name": "New"}})
        assert (await collection.find_one({"user_id": 7}))["first_name"] == "New"
        assert await collection.count_documents({"user_id": 7}) == 1
        try:
            await collection.insert_one({"_id": "another", "user_id": 7})
        except ValueError as exc:
            assert "duplicate key" in str(exc)
        else:
            raise AssertionError("duplicate logical primary keys should be rejected")

    asyncio.run(run())
