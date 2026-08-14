"""
Models package. One file per model, all re-exported here so the rest of the
codebase (and Alembic) can do `from echo.models import Reminder, Event`.

IMPORTANT: every model must be imported here. Alembic's autogenerate and
Base.metadata rely on models being registered, which happens when they're
imported. Add new models to this list.
"""

from echo.models.event import Event
from echo.models.memory import Memory
from echo.models.reminder import Reminder

__all__ = ["Reminder", "Event", "Memory"]