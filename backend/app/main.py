import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.batches import router as batches_router
from app.routers.capture import router as capture_router
from app.routers.contracts import router as contracts_router
from app.routers.offers import router as offers_router
from app.routers.payouts import router as payouts_router
from app.routers.pickups import router as pickups_router
from app.routers.telegram import router as telegram_router
from app.services import scheduler

app = FastAPI(title="TRACE")

# CORS: the frontend (Vercel) and backend (Render) are on different origins, and
# auth uses an httpOnly session cookie. Browsers refuse credentialed
# cross-origin requests when allow_origins is ["*"], so we list the allowed
# frontend origins explicitly (comma-separated in CORS_ORIGINS) and enable
# allow_credentials. Render/local/dev origins are all supported.
_allowed_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        # sensible defaults: local frontend dev + the localhost backend
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _start_scheduler() -> None:
    """Start the handoff scheduler (the demo 'spoilage clock')."""
    scheduler.start()

# Session auth uses itsdangerous-signed cookies (handled inside app.auth —
# no SessionMiddleware). The auth router owns login/logout; every other
# route (Task 7) is gated by the role dependencies in app.auth.
app.include_router(auth_router)

# Task 7 REST surface. Each router owns its resource group; the role
# dependencies (require_admin / require_buyer(role) / require_composter)
# gate every route so 401/403 is real even where the handler is a 501 stub.
# /admin/stream and /contracts/mine are implemented for real.
app.include_router(batches_router)
app.include_router(contracts_router)
app.include_router(payouts_router)
app.include_router(offers_router)
app.include_router(pickups_router)
app.include_router(telegram_router)
app.include_router(capture_router)
app.include_router(admin_router)


@app.get("/health")
def health():
    return {"status": "ok"}
