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

# CORS: the frontend (Vercel) and backend (Render) are on different origins,
# and auth uses an httpOnly session cookie. Browsers refuse credentialed
# cross-origin requests when allow_origins is ["*"], so we reflect the request
# Origin for anything matching CORS_ORIGINS (or a permissive default regex
# covering Vercel, Render previews, and localhost) and enable credentials.
_cors_env = os.environ.get("CORS_ORIGINS", "").strip()
if _cors_env:
    # Explicit allowlist (comma-separated). Use exact origins for safety.
    _allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Default: permissive but credentials-safe. Match localhost, Vercel
    # (*.vercel.app), and Render previews (*.onrender.com) so the demo works
    # on first deploy without fiddly CORS config. Tighten by setting
    # CORS_ORIGINS to your exact frontend URL in production.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(?:localhost|127\.0\.0\.1)(?::\d+)?|https://[a-z0-9-]+\.vercel\.app|https://[a-z0-9-]+\.onrender\.com)$",
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
