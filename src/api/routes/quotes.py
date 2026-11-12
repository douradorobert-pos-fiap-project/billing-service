from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.dependencies import SessionDep
from src.application.approve_quote import decide_quote
from src.application.generate_quote import generate_quote
from src.domain.quote import ConflictingQuoteDecision, Quote
from src.infrastructure.database.repositories import (
    get_catalog_item,
    get_quote,
    persist_decision,
    persist_quote,
)

router = APIRouter(prefix="/api/v1/quotes", tags=["quotes"])


class QuoteSelection(BaseModel):
    catalog_item_id: UUID
    quantity: int = Field(gt=0)


class QuoteCreate(BaseModel):
    order_id: UUID
    saga_id: UUID
    items: list[QuoteSelection] = Field(min_length=1)


class QuoteDecision(BaseModel):
    action: str


class QuoteLineResponse(BaseModel):
    catalog_item_id: UUID
    name: str
    item_type: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal


class QuoteResponse(BaseModel):
    id: UUID
    order_id: UUID
    saga_id: UUID
    status: str
    total: Decimal
    lines: list[QuoteLineResponse]


def to_response(quote: Quote) -> QuoteResponse:
    return QuoteResponse(
        id=quote.id,
        order_id=quote.order_id,
        saga_id=quote.saga_id,
        status=quote.status.value,
        total=quote.total,
        lines=[
            QuoteLineResponse(
                catalog_item_id=line.catalog_item_id,
                name=line.name,
                item_type=line.item_type.value,
                unit_price=line.unit_price,
                quantity=line.quantity,
                subtotal=line.subtotal,
            )
            for line in quote.lines
        ],
    )


@router.post("", response_model=QuoteResponse, status_code=status.HTTP_201_CREATED)
def create_quote(
    data: QuoteCreate, request: Request, session: SessionDep
) -> QuoteResponse:
    with session.begin():
        selections = []
        for selected in data.items:
            item = get_catalog_item(session, selected.catalog_item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="catalog item not found")
            selections.append((item, selected.quantity))
        quote = generate_quote(
            data.order_id, data.saga_id, request.state.correlation_id, selections
        )
        persist_quote(session, quote)
    return to_response(quote)


@router.get("/{quote_id}", response_model=QuoteResponse)
def detail_quote(quote_id: UUID, session: SessionDep) -> QuoteResponse:
    quote = get_quote(session, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="quote not found")
    return to_response(quote)


@router.post("/{quote_id}/approval", response_model=QuoteResponse)
def decide(quote_id: UUID, data: QuoteDecision, session: SessionDep) -> QuoteResponse:
    try:
        with session.begin():
            quote = get_quote(session, quote_id)
            if quote is None:
                raise HTTPException(status_code=404, detail="quote not found")
            changed = decide_quote(quote, data.action)
            persist_decision(session, quote, changed)
    except ConflictingQuoteDecision as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return to_response(quote)
