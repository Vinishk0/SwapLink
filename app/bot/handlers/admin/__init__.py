"""Admin panel routers."""

from aiogram import Router

from app.bot.handlers.admin import menu, orders, rates, users


def get_admin_router() -> Router:
    router = Router(name="admin")
    router.include_routers(menu.router, orders.router, users.router, rates.router)
    return router


__all__ = ["get_admin_router"]
