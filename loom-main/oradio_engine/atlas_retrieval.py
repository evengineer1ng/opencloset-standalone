"""Embedding-atlas retrieval backend (build-time bank, runtime cosine NN).

Loads a self-contained atlas pack (doc_vectors.npy + atlas_meta.json) and answers a query with the
top hits plus lens-themed relationships -- the same shape the synth page's baked fixture returns, so
the single-page surface consumes it unchanged. The embedding model is loaded lazily and only used to
embed the (already LLM-cleaned) query + the fixed lens anchors; the documents were embedded at build
time. Pure dot products at request time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

LENS_ANCHORS = ["knowledge", "danger", "exploration", "culture", "life"]
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class AtlasRetriever:
    def __init__(self, atlas_dir: str | Path) -> None:
        import numpy as np  # local import so the engine has no hard numpy dep unless atlas is used

        self.dir = Path(atlas_dir)
        self.vecs = np.load(self.dir / "doc_vectors.npy")
        meta = json.loads((self.dir / "atlas_meta.json").read_text(encoding="utf-8"))
        self.titles: List[str] = meta["titles"]
        self.regions: List[Any] = meta["regions"]
        self._np = np
        self._model = None
        self._anchors: Dict[str, Any] | None = None

    def _embed(self, text: str):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(MODEL_NAME)
        return self._np.asarray(self._model.encode(text, normalize_embeddings=True))

    def _anchor_vecs(self) -> Dict[str, Any]:
        if self._anchors is None:
            self._anchors = {name: self._embed(name) for name in LENS_ANCHORS}
        return self._anchors

    def retrieve(self, query: str, *, k: int = 5, rel: int = 4) -> Dict[str, Any]:
        np = self._np
        qe = self._embed(query)
        sims = self.vecs @ qe
        top = [int(i) for i in np.argsort(-sims)[:k]]
        hits = [{"title": self.titles[i], "region": self.regions[i], "score": round(float(sims[i]), 3)}
                for i in top]
        gi = top[0]
        nsims = self.vecs @ self.vecs[gi]
        neigh = [int(j) for j in np.argsort(-nsims) if j != gi][:24]
        relations: Dict[str, List[str]] = {}
        for name, av in self._anchor_vecs().items():
            themed = sorted(neigh, key=lambda j: -float(self.vecs[j] @ av))[:rel]
            relations[name] = [self.titles[j] for j in themed]
        return {"query": query, "hits": hits, "relations": relations}
