import pytest
from pydantic import ValidationError

from crashproof.models import Line, Order
from crashproof.world.store import OrderNotFound, Store


def _order(order_id: str = "ord-1") -> Order:
    return Order(
        order_id=order_id,
        customer_id="cus-1",
        lines=[Line(sku="s1", qty=2, unit_price=500)],
        paid_amount=1000,
        delivered_at=0,
        carrier="A",
        fraud_risk=False,
    )


def test_get_existing_order() -> None:
    order = _order()
    assert Store([order, _order("ord-2")]).get_order("ord-1") == order


def test_unknown_order_raises() -> None:
    with pytest.raises(OrderNotFound):
        Store([_order()]).get_order("nope")


def test_store_has_no_mutation_api() -> None:
    public = [n for n in dir(Store) if not n.startswith("_")]
    assert public == ["get_order"]


def test_returned_order_is_frozen() -> None:
    order = Store([_order()]).get_order("ord-1")
    with pytest.raises(ValidationError):
        order.paid_amount = 1  # type: ignore[misc]
