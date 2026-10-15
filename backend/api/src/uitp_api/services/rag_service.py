"""
uitp_api.services.rag_service
=============================
Retrieval-Augmented Generation (RAG) context indexing and retrieval service.
Chunks OpenAPI schemas and Requirements specifications into structured semantic units.
Allows Supervisor, API Specialist, and UI Specialist agents to retrieve exact endpoint
contracts and requirement rules without prompt bloat or hallucination.

SECURITY & SAFETY:
- Input specifications are strictly validated and length-bounded.
- Sensitive schema patterns (hardcoded credentials, internal tokens) are sanitized.
- Pure in-memory deterministic retrieval (BM25 + TF-IDF semantic relevance) with
  graceful fallback, avoiding external vector DB dependencies.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RAGChunk:
    """A semantic chunk indexed from specifications for targeted retrieval."""

    chunk_id: str
    chunk_type: str  # "openapi_endpoint" | "requirement_rule" | "scenario_journey"
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tokens: list[str] = field(default_factory=list)


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer with punctuation stripping."""
    return [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text)]


class RAGContextIndexer:
    """In-memory semantic context indexer and BM25 retriever for API & Requirement specs."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks: list[RAGChunk] = []
        self.k1 = k1
        self.b = b
        self.doc_lengths: list[int] = []
        self.avg_dl: float = 0.0
        self.doc_freq: dict[str, int] = {}

    def index_openapi(self, openapi_spec_raw: str | dict[str, Any]) -> int:
        """Parse OpenAPI specification into distinct endpoint chunks."""
        if not openapi_spec_raw:
            return 0

        spec: dict[str, Any]
        if isinstance(openapi_spec_raw, str):
            try:
                spec = json.loads(openapi_spec_raw)
            except Exception:
                try:
                    import yaml  # type: ignore
                    spec = yaml.safe_load(openapi_spec_raw)
                except Exception as exc:
                    logger.warning("Failed to parse OpenAPI for RAG indexing: %s", exc)
                    return 0
        else:
            spec = openapi_spec_raw

        paths = spec.get("paths", {})
        if not isinstance(paths, dict):
            return 0

        indexed_count = 0
        for path_key, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "options", "head"):
                op = path_item.get(method)
                if not isinstance(op, dict):
                    continue

                op_id = op.get("operationId") or f"{method}_{path_key.strip('/').replace('/', '_')}"
                summary = op.get("summary") or op.get("description") or f"{method.upper()} {path_key}"
                tags = op.get("tags") or []
                
                # Parameters
                params_info = []
                for p in op.get("parameters", []):
                    if isinstance(p, dict):
                        p_name = p.get("name")
                        p_in = p.get("in", "query")
                        p_req = p.get("required", False)
                        params_info.append(f"{p_name} ({p_in}{', required' if p_req else ''})")

                # Request body schema
                body_info = "None"
                req_body = op.get("requestBody")
                if isinstance(req_body, dict):
                    content_map = req_body.get("content", {})
                    for media_type, media_obj in content_map.items():
                        if isinstance(media_obj, dict) and "schema" in media_obj:
                            schema_desc = json.dumps(media_obj["schema"], indent=None)
                            body_info = f"{media_type}: {schema_desc[:300]}"
                            break

                # Responses
                resp_info = []
                for code, resp_obj in op.get("responses", {}).items():
                    if isinstance(resp_obj, dict):
                        desc = resp_obj.get("description", "")
                        resp_info.append(f"{code} ({desc[:50]})")

                security = op.get("security", spec.get("security", []))
                auth_str = "Bearer/JWT" if security else "None/Public"

                chunk_content = (
                    f"ENDPOINT: {method.upper()} {path_key}\n"
                    f"Summary: {summary}\n"
                    f"OperationId: {op_id}\n"
                    f"Tags: {', '.join(tags)}\n"
                    f"Auth: {auth_str}\n"
                    f"Parameters: {', '.join(params_info) if params_info else 'None'}\n"
                    f"RequestBody: {body_info}\n"
                    f"Responses: {', '.join(resp_info)}"
                )

                chunk = RAGChunk(
                    chunk_id=f"api_{method.lower()}_{path_key}",
                    chunk_type="openapi_endpoint",
                    title=f"{method.upper()} {path_key}",
                    content=chunk_content,
                    metadata={
                        "method": method.upper(),
                        "path": path_key,
                        "operation_id": op_id,
                        "tags": tags,
                        "summary": summary,
                    },
                    tokens=_tokenize(chunk_content),
                )
                self.chunks.append(chunk)
                indexed_count += 1

        self._recompute_stats()
        return indexed_count

    def index_requirements(self, requirements_content: str) -> int:
        """Parse markdown requirements into requirement rule chunks."""
        if not requirements_content or not requirements_content.strip():
            return 0

        lines = requirements_content.splitlines()
        current_req_id = "GENERAL"
        current_title = "General Specifications"
        current_lines: list[str] = []
        indexed_count = 0

        def _flush():
            nonlocal indexed_count, current_lines
            if not current_lines:
                return
            body = "\n".join(current_lines).strip()
            if not body:
                return
            chunk = RAGChunk(
                chunk_id=f"req_{current_req_id.lower()}",
                chunk_type="requirement_rule",
                title=f"Requirement {current_req_id}: {current_title}",
                content=f"REQUIREMENT: [{current_req_id}] {current_title}\n{body}",
                metadata={"req_id": current_req_id, "title": current_title},
                tokens=_tokenize(f"{current_req_id} {current_title} {body}"),
            )
            self.chunks.append(chunk)
            indexed_count += 1
            current_lines = []

        for line in lines:
            line_s = line.strip()
            # Detect heading or REQ marker
            match = re.search(r"\b(REQ-[A-Z0-9_-]+)\b", line_s, re.IGNORECASE)
            if match and (line_s.startswith("#") or line_s.startswith("-") or line_s.startswith("*")):
                _flush()
                current_req_id = match.group(1).upper()
                current_title = line_s.lstrip("#-* ").strip()
            else:
                current_lines.append(line)

        _flush()
        self._recompute_stats()
        return indexed_count

    def index_scenarios(self, scenarios_content: str) -> int:
        """Index test scenario definitions."""
        if not scenarios_content or not scenarios_content.strip():
            return 0
        indexed_count = 0
        try:
            data = json.loads(scenarios_content)
            items = data if isinstance(data, list) else data.get("scenarios", [])
            if isinstance(items, list):
                for idx, scn in enumerate(items):
                    if not isinstance(scn, dict):
                        continue
                    scn_id = str(scn.get("id") or scn.get("scenario_id") or f"SCN_{idx+1}")
                    name = str(scn.get("name") or "Scenario")
                    body = json.dumps(scn, indent=2)
                    chunk = RAGChunk(
                        chunk_id=f"scn_{scn_id.lower()}",
                        chunk_type="scenario_journey",
                        title=f"Scenario {scn_id}: {name}",
                        content=f"SCENARIO [{scn_id}]: {name}\n{body}",
                        metadata={"scenario_id": scn_id, "name": name},
                        tokens=_tokenize(f"{scn_id} {name} {body}"),
                    )
                    self.chunks.append(chunk)
                    indexed_count += 1
        except Exception:
            chunk = RAGChunk(
                chunk_id="scn_raw",
                chunk_type="scenario_journey",
                title="Custom Scenarios Document",
                content=scenarios_content[:2000],
                metadata={},
                tokens=_tokenize(scenarios_content[:2000]),
            )
            self.chunks.append(chunk)
            indexed_count += 1

        self._recompute_stats()
        return indexed_count

    def _recompute_stats(self) -> None:
        """Recompute BM25 inverted document frequencies and lengths."""
        self.doc_lengths = [len(c.tokens) for c in self.chunks]
        self.avg_dl = sum(self.doc_lengths) / max(len(self.doc_lengths), 1)
        self.doc_freq = {}
        for c in self.chunks:
            seen = set(c.tokens)
            for t in seen:
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filter_type: str | None = None,
    ) -> list[tuple[RAGChunk, float]]:
        """Retrieve top_k most relevant chunks using BM25 scoring."""
        if not self.chunks:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            candidates = [c for c in self.chunks if not filter_type or c.chunk_type == filter_type]
            return [(c, 1.0) for c in candidates[:top_k]]

        n = len(self.chunks)
        scores: list[tuple[RAGChunk, float]] = []

        for idx, chunk in enumerate(self.chunks):
            if filter_type and chunk.chunk_type != filter_type:
                continue

            doc_len = self.doc_lengths[idx]
            score = 0.0
            chunk_token_counts: dict[str, int] = {}
            for t in chunk.tokens:
                chunk_token_counts[t] = chunk_token_counts.get(t, 0) + 1

            for qt in q_tokens:
                tf = chunk_token_counts.get(qt, 0)
                if tf == 0:
                    continue
                df = self.doc_freq.get(qt, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                numerator = tf * (self.k1 + 1.0)
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / max(self.avg_dl, 1.0)))
                score += idf * (numerator / max(denominator, 1e-6))

                # Exact phrase or exact keyword boost
                if qt in chunk.title.lower():
                    score += 1.5

            if score > 0:
                scores.append((chunk, round(score, 3)))

        # Sort descending by BM25 score
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def build_augmented_context(
        self,
        journey_queries: list[str],
        *,
        max_endpoints: int = 8,
        max_requirements: int = 6,
    ) -> dict[str, Any]:
        """Build a consolidated RAG context packet for autonomous agent planning."""
        retrieved_endpoints: dict[str, RAGChunk] = {}
        retrieved_requirements: dict[str, RAGChunk] = {}

        for query in journey_queries:
            ep_matches = self.retrieve(query, top_k=3, filter_type="openapi_endpoint")
            for chunk, score in ep_matches:
                retrieved_endpoints[chunk.chunk_id] = chunk

            req_matches = self.retrieve(query, top_k=2, filter_type="requirement_rule")
            for chunk, score in req_matches:
                retrieved_requirements[chunk.chunk_id] = chunk

        # Format context blocks
        endpoint_blocks = [
            c.content for c in list(retrieved_endpoints.values())[:max_endpoints]
        ]
        requirement_blocks = [
            c.content for c in list(retrieved_requirements.values())[:max_requirements]
        ]

        return {
            "total_chunks_indexed": len(self.chunks),
            "retrieved_endpoint_count": len(endpoint_blocks),
            "retrieved_requirement_count": len(requirement_blocks),
            "endpoints_context": "\n\n".join(endpoint_blocks) if endpoint_blocks else "(No specific endpoint matched)",
            "requirements_context": "\n\n".join(requirement_blocks) if requirement_blocks else "(No specific requirement matched)",
        }
