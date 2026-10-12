from collections.abc import Sequence

from crashproof.models import Order


class OrderNotFound(KeyError):
    """Raised when an order id is not in the store."""


class Store:
    """Read-only order store. Orders are frozen and there are no setters."""

    def __init__(self, orders: Sequence[Order]) -> None:
        self._orders = {order.order_id: order for order in orders}

    def get_order(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderNotFound(order_id) from None
