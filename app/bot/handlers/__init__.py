"""Router registry. Order matters: `common` must stay last (it catches the rest)."""

from aiogram import Router

from app.bot.handlers import common, exchange, orders, profile, referral, start
from app.bot.handlers.admin import get_admin_router


def get_root_router() -> Router:
    router = Router(name="root")
    router.include_routers(
        start.router,
        get_admin_router(),
        exchange.router,
        profile.router,
        referral.router,
        orders.router,
        common.router,
    )
    return router


__all__ = ["get_root_router"]
