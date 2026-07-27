# local-vector

A tiny local database that stores documents on disk and supports three kinds of retrieval in one query:

- **Scalar property lookups** — indexed equality and range filters
- **Full-text search** — SQLite FTS5 over `TEXT` fields, ranked by `bm25`
- **Vector similarity search** — HNSW approximate nearest neighbours via `hnswlib`

Documents can be written under **caller-supplied ids** and updated in place, so an
application that owns its own primary keys (a chunk table, say) can keep them in sync with
the index without it growing on every edit.

Everything lives in a single directory: a SQLite file for documents, scalar indexes, the FTS index and raw vectors, plus one `.hnsw` file per vector field.

## Architecture

| Concern | Implementation |
| --- | --- |
| Document storage | SQLite table `t_<collection>`, one row per document, `doc_id` primary key |
| Scalar indexes | SQLite B-tree index per non-vector field |
| Full-text search | SQLite `fts5` virtual table `fts_<collection>` (falls back to `fts4`) |
| Vector storage | `_vectors` table, float32 blobs — the source of truth |
| ANN index | `hnswlib.Index` persisted to `<collection>_<field>.hnsw` |
| Query planning | Small filters → exact rescoring; otherwise ANN + post-filter |
| Concurrency | One `threading.Lock` around every read and write path |

Because raw vectors are kept in SQLite, the HNSW file is a pure accelerator: it can always be rebuilt from the database.

## Install

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Usage

```python
from local_vector_db import LocalVectorDB

db = LocalVectorDB("./my_db")

db.create_collection(
    "articles",
    {
        "title": "TEXT",
        "category": "TEXT",
        "year": "INTEGER",
        "embedding": {"type": "VECTOR", "dim": 384, "metric": "cosine"},
    },
    max_elements=10000,
)

db.add("articles", {
    "title": "Building a local vector database",
    "category": "database",
    "year": 2024,
    "embedding": my_embedding,   # list of 384 floats
})
db.commit()
```

### Stable ids and in-place updates

`add()` accepts an explicit `doc_id`, and `upsert()` writes under an id you choose,
creating the document or replacing it in place:

```python
db.upsert("articles", 42, {"title": "Revised", "embedding": new_embedding})
```

Repeated `upsert()` calls on the same id do **not** grow the vector index — the HNSW entry
for that label is overwritten. Fields omitted from the document keep their previous values,
so you can refresh a vector without restating the metadata.

`create_collection_if_missing()` makes start-up idempotent, and `get(collection, doc_id)`
reads a single document back by id.

### Querying

```python
# 1. Pure ANN vector search
db.query("articles", vector=q, k=10)

# 2. Filtered vector search
db.query("articles", filters={"category": "database"}, vector=q, k=5)

# 3. Range filter
db.query("articles", filters={"year": {"gte": 2022, "lte": 2024}})

# 4. Full-text search, best match first
db.query("articles", text="vector database")

# 5. Full-text + vector
db.query("articles", text="database", vector=q, k=5)
```

Results are dictionaries of the stored fields. Vector queries also include `_distance`.

A **text-only** query is returned in `bm25` relevance order, so `k` keeps the strongest
matches rather than an arbitrary slice. When a vector is supplied as well, the vector
supplies the ranking and the text match acts purely as a candidate filter.

### Schema reference

| Declaration | Meaning |
| --- | --- |
| `"TEXT"` | Indexed text column, also added to the FTS index |
| `"INTEGER"` / `"FLOAT"` | Indexed scalar column |
| `{"type": "VECTOR", "dim": N, "metric": "cosine"}` | Vector field; metric is `cosine`, `l2` or `ip` |

### Filter operators

`eq`, `gt`, `gte`, `lt`, `lte`, or a two-element list for an inclusive `BETWEEN` range. A bare value means equality.

## Maintenance

`delete()` tombstones the HNSW entry rather than reclaiming it, so after many deletions
call `rebuild_index(collection, field)`. It rebuilds from the `_vectors` table, compacting
tombstones and recovering from a missing or corrupt `.hnsw` file, and returns the number of
vectors reinserted.

`commit()` only rewrites the `.hnsw` files that actually changed since the last commit.

## Query planning

- **Filters or text only** — resolved entirely in SQLite.
- **Vector with a small candidate set (<= 1000)** — exact brute-force distances over those candidates, so recall is perfect.
- **Vector with a large or absent candidate set** — HNSW `knn_query` with `ef = k * k_multiplier`, then post-filtering. If post-filtering leaves fewer than `k` hits, the remaining candidates are rescored exactly.

## Producing vectors

Any embedding model works. For text:

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")   # 384 dimensions
vector = model.encode("some text").tolist()
```

## Run the demo

```powershell
.venv\Scripts\python example.py
```

## Limitations

- Single process. Threads within it are safe (all access is serialised through one lock),
  but two processes must not open the same directory for writing.
- Deletes are tombstoned in the HNSW graph; call `rebuild_index()` after many deletions.
- The HNSW index must fit in RAM.
- `commit()` rewrites the whole `.hnsw` file, so batch your writes.
- `fts4` builds have no `bm25()`, so text results fall back to the engine's own order.
