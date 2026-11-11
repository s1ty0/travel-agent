from concurrent.futures import ThreadPoolExecutor

import pytest

from travel_agent.deepseek import ProviderError
from travel_agent.service_budget import ServiceBudget


def test_five_yuan_default_and_reject_above_user_limit():
    assert ServiceBudget().summary()["cap"] == 5_000_000
    with pytest.raises(ProviderError, match="invalid_service_budget"):
        ServiceBudget(501)


def test_parallel_reservations_cannot_spend_the_same_balance_twice():
    ledger = ServiceBudget(100)

    def reserve(index):
        try:
            ledger.reserve(str(index), 600_000)
            return True
        except ProviderError as exc:
            assert str(exc) == "service_budget_exhausted"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(results) == 1
    assert ledger.summary()["reserved_unsettled"] == 600_000
