"""
ORM models. These replace the raw CREATE TABLE statements from the SQLite
version. SQLAlchemy generates the schema from these classes via init_db().
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String
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

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "due": self.due.isoformat(),
            "created": self.created.isoformat(),
        }


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    start: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "start": self.start.isoformat(),
        }
        