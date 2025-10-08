from pydantic import BaseModel

class SendOut(BaseModel):
    id: int
    email: str
    status: str
    created_at: str
