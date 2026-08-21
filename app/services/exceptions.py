"""Domain errors. Handlers translate them into user-facing messages."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected, user-fixable failures."""


class ReferralError(ServiceError):
    """Referral code could not be applied."""


class OrderError(ServiceError):
    """Order could not be created or transitioned."""


class BalanceError(ServiceError):
    """Balance operation is not possible (e.g. insufficient funds)."""


class RateError(ServiceError):
    """Rates/pairs are misconfigured."""
