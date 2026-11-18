from time import perf_counter
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.ai.providers import Answer, Provider
from app.config import get_settings
from app.errors import AppError
from app.models import Document, DocumentChunk
from app.observability.tracing import span
from app.schemas import Citation, RagOut, RetrievalOut, RetrievalRequest


def reciprocal_rank_fusion(rankings: list[list[UUID]]) -> dict[UUID, float]:
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(dict.fromkeys(ranking), 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
    return scores


@span("rag.retrieve")
def retrieve(
    db: Session, organization_id: UUID, request: RetrievalRequest, provider: Provider
) -> RetrievalOut:
    started = perf_counter()
    conditions = [
        DocumentChunk.organization_id == organization_id,
        Document.organization_id == organization_id,
        Document.ingestion_status == "ready",
        DocumentChunk.embedding_model == provider.embedding_model,
        Document.embedding_model == provider.embedding_model,
    ]
    if request.document_ids is not None:
        conditions.append(Document.id.in_(request.document_ids))
    if request.title is not None:
        conditions.append(Document.title == request.title)
    if request.content_type is not None:
        conditions.append(Document.content_type == request.content_type)
    base = (
        select(DocumentChunk, Document)
        .options(load_only(DocumentChunk.id, DocumentChunk.content, DocumentChunk.page, DocumentChunk.char_start),
                 load_only(Document.id, Document.title))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(*conditions)
    )
    top_k = request.top_k or get_settings().retrieval_top_k
    candidate_count = max(10, top_k)
    rankings = []
    candidates = {}
    input_tokens = None
    if request.mode in {"semantic", "hybrid"}:
        embedding = provider.embed([request.query], query=True)
        input_tokens = embedding.input_tokens
        distance = DocumentChunk.embedding.cosine_distance(embedding.vectors[0])
        # Exclude non-positive similarity; lexical matching can still contribute in hybrid mode.
        rows = db.execute(
            base.where(distance < 0.95).order_by(distance, DocumentChunk.id).limit(candidate_count)
        ).all()
        rankings.append([chunk.id for chunk, _ in rows])
        candidates.update({chunk.id: (chunk, doc) for chunk, doc in rows})
    if request.mode in {"keyword", "hybrid"}:
        query = func.websearch_to_tsquery("english", request.query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, query)
        rows = db.execute(
            base.where(DocumentChunk.search_vector.op("@@")(query))
            .order_by(rank.desc(), DocumentChunk.id)
            .limit(candidate_count)
        ).all()
        rankings.append([chunk.id for chunk, _ in rows])
        candidates.update({chunk.id: (chunk, doc) for chunk, doc in rows})
    scores = reciprocal_rank_fusion(rankings)
    ids = sorted(scores, key=lambda item: (-scores[item], str(item)))[:top_k]
    sources = []
    for chunk_id in ids:
        chunk, doc = candidates[chunk_id]
        excerpt = chunk.content[:1600]
        sources.append(
            Citation(
                chunk_id=chunk.id,
                document_id=doc.id,
                title=doc.title,
                page=chunk.page,
                char_start=chunk.char_start,
                char_end=chunk.char_start + len(excerpt),
                excerpt=excerpt,
                score=scores[chunk_id],
            )
        )
    return RetrievalOut(
        sources=sources,
        provider=get_settings().ai_provider,
        embedding_model=provider.embedding_model,
        retrieval_latency_ms=(perf_counter() - started) * 1000,
        input_tokens=input_tokens,
    )


def validate_citations(db: Session, organization_id: UUID, sources: list[Citation], answer: Answer) -> None:
    allowed = {str(source.chunk_id): source for source in sources}
    cited = {chunk_id for claim in answer.claims for chunk_id in claim.chunk_ids}
    if not cited.issubset(allowed):
        raise AppError(502, "invalid_citation", "Answer cited unavailable evidence")
    if not cited:
        return
    rows = db.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.id.in_([UUID(item) for item in cited]),
            DocumentChunk.organization_id == organization_id,
            Document.organization_id == organization_id,
            Document.ingestion_status == "ready",
        )
    ).all()
    if len(rows) != len(cited):
        raise AppError(502, "invalid_citation", "Answer cited unavailable evidence")
    for chunk, doc in rows:
        source = allowed[str(chunk.id)]
        if source.document_id != doc.id or not chunk.content.startswith(source.excerpt):
            raise AppError(502, "invalid_citation", "Evidence changed during retrieval")


def rag(db: Session, organization_id: UUID, request: RetrievalRequest, provider: Provider) -> RagOut:
    result = retrieve(db, organization_id, request, provider)
    started = perf_counter()
    answer = provider.answer(request.query, result.sources) if result.sources else Answer([])
    validate_citations(db, organization_id, result.sources, answer)
    used_ids = {chunk_id for claim in answer.claims for chunk_id in claim.chunk_ids}
    text = "\n\n".join(
        claim.text + " " + " ".join(f"[{cid}]" for cid in claim.chunk_ids) for claim in answer.claims
    )
    if not text:
        text = "Insufficient knowledge: no supporting evidence was found."
    elif get_settings().ai_provider == "mock":
        text = "Deterministic mock — relevant source excerpts:\n\n" + text
    payload = result.model_dump()
    payload["sources"] = [source for source in result.sources if str(source.chunk_id) in used_ids]
    return RagOut(
        **payload,
        answer=text,
        generation_model=provider.generation_model,
        generation_latency_ms=(perf_counter() - started) * 1000,
        generation_input_tokens=answer.input_tokens,
        generation_output_tokens=answer.output_tokens,
    )
