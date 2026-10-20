from pydantic import BaseModel


class MoveIn(BaseModel):
    to_group_id: int


class DeleteIn(BaseModel):
    confirm_upn: str
