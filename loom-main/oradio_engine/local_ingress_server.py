"""Tiny local HTTP server for ticket/answer/evidence endpoints."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urlparse

from .ingress import judge_intent
from .ollama_ingress import generate_candidates
from .query_codec import export_packet, query_tapes_via_ingress
from . import query_codec_impl as impl
from loom.loombit_route import summarize_index


class LoomIngressApp:
    def __init__(
        self,
        *,
        tapes: str | Path,
        loombit_index: str | Path | None = None,
        loombit_dict: str | Path | None = None,
        enable_search: bool = False,
        cfg: Dict[str, Any] | None = None,
        ollama_model: str = "",
        ollama_timeout: int = 90,
        atlas_dir: str | Path | None = None,
    ) -> None:
        self.tape_path = Path(tapes)
        self.source_rows = impl.load_tapes(self.tape_path)
        self.loombit_index = str(loombit_index) if loombit_index else None
        self.loombit_dict = str(loombit_dict) if loombit_dict else None
        self.enable_search = enable_search
        self.ollama_model = str(ollama_model or "").strip()
        self.ollama_timeout = int(ollama_timeout)
        self.cfg = dict(impl.DEFAULT_CONFIG)
        if cfg:
            self.cfg.update(cfg)
        self.evidence_store: Dict[str, Dict[str, Any]] = {}
        self.atlas_dir = str(atlas_dir) if atlas_dir else None
        self._atlas = None  # lazy AtlasRetriever (loads numpy + embedding model on first use)

    def atlas_retrieve(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.atlas_dir:
            return {"error": "atlas_not_configured", "hits": [], "relations": {}}
        if self._atlas is None:
            from .atlas_retrieval import AtlasRetriever
            self._atlas = AtlasRetriever(self.atlas_dir)
        query = str(body.get("query") or "").strip()
        if not query:
            return {"error": "empty_query", "hits": [], "relations": {}}
        return self._atlas.retrieve(query, k=int(body.get("k") or 5), rel=int(body.get("rel") or 4))

    def index_summary(self) -> Dict[str, Any] | None:
        if not self.loombit_index:
            return None
        return summarize_index(self.loombit_index, dict_path=self.loombit_dict)

    def resolve_translator_payload(self, body: Dict[str, Any]) -> Any:
        if body.get("translator_payload") is not None:
            return body.get("translator_payload")
        model = str(body.get("ollama_model") or self.ollama_model or "").strip()
        if not model:
            return None
        query = str(body.get("query") or "").strip()
        if not query:
            return None
        timeout = int(body.get("ollama_timeout") or self.ollama_timeout)
        return generate_candidates(
            query,
            model=model,
            index_summary=self.index_summary(),
            timeout=timeout,
        )

    def build_ticket(self, body: Dict[str, Any]) -> Dict[str, Any]:
        query = str(body.get("query") or "").strip()
        translator = self.resolve_translator_payload(body)
        judged = judge_intent(
            query,
            self.source_rows,
            translator_payload=translator,
            loombit_index=self.loombit_index,
            loombit_dict=self.loombit_dict,
        )
        out = judged.as_dict()
        out["translator_payload"] = translator
        return out

    def build_answer(self, body: Dict[str, Any]) -> Dict[str, Any]:
        query = str(body.get("query") or "").strip()
        translator = self.resolve_translator_payload(body)
        ticket = judge_intent(
            query,
            self.source_rows,
            translator_payload=translator,
            loombit_index=self.loombit_index,
            loombit_dict=self.loombit_dict,
        )
        atlas_payload = None
        if self.atlas_dir:
            atlas_payload = self.atlas_retrieve({
                "query": ticket.accepted_query,
                "k": int(body.get("atlas_k") or 5),
                "rel": int(body.get("atlas_rel") or 4),
            })
            if atlas_payload.get("error"):
                atlas_payload = None
        # leg 1: the user's OWN tape, sent inline with the request (highest-trust local context)
        enable_search = bool(body.get("enable_search", self.enable_search))
        rows_for_engine = list(self.source_rows)
        user_tape = body.get("tape")
        if isinstance(user_tape, list):
            for i, r in enumerate(user_tape):
                try:
                    rows_for_engine.append(impl.normalize_row(r, 100000 + i, "user-tape", "source"))
                except Exception:
                    pass
        # leg 3: atlas ROUTES DuckDuckGo -- fetch web content at the resolved entity, not a blind search
        routed = ""
        if enable_search and atlas_payload and atlas_payload.get("hits"):
            routed = str(atlas_payload["hits"][0].get("title") or "").strip()
            if routed:
                try:
                    rows_for_engine.extend(impl.ddg_search_tape(routed, 1, "atlas_routed", self.cfg))
                    enable_search = False  # routed fetch done; skip blind weak-node repair
                except Exception:
                    pass
        packet = query_tapes_via_ingress(
            rows_for_engine,
            query,
            translator_payload=translator,
            enable_search=enable_search,
            cfg=self.cfg,
            loombit_index=self.loombit_index,
            loombit_dict=self.loombit_dict,
            atlas_payload=atlas_payload,
        )
        exported = export_packet(packet)
        exported["translator_payload"] = translator
        exported["atlas_retrieval"] = atlas_payload or {"hits": [], "relations": {}}
        exported["ddg_routed_query"] = routed
        counts: Dict[str, int] = {}
        for r in packet.source_rows:
            counts[r.source_kind] = counts.get(r.source_kind, 0) + 1
        exported["source_counts"] = counts
        for item in exported.get("concept_citations", []):
            self.evidence_store[str(item.get("citation_id") or "")] = item
        for item in exported.get("evidence_citations", []):
            self.evidence_store[str(item.get("citation_id") or "")] = item
        return exported

    def get_evidence(self, citation_id: str) -> Dict[str, Any]:
        key = str(citation_id or "").strip()
        if key not in self.evidence_store:
            raise KeyError(key)
        return self.evidence_store[key]


class LoomIngressHandler(BaseHTTPRequestHandler):
    server_version = "LoomIngressHTTP/0.1"

    @property
    def app(self) -> LoomIngressApp:
        return self.server.app  # type: ignore[attr-defined]

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8", errors="replace") or "{}")

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        # CORS preflight so the single-page --web surface (file:// or GitHub Pages) can POST here
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/ticket":
                self._write_json(200, self.app.build_ticket(body))
                return
            if parsed.path == "/answer":
                self._write_json(200, self.app.build_answer(body))
                return
            if parsed.path == "/retrieve":
                self._write_json(200, self.app.atlas_retrieve(body))
                return
            self._write_json(404, {"error": "unknown endpoint"})
        except Exception as exc:
            self._write_json(500, {"error": type(exc).__name__, "detail": str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/evidence/"):
            citation_id = unquote(parsed.path.split("/evidence/", 1)[1])
            try:
                self._write_json(200, self.app.get_evidence(citation_id))
            except KeyError:
                self._write_json(404, {"error": "citation_not_found", "citation_id": citation_id})
            except Exception as exc:
                self._write_json(500, {"error": type(exc).__name__, "detail": str(exc)})
            return
        self._write_json(404, {"error": "unknown endpoint"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(
    *,
    host: str,
    port: int,
    tapes: str | Path,
    loombit_index: str | Path | None = None,
    loombit_dict: str | Path | None = None,
    enable_search: bool = False,
    cfg: Dict[str, Any] | None = None,
    ollama_model: str = "",
    ollama_timeout: int = 90,
    atlas_dir: str | Path | None = None,
) -> int:
    app = LoomIngressApp(
        tapes=tapes,
        loombit_index=loombit_index,
        loombit_dict=loombit_dict,
        enable_search=enable_search,
        cfg=cfg,
        ollama_model=ollama_model,
        ollama_timeout=ollama_timeout,
        atlas_dir=atlas_dir,
    )
    server = ThreadingHTTPServer((host, port), LoomIngressHandler)
    server.app = app  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0
