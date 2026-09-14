"""Business rules shared by the ops data and the documents that describe them.

Keeping them in one module means the database, the corpus and the golden evaluation set can
never disagree about, say, a tier's late delivery credit.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TierTerms:
    first_response_hours: int
    late_credit_pct: int
    payment_terms_days: int
    price_multiplier: Decimal


TIERS: dict[str, TierTerms] = {
    "gold": TierTerms(4, 10, 45, Decimal("0.92")),
    "silver": TierTerms(8, 5, 30, Decimal("0.96")),
    "bronze": TierTerms(24, 0, 30, Decimal("1.00")),
}
TIER_MIN_ANNUAL_REVENUE_USD = {"gold": 2_000_000, "silver": 500_000, "bronze": 0}

# band -> (salary min, salary max, bonus target %)
PAY_BANDS: dict[str, tuple[int, int, int]] = {
    "L1": (48_000, 62_000, 5),
    "L2": (60_000, 82_000, 5),
    "L3": (78_000, 105_000, 8),
    "L4": (100_000, 135_000, 10),
    "L5": (130_000, 180_000, 15),
    "L6": (175_000, 240_000, 20),
    "L7": (230_000, 320_000, 30),
}
MERIT_BUDGET_PCT = "3.5"

DOMESTIC_PER_DIEM_USD = 75
INTERNATIONAL_PER_DIEM_USD = 120
HOTEL_CAP_USD = 250
HIGH_COST_HOTEL_CAP_USD = 350
MILEAGE_RATE_USD = "0.70"
EXPENSE_SUBMISSION_DAYS = 30
EXPENSE_HARD_LIMIT_DAYS = 90
RECEIPT_REQUIRED_USD = 25

# The superseded 2023 wiki page still says these; the conflict is deliberate.
LEGACY_EXPENSE_SUBMISSION_DAYS = 60
LEGACY_DOMESTIC_PER_DIEM_USD = 60
LEGACY_HOTEL_CAP_USD = 200

CONCEALED_DAMAGE_REPORT_DAYS = 9
CLAIM_LIABILITY_PER_KG_USD = "2.50"
CLAIM_FINANCE_REVIEW_USD = 5_000
CREDIT_HOLD_DAYS_OVERDUE = 45
DISPUTE_WINDOW_BUSINESS_DAYS = 10
SECURITY_INCIDENT_REPORT_HOURS = 1
