from fastapi import APIRouter, Response
router = APIRouter()

@router.get("/r/{token}")
def redirect(token: str):
    # TODO: write Click row, 302 to original
    return Response(status_code=302, headers={"Location": "https://example.com"})
