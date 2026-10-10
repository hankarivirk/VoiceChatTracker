"""Small async document-store adapter for Redis/Upstash and PostgreSQL/Neon.

MongoDB remains preferred when configured. If MongoDB is absent, PostgreSQL is
selected next, then Redis. Redis primary mode is intended for small deployments:
it stores JSON documents in Redis hashes and requires a provider with persistence.
"""
import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any

# Primary identity field per collection. Documents may carry several id-like
# fields (an incident has both incident_id and group_id), so the first match in a
# generic list is NOT safe: it made every incident of a group share one key.
_PRIMARY_KEYS = {
    "groups": "group_id",
    "users": "user_id",
    "sessions": "session_id",
    "incidents": "incident_id",
    "blocked_users": "user_id",
}


def make_doc_id(collection: str, doc: dict) -> str:
    """Stable per-collection document key (an explicit _id always wins)."""
    if doc.get("_id") is not None:
        return f"_id:{doc['_id']}"
    primary = _PRIMARY_KEYS.get(collection)
    if primary and doc.get(primary) is not None:
        return f"{primary}:{doc[primary]}"
    return "hash:" + hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def direct_doc_id(collection: str, query: dict):
    """Return the doc key when a query is a single-field primary-key lookup, else None."""
    if not query or len(query) != 1:
        return None
    key, value = next(iter(query.items()))
    if key.startswith("$") or isinstance(value, (dict, list)) or value is None:
        return None
    if key == "_id" or key == _PRIMARY_KEYS.get(collection):
        return f"{key}:{value}"
    return None

def _get_path(doc, path):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur

def _set_path(doc, path, value, increment=False):
    cur = doc
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    last = parts[-1]
    if increment:
        cur[last] = (cur.get(last) or 0) + value
    else:
        cur[last] = deepcopy(value)

def _matches(doc, query):
    for key, value in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, q) for q in value): return False
        elif key == "$and":
            if not all(_matches(doc, q) for q in value): return False
        else:
            actual = _get_path(doc, key)
            if isinstance(value, dict):
                for op, expected in value.items():
                    if op == "$ne" and (actual == expected or (isinstance(actual, list) and expected in actual)): return False
                    if op == "$lt" and not (actual is not None and actual < expected): return False
                    if op == "$lte" and not (actual is not None and actual <= expected): return False
                    if op == "$gt" and not (actual is not None and actual > expected): return False
                    if op == "$gte" and not (actual is not None and actual >= expected): return False
                    if op == "$in" and actual not in expected: return False
                    if op == "$exists" and ((actual is not None) != bool(expected)): return False
            elif actual != value:
                return False
    return True

def _apply(doc, update):
    for op, fields in update.items():
        if op == "$set":
            for k,v in fields.items(): _set_path(doc,k,v)
        elif op == "$inc":
            for k,v in fields.items(): _set_path(doc,k,v,True)
        elif op == "$push":
            for k,v in fields.items():
                arr = _get_path(doc,k)
                if not isinstance(arr,list): arr=[]; _set_path(doc,k,arr)
                arr.append(deepcopy(v))
        elif op == "$addToSet":
            for k,v in fields.items():
                arr = _get_path(doc,k)
                if not isinstance(arr,list): arr=[]; _set_path(doc,k,arr)
                values = v.get("$each",[]) if isinstance(v,dict) and "$each" in v else [v]
                for item in values:
                    if item not in arr: arr.append(deepcopy(item))

class DocCursor:
    def __init__(self, items): self.items=items
    def sort(self, key, direction=1):
        self.items.sort(key=lambda x: _get_path(x,key) or 0, reverse=(direction==-1)); return self
    def limit(self,n): self.items=self.items[:max(0,int(n))]; return self
    def skip(self,n): self.items=self.items[max(0,int(n)):]; return self
    async def to_list(self,length=None): return self.items if length is None else self.items[:length]
    def __aiter__(self): self._iter=iter(self.items); return self
    async def __anext__(self):
        try: return next(self._iter)
        except StopIteration: raise StopAsyncIteration

class BaseCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        # Adapters do read-modify-write on whole documents. Serialise writers inside
        # this process so concurrent $inc/$set calls cannot overwrite each other.
        self._lock = asyncio.Lock()
    async def create_index(self,*args,**kwargs): return "adapter-index"
    async def _one(self,query):
        doc_id = direct_doc_id(self.name, query)
        if doc_id is not None:
            found = await self.db._get(self.name, doc_id)
            # A direct hit is only valid if the stored document really matches.
            return found if found is not None and _matches(found, query) else None
        for d in await self.db._all(self.name):
            if _matches(d,query): return d
        return None
    async def find_one(self,query):
        found = await self._one(query)
        return deepcopy(found) if found is not None else None
    def find(self,query=None):
        # Adapter backends load the collection asynchronously on first iteration.
        return LazyCursor(self,query or {})
    async def _find(self,query):
        return [deepcopy(d) for d in await self.db._all(self.name) if _matches(d,query)]
    async def update_one(self,query,update,upsert=False):
        async with self._lock:
            doc = await self._one(query)
            if doc is not None:
                _apply(doc,update); await self.db._put(self.name,doc); return
            if upsert:
                doc=deepcopy(query)
                _apply(doc,update)
                await self.db._put(self.name,doc)
    async def insert_one(self,doc):
        async with self._lock:
            doc=deepcopy(doc)
            if doc.get("_id") is not None and await self.db._get(self.name, f"_id:{doc['_id']}") is not None:
                raise ValueError("duplicate _id")
            primary = _PRIMARY_KEYS.get(self.name)
            if primary and doc.get(primary) is not None:
                existing = await self.db._get(self.name, f"{primary}:{doc[primary]}")
                if existing is not None:
                    raise ValueError(f"duplicate key error: {primary}")
            await self.db._put(self.name,doc)
        class R: inserted_id=doc.get("_id")
        return R()
    async def delete_one(self,query):
        async with self._lock:
            d = await self._one(query)
            if d is not None:
                await self.db._delete(self.name,d); return True
            return False
    async def delete_many(self,query):
        async with self._lock:
            matched = [d for d in await self.db._all(self.name) if _matches(d, query or {})]
            for doc in matched:
                await self.db._delete(self.name, doc)
        class Result:
            deleted_count = len(matched)
        return Result()
    async def count_documents(self,query=None):
        return sum(1 for d in await self.db._all(self.name) if _matches(d,query or {}))

