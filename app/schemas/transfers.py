from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money

Kind = Literal["employee", "initiative", "group", "system"]


class EndpointIn(BaseModel):
    kind: Kind
    id: str = Field(min_length=1, max_length=320)


class TransferIn(BaseModel):
    group_id: int  # the section the actor acts in; both ends must belong to it
    source: EndpointIn
    target: EndpointIn
    amount: Money = Field(gt=0)
