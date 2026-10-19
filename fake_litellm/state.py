from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass
class FakeState:
    engine: AsyncEngine
    master_key: str
    outage: bool = False
