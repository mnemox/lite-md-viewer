import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np
import hnswlib


class LocalVectorDB:
    """A tiny, single-file-like local database with scalar indexes,
    full-text search, and hnswlib-backed ANN vector search.

    Documents may carry a caller-supplied ``doc_id`` so an external system can own the
    identity of a row and update it in place via :meth:`upsert` without the id ever
    changing. Updating a vector re-adds it under the same hnswlib label, which overwrites
    the point rather than tombstoning it, so repeated updates do not grow the graph.
    """

    def __init__(self, path: str = "./local_vec_db"):
        self.path = path
        os.makedirs(path, exist_ok=True)

        self.db_path = os.path.join(path, "metadata.db")
        # check_same_thread=False: writes arrive from a background indexer thread while
        # reads are served on the request thread. All access is funnelled through _lock.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._indexes: Dict[Tuple[str, str], hnswlib.Index] = {}

        # Indexes touched since the last commit. Saving an .hnsw file rewrites it whole,
        # so only the ones that actually changed are written.
        self._dirty: Set[Tuple[str, str]] = set()

        # Reentrant: public write methods call each other (upsert -> _add_vector).
        self._lock = threading.RLock()

        self._ensure_meta_tables()
        self._load_schemas()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _ensure_meta_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _collections (
                name TEXT PRIMARY KEY,
                schema TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _vector_meta (
                collection TEXT,
                field TEXT,
                count INTEGER DEFAULT 0,
                max_elements INTEGER,
                PRIMARY KEY (collection, field)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _vectors (
                collection TEXT,
                field TEXT,
                doc_id INTEGER,
                vec BLOB,
                PRIMARY KEY (collection, field, doc_id)
            )
            """
        )
        self.conn.commit()

    def _load_schemas(self) -> None:
        self._schemas.clear()
        for row in self.conn.execute("SELECT name, schema FROM _collections"):
            self._schemas[row["name"]] = json.loads(row["schema"])

    @staticmethod
    def _q(name: str) -> str:
        """Quote an SQL identifier."""
        return '"' + name.replace('"', '""') + '"'

    @staticmethod
    def _sql_type(field_type: Union[str, Dict[str, Any]]) -> Optional[str]:
        """Return the SQLite column type for a scalar field, None for vectors."""
        if isinstance(field_type, dict):
            return None
        t = field_type.upper()
        if t == "TEXT":
            return "TEXT"
        if t in ("INT", "INTEGER"):
            return "INTEGER"
        if t in ("FLOAT", "REAL", "DOUBLE"):
            return "REAL"
        raise ValueError(f"Unknown field type: {field_type}")

    @staticmethod
    def _is_vector(field_type: Any) -> bool:
        return isinstance(field_type, dict) and field_type.get("type", "").upper() == "VECTOR"

    @staticmethod
    def _is_text(field_type: Any) -> bool:
        return isinstance(field_type, str) and field_type.upper() == "TEXT"

    def _table_name(self, collection: str) -> str:
        return f"t_{collection}"

    def _fts_name(self, collection: str) -> str:
        return f"fts_{collection}"

    def _vector_path(self, collection: str, field: str) -> str:
        return os.path.join(self.path, f"{collection}_{field}.hnsw")

    # ------------------------------------------------------------------
    # collection management
    # ------------------------------------------------------------------
    def create_collection_if_missing(
        self, name: str, schema: Dict[str, Any], max_elements: int = 10000
    ) -> bool:
        """Create the collection unless it already exists. True when it was created."""
        with self._lock:
            if name in self._schemas:
                return False
            self.create_collection(name, schema, max_elements=max_elements)
            return True

    def create_collection(
        self, name: str, schema: Dict[str, Any], max_elements: int = 10000
    ) -> None:
        """
        schema examples:
            {
                "title": "TEXT",
                "year": "INTEGER",
                "category": "TEXT",
                "embedding": {"type": "VECTOR", "dim": 384, "metric": "cosine"}
            }
        """
        if name in self._schemas:
            raise ValueError(f"Collection '{name}' already exists")

        # validate
        for field, ftype in schema.items():
            if self._is_vector(ftype):
                dim = ftype.get("dim")
                metric = ftype.get("metric", "cosine")
                if not isinstance(dim, int) or dim <= 0:
                    raise ValueError(f"Vector field '{field}' needs a positive 'dim'")
                if metric not in ("l2", "cosine", "ip"):
                    raise ValueError(f"Unsupported metric '{metric}' for '{field}'")
                ftype.setdefault("max_elements", max_elements)

        self._schemas[name] = schema
        self.conn.execute(
            "INSERT INTO _collections (name, schema) VALUES (?, ?)",
            (name, json.dumps(schema)),
        )

        # main table for scalar / text fields
        main_cols = ["doc_id INTEGER PRIMARY KEY AUTOINCREMENT"]
        text_fields = []
        for field, ftype in schema.items():
            if self._is_vector(ftype):
                continue
            sql_t = self._sql_type(ftype)
            main_cols.append(f"{self._q(field)} {sql_t}")
            if self._is_text(ftype):
                text_fields.append(field)

        self.conn.execute(
            f"CREATE TABLE {self._q(self._table_name(name))} ({', '.join(main_cols)})"
        )

        # scalar indexes
        for field, ftype in schema.items():
            if self._is_vector(ftype):
                continue
            self.conn.execute(
                f"CREATE INDEX {self._q(f'idx_{name}_{field}')} "
                f"ON {self._q(self._table_name(name))} ({self._q(field)})"
            )

        # full-text index for text fields
        if text_fields:
            cols = ", ".join(self._q(f) for f in text_fields)
            try:
                self.conn.execute(
                    f"CREATE VIRTUAL TABLE {self._q(self._fts_name(name))} USING fts5({cols})"
                )
            except sqlite3.OperationalError:
                # fallback to fts4 if fts5 isn't enabled in this build
                self.conn.execute(
                    f"CREATE VIRTUAL TABLE {self._q(self._fts_name(name))} USING fts4({cols})"
                )

        # vector indexes
        for field, ftype in schema.items():
            if not self._is_vector(ftype):
                continue
            me = ftype.get("max_elements", max_elements)
            self.conn.execute(
                "INSERT INTO _vector_meta (collection, field, count, max_elements) VALUES (?, ?, 0, ?)",
                (name, field, me),
            )
            self._init_vector_index(name, field, create=True)

        self.conn.commit()
        self.commit()

    def _init_vector_index(self, collection: str, field: str, create: bool = False) -> hnswlib.Index:
        key = (collection, field)
        if key in self._indexes:
            return self._indexes[key]

        schema = self._schemas[collection][field]
        dim = schema["dim"]
        metric = schema.get("metric", "cosine")
        max_elements = schema.get("max_elements", 10000)

        index = hnswlib.Index(space=metric, dim=dim)
        path = self._vector_path(collection, field)

        if os.path.exists(path):
            index.load_index(path, max_elements=max_elements)
        else:
            index.init_index(max_elements=max_elements, ef_construction=200, M=16)
            # Nothing on disk yet, so this empty index still needs a first save.
            self._dirty.add(key)

        index.set_ef(50)
        self._indexes[key] = index
        return index

    def _save_indexes(self) -> None:
        for key in list(self._dirty):
            index = self._indexes.get(key)
            if index is None:
                self._dirty.discard(key)
                continue
            collection, field = key
            index.save_index(self._vector_path(collection, field))
            self._dirty.discard(key)

    # ------------------------------------------------------------------
    # write path
    # ------------------------------------------------------------------
    def _split_doc(
        self, collection: str, doc: Dict[str, Any]
    ) -> Tuple[List[str], List[Any], List[str], List[str], Dict[str, List[float]]]:
        """Sort a document's supplied fields into scalar / text / vector buckets.

        Text fields are returned separately because they are written twice: to the main
        table, which is the durable source of truth, and to the FTS index, which is a
        derived structure that can be rebuilt from it.
        """
        schema = self._schemas[collection]

        scalar_fields: List[str] = []
        scalar_values: List[Any] = []
        text_fields: List[str] = []
        text_values: List[str] = []
        vectors: Dict[str, List[float]] = {}

        for field, ftype in schema.items():
            value = doc.get(field)
            if value is None:
                continue
            if self._is_vector(ftype):
                vectors[field] = self._validate_vector(field, ftype, value)
            elif self._is_text(ftype):
                text_fields.append(field)
                text_values.append(str(value))
            else:
                scalar_fields.append(field)
                scalar_values.append(value)

        return scalar_fields, scalar_values, text_fields, text_values, vectors

    def add(
        self, collection: str, doc: Dict[str, Any], doc_id: Optional[int] = None
    ) -> int:
        """Insert a new document.

        When ``doc_id`` is given the row is created with that exact id, letting an
        external system own document identity; otherwise SQLite assigns one.
        """
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")

        with self._lock:
            scalar_fields, scalar_values, text_fields, text_values, vectors = \
                self._split_doc(collection, doc)

            # Text columns exist on the main table too, so persist them there as well as
            # in the FTS index -- otherwise the stored row could never be read back.
            main_fields = scalar_fields + text_fields
            main_values = scalar_values + list(text_values)
            if doc_id is not None:
                main_fields = ["doc_id"] + main_fields
                main_values = [doc_id] + main_values

            table = self._q(self._table_name(collection))
            if main_fields:
                cols = ", ".join(self._q(f) for f in main_fields)
                placeholders = ", ".join("?" for _ in main_fields)
                cur = self.conn.execute(
                    f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", main_values
                )
            else:
                cur = self.conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
            new_id = doc_id if doc_id is not None else cur.lastrowid

            if text_fields:
                cols = ", ".join(["rowid"] + [self._q(f) for f in text_fields])
                placeholders = ", ".join("?" for _ in text_fields)
                self.conn.execute(
                    f"INSERT INTO {self._q(self._fts_name(collection))} ({cols}) "
                    f"VALUES (?, {placeholders})",
                    [new_id] + text_values,
                )

            for field, vec in vectors.items():
                self._add_vector(collection, field, new_id, vec)

            self.conn.commit()
            return new_id

    def upsert(self, collection: str, doc_id: int, doc: Dict[str, Any]) -> int:
        """Insert the document, or update it in place if ``doc_id`` already exists.

        Only the fields present in ``doc`` are written; omitted scalar columns keep their
        current value. Vectors are re-added under the same hnswlib label, which overwrites
        the point in place -- no tombstone, no growth in the graph however many times a
        document is revised.
        """
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")

        with self._lock:
            table = self._q(self._table_name(collection))
            exists = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE doc_id=?", (doc_id,)
            ).fetchone() is not None

            if not exists:
                return self.add(collection, doc, doc_id=doc_id)

            scalar_fields, scalar_values, text_fields, text_values, vectors = \
                self._split_doc(collection, doc)

            main_fields = scalar_fields + text_fields
            main_values = scalar_values + list(text_values)
            if main_fields:
                assignments = ", ".join(f"{self._q(f)} = ?" for f in main_fields)
                self.conn.execute(
                    f"UPDATE {table} SET {assignments} WHERE doc_id = ?",
                    main_values + [doc_id],
                )

            # fts4 and fts5 disagree about updating a row in place, so the row is deleted
            # and reinserted. The values are re-read from the main table (already updated
            # above) so that text fields absent from `doc` keep their previous content.
            all_text_fields = [
                f for f, t in self._schemas[collection].items() if self._is_text(t)
            ]
            if all_text_fields:
                cols = ", ".join(self._q(f) for f in all_text_fields)
                row = self.conn.execute(
                    f"SELECT {cols} FROM {table} WHERE doc_id=?", (doc_id,)
                ).fetchone()
                merged = ["" if row[f] is None else str(row[f]) for f in all_text_fields]
                fts = self._q(self._fts_name(collection))
                self.conn.execute(f"DELETE FROM {fts} WHERE rowid=?", (doc_id,))
                cols_ins = ", ".join(["rowid"] + [self._q(f) for f in all_text_fields])
                placeholders = ", ".join("?" for _ in all_text_fields)
                self.conn.execute(
                    f"INSERT INTO {fts} ({cols_ins}) VALUES (?, {placeholders})",
                    [doc_id] + merged,
                )

            for field, vec in vectors.items():
                self._add_vector(collection, field, doc_id, vec)

            self.conn.commit()
            return doc_id

    def _validate_vector(
        self, field: str, ftype: Dict[str, Any], value: Any
    ) -> List[float]:
        vec = list(value)
        dim = ftype["dim"]
        if len(vec) != dim:
            raise ValueError(
                f"Vector '{field}' expected dim {dim}, got {len(vec)}"
            )
        return vec

    def _add_vector(
        self, collection: str, field: str, doc_id: int, vector: List[float]
    ) -> None:
        index = self._init_vector_index(collection, field)

        row = self.conn.execute(
            "SELECT count, max_elements FROM _vector_meta WHERE collection=? AND field=?",
            (collection, field),
        ).fetchone()
        count, max_elements = row["count"], row["max_elements"]

        # Replacing an existing vector consumes no new capacity and must not inflate the
        # count -- otherwise repeated updates would keep doubling max_elements forever.
        is_new = self.conn.execute(
            "SELECT 1 FROM _vectors WHERE collection=? AND field=? AND doc_id=?",
            (collection, field, doc_id),
        ).fetchone() is None

        if is_new and count >= max_elements:
            max_elements = max(2, max_elements * 2)
            index.resize_index(max_elements)
            self._schemas[collection][field]["max_elements"] = max_elements
            self.conn.execute(
                "UPDATE _vector_meta SET max_elements=? WHERE collection=? AND field=?",
                (max_elements, collection, field),
            )
            self.conn.execute(
                "UPDATE _collections SET schema=? WHERE name=?",
                (json.dumps(self._schemas[collection]), collection),
            )

        arr = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        ids = np.array([doc_id], dtype=np.int64)
        # Re-adding an existing label overwrites that point in place.
        index.add_items(arr, ids=ids)
        self._dirty.add((collection, field))

        self.conn.execute(
            "INSERT OR REPLACE INTO _vectors (collection, field, doc_id, vec) VALUES (?, ?, ?, ?)",
            (collection, field, doc_id, arr.tobytes()),
        )
        if is_new:
            self.conn.execute(
                "UPDATE _vector_meta SET count=count+1 WHERE collection=? AND field=?",
                (collection, field),
            )

    def delete(self, collection: str, doc_id: int) -> None:
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")

        with self._lock:
            self.conn.execute(
                f"DELETE FROM {self._q(self._table_name(collection))} WHERE doc_id=?",
                (doc_id,),
            )

            schema = self._schemas[collection]
            text_fields = [f for f, t in schema.items() if self._is_text(t)]
            if text_fields:
                self.conn.execute(
                    f"DELETE FROM {self._q(self._fts_name(collection))} WHERE rowid=?",
                    (doc_id,),
                )

            for field, ftype in schema.items():
                if not self._is_vector(ftype):
                    continue
                try:
                    index = self._init_vector_index(collection, field)
                    index.mark_deleted(doc_id)
                    self._dirty.add((collection, field))
                except Exception:
                    pass
                cur = self.conn.execute(
                    "DELETE FROM _vectors WHERE collection=? AND field=? AND doc_id=?",
                    (collection, field, doc_id),
                )
                if cur.rowcount:
                    self.conn.execute(
                        "UPDATE _vector_meta SET count=MAX(count-1, 0) "
                        "WHERE collection=? AND field=?",
                        (collection, field),
                    )

            self.conn.commit()

    def delete_many(self, collection: str, doc_ids: List[int]) -> None:
        """Delete several documents under a single lock acquisition."""
        with self._lock:
            for doc_id in doc_ids:
                self.delete(collection, doc_id)

    def rebuild_index(self, collection: str, field: str) -> int:
        """Rebuild a vector index from the ``_vectors`` table, which is the source of truth.

        Compacts the tombstones left by :meth:`delete` and recovers from a missing or
        corrupt ``.hnsw`` file. Returns the number of vectors reinserted.
        """
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")
        ftype = self._schemas[collection].get(field)
        if not self._is_vector(ftype):
            raise ValueError(f"'{field}' is not a vector field of '{collection}'")

        with self._lock:
            rows = self.conn.execute(
                "SELECT doc_id, vec FROM _vectors WHERE collection=? AND field=?",
                (collection, field),
            ).fetchall()

            dim = ftype["dim"]
            metric = ftype.get("metric", "cosine")
            capacity = max(2, len(rows) * 2)

            index = hnswlib.Index(space=metric, dim=dim)
            index.init_index(max_elements=capacity, ef_construction=200, M=16)
            index.set_ef(50)

            if rows:
                ids = np.asarray([r["doc_id"] for r in rows], dtype=np.int64)
                buf = bytearray()
                for r in rows:
                    buf.extend(r["vec"])
                vecs = np.frombuffer(bytes(buf), dtype=np.float32).reshape(len(rows), dim)
                index.add_items(vecs, ids=ids)

            self._indexes[(collection, field)] = index
            self._dirty.add((collection, field))

            self._schemas[collection][field]["max_elements"] = capacity
            self.conn.execute(
                "UPDATE _vector_meta SET count=?, max_elements=? "
                "WHERE collection=? AND field=?",
                (len(rows), capacity, collection, field),
            )
            self.conn.execute(
                "UPDATE _collections SET schema=? WHERE name=?",
                (json.dumps(self._schemas[collection]), collection),
            )
            self.conn.commit()
            return len(rows)

    # ------------------------------------------------------------------
    # read path
    # ------------------------------------------------------------------
    def query(
        self,
        collection: str,
        filters: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        vector: Optional[List[float]] = None,
        vector_field: Optional[str] = None,
        k: int = 10,
        k_multiplier: int = 10,
    ) -> List[Dict[str, Any]]:
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")

        with self._lock:
            schema = self._schemas[collection]

            if vector is not None:
                vector_field = self._resolve_vector_field(schema, vector_field)

            candidate_ids: Optional[set] = None

            if filters:
                candidate_ids = self._filter_rowids(collection, filters)

            # Without a vector to rank by, relevance order is all the caller has, so the
            # matches are read back in bm25 order rather than as an unordered set.
            if text and vector is None:
                ranked_ids = self._search_text_ranked(collection, text)
                if candidate_ids is not None:
                    ranked_ids = [i for i in ranked_ids if i in candidate_ids]
                return self._fetch_in_order(collection, ranked_ids[:k])

            if text:
                # Here the vector supplies the ranking and the match set is only a filter,
                # so it must stay unbounded.
                text_ids = self._search_text(collection, text)
                candidate_ids = text_ids if candidate_ids is None else candidate_ids & text_ids

            if vector is None:
                return self._fetch_by_ids(collection, candidate_ids, text=text, limit=k)

            # vector search
            if candidate_ids is not None and len(candidate_ids) <= 1000:
                ranked = self._exact_vector_search(collection, vector_field, vector, candidate_ids, k)
            else:
                ranked = self._ann_vector_search(
                    collection, vector_field, vector, candidate_ids, k, k_multiplier
                )

            return self._fetch_ranked(collection, ranked)

    def get(self, collection: str, doc_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single document's scalar/text fields by id, or None if absent."""
        if collection not in self._schemas:
            raise ValueError(f"Collection '{collection}' does not exist")
        with self._lock:
            row = self.conn.execute(
                f"SELECT * FROM {self._q(self._table_name(collection))} WHERE doc_id=?",
                (doc_id,),
            ).fetchone()
            return dict(row) if row else None

    def has_collection(self, name: str) -> bool:
        return name in self._schemas

    def _resolve_vector_field(self, schema: Dict[str, Any], field: Optional[str]) -> str:
        vectors = [f for f, t in schema.items() if self._is_vector(t)]
        if not vectors:
            raise ValueError("No vector field defined in this collection")
        if field:
            if field not in vectors:
                raise ValueError(f"Vector field '{field}' not found")
            return field
        if len(vectors) > 1:
            raise ValueError(f"Multiple vector fields: {vectors}. Specify vector_field")
        return vectors[0]

    def _filter_rowids(self, collection: str, filters: Dict[str, Any]) -> set:
        clauses = []
        values = []
        for field, value in filters.items():
            clause, vals = self._build_filter_clause(field, value)
            clauses.append(clause)
            values.extend(vals)

        where = " AND ".join(clauses)
        sql = f"SELECT doc_id FROM {self._q(self._table_name(collection))} WHERE {where}"
        return {row["doc_id"] for row in self.conn.execute(sql, values)}

    def _build_filter_clause(self, field: str, value: Any) -> Tuple[str, List[Any]]:
        qf = self._q(field)
        if isinstance(value, dict):
            parts = []
            vals = []
            allowed = {">": ">", ">=": ">=", "<": "<", "<=": "<=",
                       "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
                       "eq": "="}
            for op, v in value.items():
                op_sql = allowed.get(op)
                if op_sql is None:
                    raise ValueError(f"Unsupported filter operator: {op}")
                parts.append(f"{qf} {op_sql} ?")
                vals.append(v)
            return " AND ".join(parts), vals

        if isinstance(value, (list, tuple)) and len(value) == 2:
            # range [low, high] inclusive
            return f"{qf} BETWEEN ? AND ?", list(value)

        return f"{qf} = ?", [value]

    def _require_text_fields(self, collection: str) -> None:
        schema = self._schemas[collection]
        if not any(self._is_text(t) for t in schema.values()):
            raise ValueError(
                f"Collection '{collection}' has no TEXT fields to full-text search"
            )

    def _search_text(self, collection: str, text: str) -> set:
        """Every rowid matching the query, unordered -- for use as a candidate filter."""
        self._require_text_fields(collection)
        fts = self._q(self._fts_name(collection))
        sql = f"SELECT rowid FROM {fts} WHERE {fts} MATCH ?"
        return {row["rowid"] for row in self.conn.execute(sql, (text,))}

    def _search_text_ranked(self, collection: str, text: str) -> List[int]:
        """Matching rowids, best first.

        fts5's bm25() is negative and increases with relevance, so ascending order puts
        the strongest match first. fts4 has no bm25(), so it keeps the engine's own order.
        """
        self._require_text_fields(collection)
        fts = self._q(self._fts_name(collection))
        try:
            return [
                row["rowid"] for row in self.conn.execute(
                    f"SELECT rowid FROM {fts} WHERE {fts} MATCH ? ORDER BY bm25({fts})",
                    (text,),
                )
            ]
        except sqlite3.OperationalError:
            return [
                row["rowid"] for row in self.conn.execute(
                    f"SELECT rowid FROM {fts} WHERE {fts} MATCH ?", (text,)
                )
            ]

    def _fetch_by_ids(
        self,
        collection: str,
        ids: Optional[set],
        text: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        table = self._q(self._table_name(collection))
        if ids is None:
            sql = f"SELECT * FROM {table} LIMIT ?"
            params = (limit,)
        else:
            if not ids:
                return []
            placeholders = ", ".join("?" for _ in ids)
            sql = f"SELECT * FROM {table} WHERE doc_id IN ({placeholders}) LIMIT ?"
            params = tuple(ids) + (limit,)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _fetch_in_order(self, collection: str, ids: List[int]) -> List[Dict[str, Any]]:
        """Read rows by id, preserving the caller's ordering rather than the table's."""
        if not ids:
            return []
        table = self._q(self._table_name(collection))
        placeholders = ", ".join("?" for _ in ids)
        found = {
            row["doc_id"]: dict(row)
            for row in self.conn.execute(
                f"SELECT * FROM {table} WHERE doc_id IN ({placeholders})", tuple(ids)
            )
        }
        return [found[i] for i in ids if i in found]

    def _ann_vector_search(
        self,
        collection: str,
        field: str,
        vector: List[float],
        candidate_ids: Optional[set],
        k: int,
        k_multiplier: int,
    ) -> List[Tuple[int, float]]:
        index = self._init_vector_index(collection, field)
        query = np.asarray(vector, dtype=np.float32).reshape(1, -1)

        count = index.get_current_count()
        if count == 0:
            return []

        ef = max(1, min(k * k_multiplier, 1000))
        top_k = min(ef, count)
        index.set_ef(max(ef, top_k))

        try:
            labels, distances = index.knn_query(query, k=top_k, num_threads=1)
        except RuntimeError:
            all_ids = {
                row["doc_id"]
                for row in self.conn.execute(
                    "SELECT doc_id FROM _vectors WHERE collection=? AND field=?",
                    (collection, field),
                )
            }
            if candidate_ids is not None:
                all_ids &= candidate_ids
            return self._exact_vector_search(collection, field, vector, all_ids, k)

        labels = labels[0]
        distances = distances[0]

        ranked = []
        for label, dist in zip(labels, distances):
            label = int(label)
            if candidate_ids is not None and label not in candidate_ids:
                continue
            ranked.append((label, float(dist)))

        if len(ranked) < k and candidate_ids is not None:
            # fallback to exact search over the remaining candidates
            remaining = candidate_ids - {label for label, _ in ranked}
            if remaining:
                exact = self._exact_vector_search(collection, field, vector, remaining, k)
                seen = {label for label, _ in ranked}
                for label, dist in exact:
                    if label not in seen:
                        ranked.append((label, dist))

        return ranked[:k]

    def _exact_vector_search(
        self,
        collection: str,
        field: str,
        vector: List[float],
        candidate_ids: set,
        k: int,
    ) -> List[Tuple[int, float]]:
        if not candidate_ids:
            return []

        ids, vecs = self._load_vectors(collection, field, candidate_ids)
        if ids.size == 0:
            return []

        query = np.asarray(vector, dtype=np.float32)
        metric = self._schemas[collection][field].get("metric", "cosine")

        if metric == "l2":
            diff = vecs - query
            dists = np.einsum("ij,ij->i", diff, diff)
        elif metric == "cosine":
            norms_v = np.linalg.norm(vecs, axis=1)
            norm_q = np.linalg.norm(query)
            denom = norms_v * norm_q
            denom[denom == 0] = 1e-12
            dists = 1.0 - (vecs @ query) / denom
        else:  # ip
            dists = 1.0 - (vecs @ query)

        order = np.argsort(dists)[:k]
        return [(int(ids[i]), float(dists[i])) for i in order]

    def _load_vectors(
        self, collection: str, field: str, doc_ids: set
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Load raw vectors for the given doc_ids from SQLite."""
        dim = self._schemas[collection][field]["dim"]
        ids: List[int] = []
        buf = bytearray()

        id_list = list(doc_ids)
        chunk = 900  # stay under SQLite's variable limit
        for start in range(0, len(id_list), chunk):
            part = id_list[start:start + chunk]
            placeholders = ", ".join("?" for _ in part)
            sql = (
                "SELECT doc_id, vec FROM _vectors "
                f"WHERE collection=? AND field=? AND doc_id IN ({placeholders})"
            )
            for row in self.conn.execute(sql, [collection, field] + part):
                ids.append(row["doc_id"])
                buf.extend(row["vec"])

        if not ids:
            return np.empty(0, dtype=np.int64), np.empty((0, dim), dtype=np.float32)

        vecs = np.frombuffer(bytes(buf), dtype=np.float32).reshape(len(ids), dim)
        return np.asarray(ids, dtype=np.int64), vecs

    def _fetch_ranked(self, collection: str, ranked: List[Tuple[int, float]]) -> List[Dict[str, Any]]:
        if not ranked:
            return []

        table = self._q(self._table_name(collection))
        placeholders = ", ".join("?" for _ in ranked)
        sql = f"SELECT * FROM {table} WHERE doc_id IN ({placeholders})"
        rows = {row["doc_id"]: dict(row) for row in self.conn.execute(sql, tuple(id for id, _ in ranked))}

        results = []
        for doc_id, dist in ranked:
            row = rows.get(doc_id)
            if row:
                row["_distance"] = dist
                results.append(row)
        return results

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def commit(self) -> None:
        """Persist pending SQLite work and save any vector index that actually changed."""
        with self._lock:
            self._save_indexes()
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.commit()
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
