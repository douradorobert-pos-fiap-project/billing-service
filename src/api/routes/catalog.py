from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from src.api.dependencies import SessionDep
from src.domain.catalog import CatalogItemType, money
from src.infrastructure.database.models import CatalogItemModel

router = APIRouter(prefix="/api/v1/catalog-items", tags=["catalog"])


class CatalogItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    item_type: CatalogItemType
    unit_price: Decimal = Field(ge=0, decimal_places=2)


class CatalogItemResponse(BaseModel):
    id: UUID
    name: str
    item_type: CatalogItemType
    unit_price: Decimal


@router.post(
    "", response_model=CatalogItemResponse, status_code=status.HTTP_201_CREATED
)
def create_catalog_item(
    data: CatalogItemCreate, session: SessionDep
) -> CatalogItemResponse:
    model = CatalogItemModel(
        id=str(uuid4()),
        name=data.name,
        item_type=data.item_type.value,
        unit_price=money(data.unit_price),
        active=True,
    )
    with session.begin():
        session.add(model)
    return CatalogItemResponse(
        id=UUID(model.id),
        name=model.name,
        item_type=CatalogItemType(model.item_type),
        unit_price=model.unit_price,
    )
