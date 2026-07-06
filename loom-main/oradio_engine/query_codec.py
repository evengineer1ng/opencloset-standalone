"""Official Loom query codec API."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from . import query_codec_impl as impl
from .codec import codec_manifest, codec_manifest_dict, encode_audio, encode_text
from .answer_synthesis import (
    build_concept_citations,
    build_evidence_citations,
    build_route_trace,
    build_synthesis_plan,
)
from .ingress import judge_intent
from .packet import (
    CODEC_VERSION,
    PACKET_VERSION,
    AnswerPacket,
    AnswerTapeRow,
    ConceptCitation,
    EvidenceHit,
    EvidenceCitation,
    QuestionTapeRow,
    RenderIntent,
    RouteTraceStep,
    SourceRow,
)


def _row_to_source_row(row: impl.Row) -> SourceRow:
    return SourceRow(
        ref=row.ref_id(),
        tape_id=row.tape_id,
        row_index=row.row_index,
        actor=row.actor,
        action=row.action,
        object=row.object,
        lap=row.lap,
        priority=row.priority,
        valence=row.valence,
        source=row.source,
        source_domain=row.source_domain,
        headline=row.headline,
        thread=row.thread,
        topic=row.topic,
        time=row.time,
        kind=row.kind,
        source_kind=row.source_kind,
        tags=list(row.tags),
        raw=dict(row.raw),
    )


def _hit_to_packet_hit(hit: impl.EvidenceHit) -> EvidenceHit:
    return EvidenceHit(
        ref=hit.row.ref_id(),
        row_ref=hit.row.ref_id(),
        score=hit.score,
        token_score=hit.token_score,
        priority_score=hit.priority_score,
        chronology_score=hit.chronology_score,
        source_score=hit.source_score,
        matched_terms=list(hit.matched_terms),
        native=hit.native,
        clause=hit.row.clause(),
        source_row=_row_to_source_row(hit.row),
    )


def _render_intent(query: str, conf: impl.ConfidenceGraph, answer_form: Dict[str, Any]) -> RenderIntent:
    playback = {
        "style": "opera",
        "artic": "notes",
        "pitch": "timestamp",
        "tempo": 1.0,
        "vibrato": 0.035,
        "brightness": 1.0,
        "continuity": 0.4,
    }
    return RenderIntent(
        mode="renderer_first",
        voice="engineer" if answer_form.get("transform") == "interpretive" else "intern",
        playback=playback,
        audit={
            "query": query,
            "target_confidence": conf.total,
            "relation": conf.relation,
            "meaning": conf.meaning,
        },
    )


def _question_tape(query: str, conf: impl.ConfidenceGraph, answer_form: Dict[str, Any], rows: Sequence[impl.Row]) -> List[QuestionTapeRow]:
    focus = answer_form.get("subject") or impl.query_focus_phrase(query) or "the query"
    return [
        QuestionTapeRow(
            ref=f"question:{impl.stable_id(query)}",
            actor="user",
            action="ask",
            object=query,
            query=query,
            transform=answer_form.get("transform") or impl.infer_transform(query),
            focus=focus,
            relation=conf.relation,
            meaning=conf.meaning,
            confidence=conf.total,
            evidence_refs=[h.row.ref_id() for h in conf.evidence[:4]],
            meta={"rows_loaded": len(rows), "word_nodes": conf.words},
        )
    ]


def _answer_tape(result: Dict[str, Any], conf: impl.ConfidenceGraph) -> List[AnswerTapeRow]:
    form = result.get("diagnostics", {}).get("answer_form", {})
    claim = str(form.get("claim") or "")
    object_text = render_meaning_from_result(result)
    return [
        AnswerTapeRow(
            ref=f"answer:{impl.stable_id(claim, conf.total)}",
            actor="loom",
            action="answer",
            subject=str(form.get("subject") or ""),
            transform=str(form.get("transform") or "summary"),
            claim=claim,
            object=object_text,
            valence="calm" if conf.total >= 0.55 else "alarm",
            confidence=conf.total,
            evidence_refs=[h["ref"] for h in result.get("confidence", {}).get("top_evidence", [])[:6]],
            relation_clause=str(form.get("relation_clause") or ""),
            boundary_clause=str(form.get("boundary_clause") or ""),
            implication_clause=str(form.get("implication_clause") or ""),
            meta={"confidence_clause": str(form.get("confidence_clause") or "")},
        )
    ]


def render_meaning_from_result(result: Dict[str, Any]) -> str:
    return str(result.get("meaning") or "").strip()


def render_evidence_from_result(result: Dict[str, Any]) -> str:
    return str(result.get("evidence_text") or "").strip()


def _atlas_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug or "atlas"


def _atlas_payload_rows(query: str, atlas_payload: Dict[str, Any] | None) -> List[impl.Row]:
    if not atlas_payload:
        return []
    hits = list((atlas_payload.get("hits") or []))
    relations = dict(atlas_payload.get("relations") or {})
    rows: List[impl.Row] = []
    query_slug = _atlas_slug(query)[:40]
    tape_id = f"atlas:{query_slug}"
    for idx, hit in enumerate(hits, start=1):
        if not isinstance(hit, dict):
            continue
        title = str(hit.get("title") or "").strip()
        if not title:
            continue
        region = str(hit.get("region") or "atlas").strip() or "atlas"
        score = float(hit.get("score") or 0.0)
        tags = ["atlas", f"region:{region}"]
        for lens, names in relations.items():
            if not isinstance(names, list):
                continue
            if title in {str(name).strip() for name in names}:
                tags.append(f"lens:{lens}")
        rows.append(
            impl.Row(
                tape_id=tape_id,
                row_index=idx - 1,
                actor=title,
                action="atlas_match",
                object=f"{title} is a {region} concept",
                lap=idx,
                priority=max(0.0, min(1.0, score)),
                valence="calm",
                source="atlas_retrieval",
                source_domain="atlas.local",
                headline=title,
                thread=region,
                topic=query,
                kind="atlas_hit",
                source_kind="atlas",
                raw={
                    "atlas_query": query,
                    "atlas_title": title,
                    "atlas_region": region,
                    "atlas_score": score,
                    "atlas_relations": relations,
                    "content_actor": title,
                    "content_action": "is",
                    "content_object": f"a {region} concept",
                },
                tags=tags,
            )
        )
    for lens, names in sorted(relations.items()):
        if not isinstance(names, list) or not names:
            continue
        clean_names = [str(name).strip() for name in names if str(name).strip()]
        if not clean_names:
            continue
        idx = len(rows)
        rows.append(
            impl.Row(
                tape_id=tape_id,
                row_index=idx,
                actor="atlas",
                action="lens",
                object=", ".join(clean_names[:6]),
                lap=idx + 1,
                priority=0.32,
                valence="calm",
                source="atlas_retrieval",
                source_domain="atlas.local",
                headline=f"{lens} lens neighbors",
                thread=lens,
                topic=query,
                kind="atlas_relation",
                source_kind="atlas",
                raw={
                    "atlas_query": query,
                    "atlas_lens": lens,
                    "atlas_neighbors": clean_names,
                    "content_actor": "atlas",
                    "content_action": "related",
                    "content_object": ", ".join(clean_names[:6]),
                },
                tags=["atlas", f"lens:{lens}"],
            )
        )
    return rows


def build_packet(
    query: str,
    source_rows: Sequence[impl.Row],
    result: Dict[str, Any],
    conf: impl.ConfidenceGraph,
    working_rows: Sequence[impl.Row],
    *,
    ingress: Dict[str, Any] | None = None,
) -> AnswerPacket:
    answer_form = result.get("diagnostics", {}).get("answer_form", {})
    hits = [_hit_to_packet_hit(hit) for hit in conf.evidence[:12]]
    render = _render_intent(query, conf, answer_form)
    route_trace = build_route_trace(ingress)
    concept_citations = build_concept_citations(ingress, route_trace)
    evidence_citations = build_evidence_citations(result)
    synthesis = build_synthesis_plan(
        answer_form=answer_form,
        result=result,
        ingress=ingress,
        render_voice=render.voice,
        confidence_total=conf.total,
        concept_citations=concept_citations,
        evidence_citations=evidence_citations,
    )
    packet = AnswerPacket(
        packet_version=PACKET_VERSION,
        codec_version=CODEC_VERSION,
        query=query,
        source_rows=[_row_to_source_row(row) for row in source_rows],
        question_tape=_question_tape(query, conf, answer_form, working_rows),
        evidence_hits=hits,
        answer_tape=_answer_tape(result, conf),
        render_intent=render,
        codec_manifest=codec_manifest(),
        synthesis=synthesis,
        concept_citations=concept_citations,
        evidence_citations=evidence_citations,
        route_trace=route_trace,
        meta={
            "meaning_text": render_meaning_from_result(result),
            "evidence_text": render_evidence_from_result(result),
            "rows_loaded": len(source_rows),
            "rows_after_search": len(working_rows),
            "search_log": result.get("search_log", []),
            "confidence": result.get("confidence", {}),
            "diagnostics": result.get("diagnostics", {}),
            "text_artifact": encode_text({"answer_tape": [asdict(_answer_tape(result, conf)[0])]}),
        },
    )
    packet.meta["audio_artifact"] = encode_audio(packet)
    return packet


def export_packet(packet: AnswerPacket) -> Dict[str, Any]:
    return packet.as_dict()


def render_meaning(packet: AnswerPacket | Dict[str, Any]) -> str:
    if isinstance(packet, AnswerPacket):
        return str(packet.meta.get("meaning_text") or (packet.answer_tape[0].object if packet.answer_tape else "")).strip()
    meta = (packet or {}).get("meta", {})
    answer_tape = (packet or {}).get("answer_tape", [])
    return str(meta.get("meaning_text") or ((answer_tape[0] if answer_tape else {}).get("object")) or "").strip()


def render_evidence(packet: AnswerPacket | Dict[str, Any]) -> str:
    if isinstance(packet, AnswerPacket):
        return str(packet.meta.get("evidence_text") or "").strip()
    return str(((packet or {}).get("meta", {}) or {}).get("evidence_text") or "").strip()


def query_tapes(
    tapes: str | Path | Sequence[impl.Row],
    query: str,
    *,
    enable_search: bool = True,
    cfg: Dict[str, Any] | None = None,
) -> AnswerPacket:
    config = dict(impl.DEFAULT_CONFIG)
    if cfg:
        config.update(cfg)
    if isinstance(tapes, (str, Path)):
        source_rows = impl.load_tapes(Path(tapes))
    else:
        source_rows = list(tapes)
    result, _search_log, _search_rows, conf, working = impl.answer_once(query, list(source_rows), config, enable_search=enable_search)
    return build_packet(query, source_rows, result, conf, working)


def query_tapes_via_ingress(
    tapes: str | Path | Sequence[impl.Row],
    human_query: str,
    *,
    translator_payload: Any = None,
    enable_search: bool = True,
    cfg: Dict[str, Any] | None = None,
    loombit_index: str | Path | None = None,
    loombit_dict: str | Path | None = None,
    atlas_payload: Dict[str, Any] | None = None,
) -> AnswerPacket:
    config = dict(impl.DEFAULT_CONFIG)
    if cfg:
        config.update(cfg)
    if isinstance(tapes, (str, Path)):
        source_rows = impl.load_tapes(Path(tapes))
    else:
        source_rows = list(tapes)
    judged = judge_intent(
        human_query,
        source_rows,
        translator_payload=translator_payload,
        loombit_index=str(loombit_index) if loombit_index else None,
        loombit_dict=str(loombit_dict) if loombit_dict else None,
    )
    atlas_rows = _atlas_payload_rows(judged.accepted_query, atlas_payload)
    unified_rows = list(source_rows) + atlas_rows
    result, _search_log, _search_rows, conf, working = impl.answer_once(
        judged.accepted_query,
        unified_rows,
        config,
        enable_search=enable_search,
    )
    packet = build_packet(judged.accepted_query, unified_rows, result, conf, working, ingress=judged.as_dict())
    packet.query = human_query
    packet.meta["engine_query"] = judged.accepted_query
    packet.meta["ingress"] = judged.as_dict()
    packet.meta["rows_loaded_native"] = len(source_rows)
    packet.meta["rows_loaded_atlas"] = len(atlas_rows)
    packet.meta["atlas"] = {
        "enabled": bool(atlas_payload),
        "hit_count": len((atlas_payload or {}).get("hits") or []),
        "relation_lenses": sorted((atlas_payload or {}).get("relations", {}).keys()),
        "rows_added": len(atlas_rows),
    }
    return packet


def compat_query_packet(
    tapes: str | Path,
    query: str,
    *,
    enable_search: bool = True,
    cfg: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return export_packet(query_tapes(tapes, query, enable_search=enable_search, cfg=cfg))


def print_packet_response(packet: AnswerPacket) -> None:
    print("\nMEANING\n" + "-" * 72)
    print(render_meaning(packet))
    print("\nEVIDENCE\n" + "-" * 72)
    print(render_evidence(packet))
    search_log = packet.meta.get("search_log", [])
    if search_log:
        print("\nSEARCH / CONFIDENCE REPAIR\n" + "-" * 72)
        for item in search_log:
            print(
                f"round {item['round']}: searched {item['searched']!r} "
                f"({item['debt_type']} {item['before_node_confidence']:.2f}); "
                f"total {item['before_total_confidence']:.2f} -> {item['after_total_confidence']:.2f}; "
                f"rows +{item['rows_added']}"
            )


def build_arg_parser():
    ap = impl.argparse.ArgumentParser(description="O-Radio deterministic tape synthesis chatbot v2")
    ap.add_argument("--tapes", required=True, help="Folder or single file containing tape JSON/NDJSON/.oradio/.loom")
    ap.add_argument("--query", help="One-shot query. If omitted, starts an interactive chat loop.")
    ap.add_argument("--search", action="store_true", help="Compatibility flag; search is already enabled by default.")
    ap.add_argument("--no-search", action="store_true", help="Disable DuckDuckGo confidence repair.")
    ap.add_argument("--session", default="", help="Conversation tape NDJSON path")
    ap.add_argument("--outdir", default="", help="Directory for per-turn JSON outputs")
    ap.add_argument("--write-search-tape", action="store_true", help="Write acquired search rows to JSON")
    ap.add_argument("--write-answer-tape", action="store_true", help="Write final answer records to JSON")
    ap.add_argument("--target-confidence", type=float, default=impl.DEFAULT_CONFIG["target_confidence"])
    ap.add_argument("--max-search-rounds", type=int, default=impl.DEFAULT_CONFIG["max_search_rounds"])
    ap.add_argument("--max-results-per-search", type=int, default=impl.DEFAULT_CONFIG["max_results_per_search"])
    return ap


def _packet_records(query: str, packet: AnswerPacket, turn: int) -> List[Dict[str, Any]]:
    result = {
        "meaning": render_meaning(packet),
        "evidence_text": render_evidence(packet),
        "confidence": packet.meta.get("confidence", {}),
        "diagnostics": packet.meta.get("diagnostics", {}),
        "evidence": [hit.as_dict() for hit in packet.evidence_hits],
    }
    search_log = packet.meta.get("search_log", [])
    search_rows = []
    for row in packet.source_rows:
        if row.source_kind == "search":
            search_rows.append(impl.normalize_row(row.as_dict(), row.row_index, row.tape_id, row.source_kind))
    return impl.make_chat_records(query, result, search_log, search_rows, turn)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = dict(impl.DEFAULT_CONFIG)
    cfg["target_confidence"] = args.target_confidence
    cfg["max_search_rounds"] = args.max_search_rounds
    cfg["max_results_per_search"] = args.max_results_per_search
    tape_path = Path(args.tapes)
    source_rows = impl.load_tapes(tape_path)
    if not source_rows:
        print(f"No tape rows loaded from {tape_path}", file=sys.stderr)
        return 2
    session = Path(args.session) if args.session else Path("oradio_chat_session.ndjson")
    outdir = Path(args.outdir) if args.outdir else Path("oradio_outputs")
    search_enabled = not args.no_search
    print(f"loaded {len(source_rows)} rows from {tape_path}")
    print(f"search={'on' if search_enabled else 'off'} | session={session}")

    def handle(q: str, turn_no: int) -> None:
        packet = query_tapes(source_rows, q, enable_search=search_enabled, cfg=cfg)
        print_packet_response(packet)
        records = _packet_records(q, packet, turn_no)
        impl.append_ndjson(session, records)
        turn_id = impl.stable_id(q, turn_no, impl.time.time())
        out_path = outdir / f"turn_{turn_no:04d}_{turn_id}.json"
        impl.save_json(out_path, {
            "query": q,
            "packet": export_packet(packet),
            "chat_records": records,
        })
        if args.write_search_tape:
            search_rows = [row.as_dict() for row in packet.source_rows if row.source_kind == "search"]
            if search_rows:
                impl.save_json(outdir / f"turn_{turn_no:04d}_{turn_id}.search.tape.json", search_rows)
        if args.write_answer_tape:
            impl.save_json(outdir / f"turn_{turn_no:04d}_{turn_id}.answer.packet.json", export_packet(packet))

    if args.query:
        handle(args.query, 1)
        return 0

    turn_no = 0
    while True:
        try:
            q = input("\nquery> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            return 130
        if not q:
            continue
        if q.lower() in {"quit", "exit", ":q"}:
            return 0
        turn_no += 1
        handle(q, turn_no)


def main_compat(argv: Sequence[str] | None = None) -> int:
    return main(argv)
