"""Shared review eligibility rule for the future P07 review command and task source."""

import uuid

from .auth.identity import Actor


def may_review_plan(
    actor: Actor, *, scope_id: uuid.UUID, business_type: str, plan_status: str,
    plan_author_id: uuid.UUID, requester_id: uuid.UUID, assigned_reviewer_id: uuid.UUID | None,
    review_kind: str,
) -> bool:
    """Require current grant, explicit assignment, correct state and separation of duties."""
    if assigned_reviewer_id != actor.id or plan_author_id == actor.id:
        return False
    if review_kind == "TECHNICAL":
        return (business_type == "B" and plan_status == "TECH_REVIEW"
                and actor.has("TECH_REVIEWER", scope_id))
    if review_kind == "PROCUREMENT":
        return (plan_status == "PROCUREMENT_REVIEW" and requester_id != actor.id
                and actor.has("PROCUREMENT_APPROVER", scope_id))
    return False