class LazyCursor(DocCursor):
    def __init__(self, collection, query): self.collection=collection; self.query=query; self.items=[]; self._loaded=False
    def sort(self,key,direction=1):
        if self._loaded: super().sort(key,direction)
        else: self.sort_spec=(key,direction)
        return self
    def limit(self,n):
        if self._loaded: super().limit(n)
        else: self.limit_n=max(0,int(n))
        return self
    def skip(self,n):
        if self._loaded: super().skip(n)
        else: self.skip_n=max(0,int(n))
        return self
    async def _load(self):
        if self._loaded:return
        self.items=await self.collection._find(self.query)
        if hasattr(self,"sort_spec"): self.items.sort(key=lambda x:_get_path(x,self.sort_spec[0]) or 0,reverse=self.sort_spec[1]==-1)
        if getattr(self,"skip_n",0): self.items=self.items[self.skip_n:]
        if hasattr(self,"limit_n"): self.items=self.items[:self.limit_n]
        self._loaded=True
    async def to_list(self,length=None):
        await self._load(); return self.items if length is None else self.items[:length]
    def __aiter__(self): return self._async_iter()
    async def _async_iter(self):
        await self._load()
        for item in self.items: yield item

class BaseDatabase:
    def __init__(self,name): self.name=name; self._collections={}
    def __getitem__(self,name):
        if name not in self._collections:self._collections[name]=BaseCollection(self,name)
        return self._collections[name]

class RedisDatabase(BaseDatabase):
    def __init__(self,name,client): super().__init__(name); self.client=client; self.prefix=f"vcblogger:{name}"
    def _key(self,col): return f"{self.prefix}:{col}"
    async def _all(self,col):
        vals=await self.client.hvals(self._key(col))
        return [json.loads(v) for v in vals]
    async def _get(self,col,doc_id):
        raw=await self.client.hget(self._key(col),doc_id)
        return json.loads(raw) if raw else None
    async def _put(self,col,doc):
        await self.client.hset(self._key(col),make_doc_id(col,doc),json.dumps(doc,separators=(",",":"),default=str))
    async def _delete(self,col,doc): await self.client.hdel(self._key(col),make_doc_id(col,doc))
    async def close(self): await self.client.aclose()

class PostgresDatabase(BaseDatabase):
    def __init__(self,name,pool): super().__init__(name); self.pool=pool; self.namespace=name
    async def init(self):
        async with self.pool.acquire() as c:
            await c.execute("""CREATE TABLE IF NOT EXISTS vcblogger_documents(
                namespace TEXT NOT NULL, collection_name TEXT NOT NULL, doc_id TEXT NOT NULL,
                document JSONB NOT NULL, PRIMARY KEY(namespace,collection_name,doc_id))""")
    async def _all(self,col):
        async with self.pool.acquire() as c:
            rows=await c.fetch("SELECT document FROM vcblogger_documents WHERE namespace=$1 AND collection_name=$2",self.namespace,col)
        return [json.loads(r["document"]) if isinstance(r["document"], str) else dict(r["document"]) for r in rows]
    async def _get(self,col,doc_id):
        async with self.pool.acquire() as c:
            row=await c.fetchrow("SELECT document FROM vcblogger_documents WHERE namespace=$1 AND collection_name=$2 AND doc_id=$3",self.namespace,col,doc_id)
        if row is None: return None
        d=row["document"]
        return json.loads(d) if isinstance(d,str) else dict(d)
    async def _put(self,col,doc):
        async with self.pool.acquire() as c:
            await c.execute("""INSERT INTO vcblogger_documents(namespace,collection_name,doc_id,document)
                VALUES($1,$2,$3,$4::jsonb) ON CONFLICT(namespace,collection_name,doc_id)
                DO UPDATE SET document=EXCLUDED.document""",self.namespace,col,make_doc_id(col,doc),json.dumps(doc,default=str))
    async def _delete(self,col,doc):
        async with self.pool.acquire() as c:
            await c.execute("DELETE FROM vcblogger_documents WHERE namespace=$1 AND collection_name=$2 AND doc_id=$3",self.namespace,col,make_doc_id(col,doc))
    async def close(self): await self.pool.close()
