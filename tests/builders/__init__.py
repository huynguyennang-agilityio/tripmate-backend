"""The base of test data builders.

A builder makes one model with valid defaults, so a test names only the values it
is about. A module's builder lives in `tests/builders/<module>.py`:

    class TripBuilder(Builder[Trip]):
        model = Trip

        def defaults(self) -> dict[str, Any]:
            return {"name": unique("trip")}

    trip = TripBuilder(name="Hue").build()                 # not stored
    trip = await TripBuilder().but(name="Hue").create(session)  # added and flushed
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, Self

from sqlalchemy.ext.asyncio import AsyncSession


def unique(prefix: str) -> str:
    """A value no other test uses, for columns that must be unique."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Builder[ModelT]:
    """Defaults from `defaults()`, overridden by keyword values."""

    model: ClassVar[type[Any]]

    def __init__(self, **values: Any) -> None:
        self._values = {**self.defaults(), **values}

    def defaults(self) -> dict[str, Any]:
        """The model's values when the test gives none; called once per builder."""
        return {}

    def but(self, **values: Any) -> Self:
        """A copy of this builder with some values changed."""
        return type(self)(**{**self._values, **values})

    def build(self) -> ModelT:
        """The model, not stored."""
        instance: ModelT = self.model(**self._values)
        return instance

    async def create(self, session: AsyncSession) -> ModelT:
        """The model, added to `session` and flushed (the test's transaction stores it)."""
        instance = self.build()
        session.add(instance)
        await session.flush()
        return instance
