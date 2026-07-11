from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.auth import router as auth_router

app = FastAPI(title="TRACE")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session auth uses itsdangerous-signed cookies (handled inside app.auth —
# no SessionMiddleware). The auth router owns login/logout; every other
# route (Task 7) is gated by the role dependencies in app.auth.
app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}
