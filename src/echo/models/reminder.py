"""Reminder model — a thing to remind the user about at a specific time."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from echo.db import Base


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    due: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    created: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(), nullable=False
    )
    # set once the scheduler has alerted the user, so it fires exactly once
    fired: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "due": self.due.isoformat(),
            "created": self.created.isoformat(),
            "fired": self.fired,
        }