"""Per-run CNY reservations, independent of the travel budget and provider billing."""

from dataclasses import dataclass
from datetime import date
from threading import RLock

from .deepseek import ProviderError


@dataclass(frozen=True)
class PriceCard:
    # Initial CNY peak/miss prices were rechecked on 2026-11-11; no cache discounts.
    checked_on: date = date(2026, 11, 8)
    valid_until: date = date(2026, 12, 11)
    model: str = "deepseek-flash"
    input_micro_cny_per_token: int = 2
    output_micro_cny_per_token: int = 8
    search_micro_cny_per_call: int = 5000
    fetch_micro_cny_per_call: int = 5000
    amap_free_quota_only: bool = False

    def validate(self, model: str, today: date):
        if model != self.model:
            raise ProviderError("model_price_unknown")
        if not self.checked_on <= today <= self.valid_until:
            raise ProviderError("price_card_expired")
        if any(
            type(rate) is not int or rate < 0
            for rate in (
                self.input_micro_cny_per_token,
                self.output_micro_cny_per_token,
                self.search_micro_cny_per_call,
                self.fetch_micro_cny_per_call,
            )
        ):
            raise ProviderError("invalid_price_card")

    def validate_fetch(self, today: date):
        # Fetch does not use the configured chat model or any token pricing.
        if not self.checked_on <= today <= self.valid_until:
            raise ProviderError("price_card_expired")
        if type(self.fetch_micro_cny_per_call) is not int or self.fetch_micro_cny_per_call < 0:
            raise ProviderError("invalid_price_card")


class ServiceBudget:
    """Shared run ledger. Atomic reservations include all concurrent agent branches."""

    def __init__(self, cap_minor: int = 500):
        if type(cap_minor) is not int or not 1 <= cap_minor <= 500:
            raise ProviderError("invalid_service_budget")
        self.cap_micro = cap_minor * 10000
        self.estimated_micro = 0
        self.reservations = {}
        self.uncertain = False
        self.lock = RLock()

    def reserve(self, event_id: str, micro_cny: int):
        with self.lock:
            self._reserve(event_id, micro_cny)

    def _reserve(self, event_id: str, micro_cny: int):
        if self.uncertain:
            raise ProviderError("service_cost_unknown")
        if type(micro_cny) is not int or micro_cny < 0 or event_id in self.reservations:
            raise ProviderError("invalid_reservation")
        if self.estimated_micro + sum(self.reservations.values()) + micro_cny > self.cap_micro:
            raise ProviderError("service_budget_exhausted")
        self.reservations[event_id] = micro_cny

    def settle(self, event_id: str, upper_estimate_micro: int):
        with self.lock:
            self._settle(event_id, upper_estimate_micro)

    def _settle(self, event_id: str, upper_estimate_micro: int):
        reserved = self.reservations[event_id]
        if type(upper_estimate_micro) is not int or not 0 <= upper_estimate_micro <= reserved:
            self.uncertain = True
            raise ProviderError("service_reservation_exceeded")
        self.estimated_micro += upper_estimate_micro
        del self.reservations[event_id]

    def unknown(self):
        with self.lock:
            self.uncertain = True

    def summary(self):
        with self.lock:
            return self._summary()

    def _summary(self):
        return {
            "currency": "CNY",
            "unit": "micro_cny",
            "cap": self.cap_micro,
            "estimated_upper": self.estimated_micro,
            "reserved_unsettled": sum(self.reservations.values()),
            "status": "uncertain" if self.uncertain else "estimated",
            "actual_billed": None,
        }
