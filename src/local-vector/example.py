import os
import shutil
import numpy as np
from local_vector_db import LocalVectorDB

DB_DIR = "./demo_db"

if __name__ == "__main__":
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

    # make one cluster center per category so the demo is deterministic
    np.random.seed(42)
    categories = {
        "database": np.random.rand(128).astype(np.float32),
        "ai": np.random.rand(128).astype(np.float32),
        "web": np.random.rand(128).astype(np.float32),
    }

    docs = []
    for category, center in categories.items():
        for i in range(5):
            vec = center + np.random.normal(0.0, 0.05, 128).astype(np.float32)
            docs.append(
                {
                    "title": f"{category} article {i + 1}",
                    "category": category,
                    "year": 2020 + i,
                    "embedding": vec.tolist(),
                }
            )

    for doc in docs:
        db.add("articles", doc)
    db.commit()

    # query vector close to the 'database' cluster
    query_vec = categories["database"] + np.random.normal(0.0, 0.02, 128).astype(np.float32)

    print("=== Pure vector search ===")
    for r in db.query("articles", vector=query_vec.tolist(), k=5):
        print(f"  {r['title']:<25} | {r['category']:<8} | distance={r['_distance']:.4f}")

    print("\n=== Filtered vector search (category=database) ===")
    for r in db.query(
        "articles",
        filters={"category": "database"},
        vector=query_vec.tolist(),
        k=3,
    ):
        print(f"  {r['title']:<25} | distance={r['_distance']:.4f}")

    print("\n=== Full-text search (title:database) ===")
    for r in db.query("articles", text="title:database"):
        print(f"  {r['title']:<25} | {r['category']}")

    print("\n=== Text + vector search (database, top 3) ===")
    for r in db.query(
        "articles",
        text="database",
        vector=query_vec.tolist(),
        k=3,
    ):
        print(f"  {r['title']:<25} | distance={r['_distance']:.4f}")

    db.close()
