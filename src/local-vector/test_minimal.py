import os, shutil, traceback
import numpy as np
from local_vector_db import LocalVectorDB

DB_DIR = "./demo_test"
if os.path.exists(DB_DIR):
    shutil.rmtree(DB_DIR)

db = LocalVectorDB(DB_DIR)
schema = {
    "title": "TEXT",
    "category": "TEXT",
    "year": "INTEGER",
    "embedding": {"type": "VECTOR", "dim": 128, "metric": "cosine"},
}
db.create_collection("articles", schema, max_elements=1000)

doc = {
    "title": "test",
    "category": "db",
    "year": 2024,
    "embedding": list(np.random.rand(128).astype(np.float32)),
}
print("adding...")
db.add("articles", doc)
print("committing...")
db.commit()

print("querying...")
try:
    res = db.query("articles", vector=list(np.random.rand(128).astype(np.float32)), k=5)
    print("result:", res)
except Exception:
    traceback.print_exc()

db.close()
print("done")
