"""Validated API inputs and responses."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Category(str, Enum):
    ROADWORKS = "roadworks"
    POTHOLE = "pothole"
    DAMAGED_SURFACE = "damaged_surface"
    OBSTRUCTION = "obstruction"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(str, Enum):
    REPORTED = "reported"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class APIModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )


class ReportCreate(APIModel):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    location: str = Field(min_length=3, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: Category
    severity: Severity = Severity.MEDIUM


class Report(ReportCreate):
    id: int
    status: Status
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class StatusUpdate(APIModel):
    expected_status: Status
    status: Status
    note: str = Field(min_length=3, max_length=1000)


class StatusEvent(APIModel):
    id: int
    report_id: int
    from_status: Status | None
    to_status: Status
    note: str
    created_at: datetime


class NearbyReport(APIModel):
    report: Report
    distance_km: float


class Summary(APIModel):
    total_reports: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    high_severity_unresolved: int
