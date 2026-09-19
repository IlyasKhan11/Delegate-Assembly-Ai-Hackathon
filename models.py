from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class IncomingUserRequest(BaseModel):
    raw_prompt: str
    user_language: Optional[str] = "English"
    mode: Optional[str] = Field(
        default="delegate",
        description="'delegate' (AI negotiates autonomously within constraints) or 'assist' (pauses for user approval on each commitment)"
    )

class RuleConstraint(BaseModel):
    parameter: str = Field(
        description="What is being limited, e.g., 'price', 'delivery_time', 'appointment_date'"
    )
    operator: str = Field(
        description="'max' for ceilings, 'min' for floors, or 'exact'"
    )
    value: str = Field(
        description="The limit value, e.g., '20.0', '45', 'Tuesday 3 PM'"
    )
    unit: Optional[str] = Field(
        default=None, description="e.g., 'USD', 'PKR', 'minutes'"
    )

class ExtractedRules(BaseModel):
    intent: str = Field(
        description="The primary action, e.g., 'book_hotel', 'order_pizza', 'schedule_appointment'"
    )
    target_business: str = Field(
        description="The entity being called, e.g., 'Hotel front desk', 'Pizzeria', 'Dental clinic'"
    )
    action_details: Optional[str] = Field(
        default=None,
        description="Specific items, requests, or subject of the action (e.g., 'Dentist appointment for Tuesday before 4 PM')"
    )
    opening_phrase: Optional[str] = Field(
        default=None,
        description="A natural, professional opening sentence CallBridge speaks when the receptionist answers."
    )
    constraints: List[RuleConstraint] = Field(
        default_factory=list,
        description="All limits (budgets, time limits, deadlines)",
    )
    forbidden_disclosures: List[str] = Field(
        default_factory=list,
        description="Info the bot is strictly forbidden from sharing (e.g. credit card, address, SSN)",
    )
    special_notes: Optional[str] = None


class TurnDecision(BaseModel):
    is_dealbreaker: bool = Field(
        description="True if the caller quotes a price above spend ceiling, violates time rules, or asks for unauthorized commitment/data."
    )
    violation_parameter: Optional[str] = Field(
        default=None,
        description="Name of violated rule (e.g. 'price', 'delivery_time'), or null if safe."
    )
    violation_reason: Optional[str] = Field(
        default=None,
        description="Human-readable reason for the red card (e.g. 'Price exceeds maximum allowed of $30.')."
    )
    immediate_stalling_phrase: Optional[str] = Field(
        default=None,
        description="Short, polite phrase the AI immediately speaks to caller while paused (e.g. 'Hold on a moment while I confirm that.')."
    )
    suggested_user_options: Optional[List[str]] = Field(
        default_factory=list,
        description="2 to 4 choices for the UI buttons (e.g. ['Ask for a discount', 'Offer a smaller order'])."
    )
    draft_response: Optional[str] = Field(
        default="",
        description="If safe: autonomous next reply to say to caller. If dealbreaker: initial drafted counter-action."
    )
    call_completed: bool = Field(
        default=False,
        description="True if the goal has been confirmed/finalized or the receptionist said goodbye and the call should conclude."
    )

    @field_validator("suggested_user_options", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

class UserDecisionAction(BaseModel):
    chosen_action: str = Field(
        description="The action selected by the user, e.g., 'Negotiate down to $30', 'Accept', 'Decline'"
    )
    custom_instruction: Optional[str] = Field(
        default=None,
        description="Optional text if the user chose 'Type my own response'"
    )

class StagedResponse(BaseModel):
    english_response: str = Field(
        description="The exact phrase the agent will speak to the caller once approved."
    )
    translated_response: str = Field(
        description="The same phrase translated into the user's language so they can verify it."
    )
    action_summary: str = Field(
        description="Short summary for the decision ledger, e.g., 'Counter-offer drafted at $30'."
    )

class FinalSummary(BaseModel):
    outcome_headline: str = Field(
        description="Bold headline summary, e.g. 'Early check-in at 12:00 held for $20.'"
    )
    outcome_subtext: str = Field(
        description="One-sentence description of final payment or logistics status."
    )
    confirmed_items: List[str] = Field(
        default_factory=list,
        description="List of solid facts agreed upon during the conversation."
    )
    unresolved_items: List[str] = Field(
        default_factory=list,
        description="Ambiguities or things the caller could not confirm."
    )

class IncomingCallerTurn(BaseModel):
    caller_text: str
