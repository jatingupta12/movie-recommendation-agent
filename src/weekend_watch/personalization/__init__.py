"""Single-user preferences and title activity data layer."""

from .models import UserPreferences
from .service import PersonalizationService

__all__ = ["PersonalizationService", "UserPreferences"]
