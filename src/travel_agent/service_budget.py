"""Per-run CNY reservations, independent of the travel budget and provider billing."""

from dataclasses import dataclass
from datetime import date

from .deepseek import ProviderError


@dataclass(frozen=True)
class PriceCard:
    # Official CNY peak/miss prices checked on this date; never assume cache discounts.
    checked_on: date = date(2026, 11, 8)
    valid_until: date = date(2026, 11, 15)
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
    """Single-run, sequential ledger. Unknown charges retain reservation and stop continuation."""

    def __init__(self, cap_minor: int = 200):
        if type(cap_minor) is not int or not 1 <= cap_minor <= 200:
            raise ProviderError("invalid_service_budget")
        self.cap_micro = cap_minor * 10000
        self.estimated_micro = 0
        self.reservations = {}
        self.uncertain = False

    def reserve(self, event_id: str, micro_cny: int):
        if self.uncertain:
            raise ProviderError("service_cost_unknown")
        if type(micro_cny) is not int or micro_cny < 0 or event_id in self.reservations:
            raise ProviderError("invalid_reservation")
        if self.estimated_micro + sum(self.reservations.values()) + micro_cny > self.cap_micro:
            raise ProviderError("service_budget_exhausted")
        self.reservations[event_id] = micro_cny

    def settle(self, event_id: str, upper_estimate_micro: int):
        reserved = self.reservations[event_id]
        if type(upper_estimate_micro) is not int or not 0 <= upper_estimate_micro <= reserved:
            self.uncertain = True
            raise ProviderError("service_reservation_exceeded")
        self.estimated_micro += upper_estimate_micro
        del self.reservations[event_id]

    def unknown(self):
        self.uncertain = True

    def summary(self):
        return {
            "currency": "CNY",
            "unit": "micro_cny",
            "cap": self.cap_micro,
            "estimated_upper": self.estimated_micro,
            "reserved_unsettled": sum(self.reservations.values()),
            "status": "uncertain" if self.uncertain else "estimated",
            "actual_billed": None,
        }
