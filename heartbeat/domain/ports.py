from typing import Protocol


class DeliveryPort(Protocol):
    async def deliver(self, message: str) -> None: ...
