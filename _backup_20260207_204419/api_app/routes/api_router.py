from fastapi import APIRouter

from .players import router as players_router
from .jobs import router as jobs_router

router = APIRouter(prefix="/api/v1", tags=["v1"])

router.include_router(players_router)
router.include_router(jobs_router)
