from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel

from app.ai.providers import Provider, get_provider
from app.api.dependencies import DB, Analyst, CurrentUser
from app.schemas import DocumentOut, RagOut, RetrievalOut, RetrievalRequest
from app.services import documents, retrieval

router = APIRouter(tags=["knowledge"])
AI = Annotated[Provider, Depends(get_provider)]


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int


@router.post("/documents", response_model=DocumentOut, status_code=201)
def upload(db: DB, user: Analyst, provider: AI, file: Annotated[UploadFile, File()]):
    return documents.ingest(db, user, file.file.read(10 * 1024 * 1024 + 1), file.filename or "", provider)


@router.get("/documents", response_model=DocumentList)
def list_documents(
    db: DB, user: CurrentUser, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return documents.list_documents(db, user.organization_id, limit, offset)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: UUID, db: DB, user: CurrentUser):
    return documents.get_document(db, user.organization_id, document_id)


@router.post("/retrieval/search", response_model=RetrievalOut)
def search(request: RetrievalRequest, db: DB, user: CurrentUser, provider: AI):
    return retrieval.retrieve(db, user.organization_id, request, provider)


@router.post("/rag/query", response_model=RagOut)
def query(request: RetrievalRequest, db: DB, user: CurrentUser, provider: AI):
    return retrieval.rag(db, user.organization_id, request, provider)
