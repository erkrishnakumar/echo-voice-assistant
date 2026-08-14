"""Memory model — a short personal fact about the user, remembered for recall
in later conversations (e.g. "my girlfriend's name is Aditi")."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from echo.db import Base


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fact: Mapped[str] = mapped_column(String(500), nullable=False)
    created: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "fact": self.fact,
            "created": self.created.isoformat(),
        }
