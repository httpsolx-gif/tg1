from pydantic import BaseModel
from typing import Optional

class ErrorLogOut(BaseModel):
    ts: str
    level: str
    event: str
    error: Optional[str] = None
    event_id: Optional[str] = None
