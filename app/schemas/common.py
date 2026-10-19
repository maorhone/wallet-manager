from decimal import Decimal
from typing import Annotated

from pydantic import Field, PlainSerializer

Money = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
]
