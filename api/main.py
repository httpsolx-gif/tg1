import uvicorn
from api.app import build_app

app = build_app()

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)
