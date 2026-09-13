"""Tools 6-7: check_billing_status / apply_billing_credit -- mocked Billing
Service, owned by the billing specialist agent.

apply_billing_credit is the one tool in this POC with a *conditional* risk
level (see ToolSpec.risk_override / registry.effective_risk): a credit under
AUTO_APPROVE_CREDIT_THRESHOLD_USD auto-approves, at or above it a human must
approve, mirroring create_fault_ticket's existing high-risk gating but scaled
to the actual dollar amount requested rather than being unconditional.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .registry import RiskLevel, ToolRegistry, ToolSpec

# Scenario fixture: service BB-111 (customer 111) demonstrates the "billing
# hold" path -- overdue and suspended, needing a credit to restore service.
_OVERDUE_SERVICES: dict[str, dict] = {
    "BB-111": {"overdue_amount": 189.50, "days_overdue": 42},
}

AUTO_APPROVE_CREDIT_THRESHOLD_USD = 10.0


class CheckBillingStatusInput(BaseModel):
    model_config = ConfigDict(title="check_billing_status")

    service_id: str = Field(description="The broadband service identifier")


def check_billing_status(payload: CheckBillingStatusInput) -> dict:
    overdue = _OVERDUE_SERVICES.get(payload.service_id)
    if overdue:
        return {
            "overdue_amount": overdue["overdue_amount"],
            "days_overdue": overdue["days_overdue"],
            "service_suspended": True,
            "status": "suspended_nonpayment",
        }
    return {
        "overdue_amount": 0.0,
        "days_overdue": 0,
        "service_suspended": False,
        "status": "in_good_standing",
    }


class ApplyBillingCreditInput(BaseModel):
    model_config = ConfigDict(title="apply_billing_credit")

    service_id: str = Field(description="The broadband service identifier")
    amount: float = Field(description="Credit amount in dollars", gt=0)
    reason: str = Field(description="Grounded reason for the credit, from billing status observed")


def apply_billing_credit(payload: ApplyBillingCreditInput) -> dict:
    credit_number = 1000 + (sum(ord(c) for c in payload.service_id) % 9000)
    return {"credit_id": f"CR-{credit_number}", "amount": payload.amount, "status": "applied"}


def _apply_billing_credit_risk(tool_input: dict) -> RiskLevel:
    # Fails safe: a missing/malformed amount is treated as infinite, which
    # is never < the threshold, so it always requires approval when unsure.
    amount = tool_input.get("amount", float("inf"))
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = float("inf")
    return "low" if amount < AUTO_APPROVE_CREDIT_THRESHOLD_USD else "high"


def register_billing_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="check_billing_status",
            description="Check whether a service_id has an overdue balance or suspension.",
            input_schema=CheckBillingStatusInput,
            risk="low",
            handler=check_billing_status,
        )
    )
    registry.register(
        ToolSpec(
            name="apply_billing_credit",
            description=(
                "Apply a billing credit to a service_id for a grounded reason. "
                f"Credits under ${AUTO_APPROVE_CREDIT_THRESHOLD_USD:.0f} auto-approve; "
                "larger credits require human approval."
            ),
            input_schema=ApplyBillingCreditInput,
            risk="high",  # baseline/safe default; effective_risk() may lower it
            handler=apply_billing_credit,
            risk_override=_apply_billing_credit_risk,
        )
    )
