
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
