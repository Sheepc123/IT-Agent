"""Request bodies for the business A request endpoints."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class MinimumSpecs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_gib: int = Field(gt=0)
    storage_bytes: int = Field(gt=0)


class RequestDraft(BaseModel):
    """A draft may omit business fields; submit checks them. Unknown is null, never 0."""

    model_config = ConfigDict(extra="forbid")  # identity and status cannot be sent by the client

    business_type: str = Field(default="A", pattern="^A$")
    purpose: str | None = None
    category: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    template_key: str | None = None
    minimum_specs: MinimumSpecs | None = None
    budget_minor: int | None = Field(default=None, ge=0)
    required_by: AwareDatetime | None = None
    destination: str | None = None


REQUIRED_FIELDS = ("purpose", "category", "quantity", "minimum_specs", "budget_minor", "required_by", "destination")


def missing_fields(draft: RequestDraft) -> list[str]:
    return [name for name in REQUIRED_FIELDS if getattr(draft, name) in (None, "")]


def iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
