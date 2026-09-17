"""The golden evaluation set for Larkspur Logistics.

Every expected answer is derived from the same modules that produce the corpus and the ops
database (`data_gen.facts` and `data_gen.world`), so the questions cannot drift away from the
data: change a business rule and both the documents and the expectations move together.

A case carries what each metric needs:
- `expected_docs`: retrieval is correct when any of these documents is retrieved (recall@k, MRR);
- `expected_facts`: every string must appear in the normalised answer;
- `expected_route`: what the router should choose;
- `must_refuse` with `forbidden`: the caller may not see those strings anywhere, in the answer
  or in the retrieved context. A single occurrence is an ACL leak and fails the run.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.router.classifier import Route
from data_gen import facts
from data_gen.world import AS_OF, World, build_world

SEED = 7
# The reporting window the "last month" questions mean, relative to the frozen as-of date.
MONTH_START = datetime(2026, 8, 1, tzinfo=UTC)
MONTH_LABEL = "August 2026"


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    question: str
    persona: str
    expected_route: Route
    expected_docs: tuple[str, ...] = ()
    expected_facts: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    must_refuse: bool = False

    @property
    def kind(self) -> str:
        return "acl" if self.must_refuse else str(self.expected_route)


def _count(value: int) -> str:
    return str(value)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal(1)))


def _docs(
    case_id: str,
    question: str,
    persona: str,
    docs: tuple[str, ...],
    expected: tuple[str, ...],
) -> GoldenCase:
    return GoldenCase(case_id, question, persona, Route.DOCS, docs, expected)


def _policy_cases() -> list[GoldenCase]:
    travel = ("pol-travel-expense", "wiki-general-expense-reimbursement")
    return [
        _docs(
            "doc-per-diem-domestic",
            "What is the domestic per diem for meals while travelling?",
            "sales",
            travel,
            (str(facts.DOMESTIC_PER_DIEM_USD),),
        ),
        _docs(
            "doc-per-diem-international",
            "How much is the international per diem?",
            "ops",
            ("pol-travel-expense",),
            (str(facts.INTERNATIONAL_PER_DIEM_USD),),
        ),
        _docs(
            "doc-hotel-cap",
            "What is the nightly hotel cap in a standard market?",
            "support",
            travel,
            (str(facts.HOTEL_CAP_USD),),
        ),
        _docs(
            "doc-hotel-cap-high-cost",
            "What hotel rate can I book in San Francisco?",
            "finance",
            ("pol-travel-expense",),
            (str(facts.HIGH_COST_HOTEL_CAP_USD),),
        ),
        _docs(
            "doc-mileage",
            "What is the mileage rate for using my own car for work?",
            "ops",
            ("pol-travel-expense",),
            (facts.MILEAGE_RATE_USD,),
        ),
        _docs(
            "doc-expense-deadline",
            "How many days do I have to submit an expense report?",
            "engineering",
            travel,
            (str(facts.EXPENSE_SUBMISSION_DAYS),),
        ),
        _docs(
            "doc-expense-hard-limit",
            "Can I claim an expense from four months ago?",
            "finance",
            ("pol-travel-expense",),
            (str(facts.EXPENSE_HARD_LIMIT_DAYS),),
        ),
        _docs(
            "doc-receipt-threshold",
            "Above what amount do I need an itemized receipt?",
            "sales",
            ("pol-travel-expense",),
            (str(facts.RECEIPT_REQUIRED_USD),),
        ),
        _docs(
            "doc-corporate-card",
            "How quickly must a personal charge on a corporate card be repaid?",
            "finance",
            ("pol-travel-expense",),
            ("10",),
        ),
        _docs(
            "doc-pto-accrual",
            "How much paid time off does someone with four years of service get?",
            "hr",
            ("pol-pto-leave",),
            ("20",),
        ),
        _docs(
            "doc-pto-carryover",
            "How many unused PTO days can I carry into next year?",
            "support",
            ("pol-pto-leave",),
            ("5",),
        ),
        _docs(
            "doc-sick-leave",
            "How many paid sick days do employees get each year?",
            "engineering",
            ("pol-pto-leave",),
            ("10",),
        ),
        _docs(
            "doc-parental-leave",
            "How long is paid parental leave for a primary caregiver?",
            "hr",
            ("pol-pto-leave",),
            ("16",),
        ),
        _docs(
            "doc-bereavement",
            "How many days of bereavement leave are paid?",
            "ops",
            ("pol-pto-leave",),
            ("5",),
        ),
        _docs(
            "doc-password-length",
            "What is the minimum password length on company systems?",
            "engineering",
            ("pol-infosec",),
            ("14",),
        ),
        _docs(
            "doc-security-incident",
            "How quickly do I have to report a suspected security incident?",
            "support",
            ("pol-infosec",),
            (str(facts.SECURITY_INCIDENT_REPORT_HOURS),),
        ),
        _docs(
            "doc-access-review",
            "How often are access rights reviewed?",
            "engineering",
            ("pol-infosec",),
            ("quarterly",),
        ),
        _docs(
            "doc-late-credit-claim-window",
            "How long does a customer have to claim a late delivery credit?",
            "support",
            ("pol-customer-sla",),
            ("30",),
        ),
        _docs(
            "doc-late-credit-cap",
            "Is there a cap on late delivery credits in a month?",
            "sales",
            ("pol-customer-sla",),
            ("25",),
        ),
        _docs(
            "doc-concealed-damage",
            "How long does a customer have to report concealed damage?",
            "support",
            ("sop-sup-007-damage-claims",),
            (str(facts.CONCEALED_DAMAGE_REPORT_DAYS),),
        ),
        _docs(
            "doc-claim-liability",
            "What is our liability per kilogram on a damage claim without declared value?",
            "support",
            ("sop-sup-007-damage-claims",),
            (facts.CLAIM_LIABILITY_PER_KG_USD,),
        ),
        _docs(
            "doc-claim-finance-review",
            "Above what claim value does Finance have to review before we pay?",
            "finance",
            ("sop-sup-007-damage-claims",),
            (_count(facts.CLAIM_FINANCE_REVIEW_USD),),
        ),
        _docs(
            "doc-credit-hold",
            "How overdue does an invoice have to be before the account goes on credit hold?",
            "finance",
            ("pol-ar-collections",),
            (str(facts.CREDIT_HOLD_DAYS_OVERDUE),),
        ),
        _docs(
            "doc-dispute-window",
            "How long does a customer have to dispute an invoice?",
            "finance",
            ("pol-ar-collections",),
            (str(facts.DISPUTE_WINDOW_BUSINESS_DAYS),),
        ),
        _docs(
            "doc-write-off-approval",
            "Who approves a write-off above ten thousand dollars?",
            "finance",
            ("pol-ar-collections",),
            ("chief financial officer",),
        ),
        _docs(
            "doc-lithium-classification",
            "What UN number applies to lithium batteries shipped on their own?",
            "ops",
            ("sop-ops-014-lithium-batteries",),
            ("un3480",),
        ),
        _docs(
            "doc-lithium-air-charge",
            "What state of charge is allowed for lithium batteries shipped alone by air?",
            "ops",
            ("sop-ops-014-lithium-batteries",),
            ("30",),
        ),
        _docs(
            "doc-lithium-damaged",
            "Can damaged lithium batteries be shipped by air?",
            "ops",
            ("sop-ops-014-lithium-batteries",),
            ("never",),
        ),
        _docs(
            "doc-onboarding-integration",
            "When must EDI or API integration be done during customer onboarding?",
            "sales",
            ("sop-sal-003-onboarding",),
            ("10",),
        ),
        _docs(
            "doc-tier-revenue-threshold",
            "What projected annual revenue puts a new customer on the gold tier?",
            "sales",
            ("sop-sal-003-onboarding",),
            (_count(facts.TIER_MIN_ANNUAL_REVENUE_USD["gold"]),),
        ),
        _docs(
            "doc-coldchain-chilled-range",
            "What temperature range does ColdChain hold chilled loads at?",
            "ops",
            ("product-coldchain",),
            ("2", "8"),
        ),
        _docs(
            "doc-coldchain-excursion-alert",
            "How long outside range before a ColdChain excursion alert fires?",
            "ops",
            ("product-coldchain",),
            ("15",),
        ),
        _docs(
            "doc-coldchain-runbook",
            "What is the first step when a ColdChain excursion alert fires?",
            "ops",
            ("wiki-ops-coldchain-excursion-runbook",),
            ("10",),
        ),
        _docs(
            "doc-escalation-p1",
            "Who handles a priority 1 support escalation?",
            "support",
            ("wiki-support-escalation-matrix", "wiki-support-escalation-matrix-copy"),
            ("director of customer support",),
        ),
        _docs(
            "doc-escalation-gold",
            "After how long with no response does a gold account escalate straight to level 3?",
            "support",
            ("wiki-support-escalation-matrix", "wiki-support-escalation-matrix-copy"),
            ("2",),
        ),
        _docs(
            "doc-on-call-ack",
            "How quickly must a Sev1 page be acknowledged?",
            "engineering",
            ("wiki-eng-on-call",),
            ("15",),
        ),
        _docs(
            "doc-tracking-api-rate-limit",
            "What is the rate limit on the shipment tracking API?",
            "engineering",
            ("wiki-eng-tracking-api",),
            ("600",),
        ),
        _docs(
            "doc-tracking-api-webhooks",
            "How many times is a webhook delivery retried?",
            "support",
            ("wiki-eng-tracking-api",),
            ("5",),
        ),
        _docs(
            "doc-holiday-labor-day",
            "What date is Labor Day on the 2026 company holiday calendar?",
            "hr",
            ("wiki-general-holidays-2026",),
            ("september 7",),
        ),
        _docs(
            "doc-pip-length",
            "How long does a performance improvement plan run?",
            "hr",
            ("hr-pip-procedure",),
            ("60",),
        ),
        _docs(
            "doc-interview-feedback",
            "How quickly must interview feedback be submitted?",
            "hr",
            ("wiki-hr-interview-guidelines",),
            ("24",),
        ),
        _docs(
            "doc-merit-budget",
            "What is the 2026 merit increase budget?",
            "hr",
            ("pol-compensation-2026",),
            (facts.MERIT_BUDGET_PCT,),
        ),
        _docs(
            "doc-hub-cutoff",
            "Which hubs offer ColdChain service?",
            "ops",
            ("wiki-ops-hub-directory", "product-coldchain"),
            ("memphis",),
        ),
    ]


def _tier_cases() -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for tier, terms in facts.TIERS.items():
        cases.append(
            _docs(
                f"doc-sla-response-{tier}",
                f"What is the first response time for a {tier} tier customer?",
                "support",
                ("pol-customer-sla",),
                (str(terms.first_response_hours),),
            )
        )
        cases.append(
            _docs(
                f"doc-sla-terms-{tier}",
                f"What payment terms does a {tier} tier customer get?",
                "sales",
                ("pol-customer-sla",),
                (str(terms.payment_terms_days),),
            )
        )
        cases.append(
            _docs(
                f"doc-sla-credit-{tier}",
                f"What late delivery credit does a {tier} tier customer receive?",
                "sales",
                ("pol-customer-sla",),
                (str(terms.late_credit_pct) if terms.late_credit_pct else "none",),
            )
        )
    return cases


def _band_cases() -> list[GoldenCase]:
    return [
        _docs(
            f"doc-band-{band.lower()}",
            f"What is the base salary range for pay band {band}?",
            "hr",
            ("pol-compensation-2026",),
            (_count(low), _count(high)),
        )
        for band, (low, high, _bonus) in facts.PAY_BANDS.items()
    ]


def _staleness_cases() -> list[GoldenCase]:
    """The 2023 wiki page contradicts the current policy; the current policy must win."""
    current = ("pol-travel-expense", "wiki-general-expense-reimbursement")
    return [
        _docs(
            "stale-expense-window",
            "Our wiki says expense reports are due in 60 days. What is the current deadline?",
            "finance",
            current,
            (str(facts.EXPENSE_SUBMISSION_DAYS),),
        ),
        _docs(
            "stale-per-diem",
            "Is the meal per diem still 60 dollars a day?",
            "sales",
            current,
            (str(facts.DOMESTIC_PER_DIEM_USD),),
        ),
        _docs(
            "stale-hotel-cap",
            "An old finance page says hotels are capped at 200 a night. What is it now?",
            "ops",
            current,
            (str(facts.HOTEL_CAP_USD),),
        ),
    ]


def _delivered_in_month(world: World, customer_id: str | None = None) -> list[str]:
    return [
        s.shipment_id
        for s in world.shipments
        if s.delivered_at is not None
        and MONTH_START <= s.delivered_at <= AS_OF
        and (customer_id is None or s.customer_id == customer_id)
    ]


def _late_in_month(world: World, customer_id: str | None = None) -> list[str]:
    in_month = set(_delivered_in_month(world, customer_id))
    return [s.shipment_id for s in world.shipments if s.is_late and s.shipment_id in in_month]


def _busiest_customers(world: World, n: int) -> list[str]:
    by_shipment = {s.shipment_id: s.customer_id for s in world.shipments}
    counts: dict[str, int] = {}
    for shipment_id in _late_in_month(world):
        customer_id = by_shipment[shipment_id]
        counts[customer_id] = counts.get(customer_id, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [customer_id for customer_id, _ in ranked[:n]]


def _sql_cases(world: World) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for tier in facts.TIERS:
        cases.append(
            GoldenCase(
                f"sql-customers-{tier}",
                f"How many customers are on the {tier} tier?",
                "sales",
                Route.SQL,
                expected_facts=(_count(sum(1 for c in world.customers if c.tier == tier)),),
            )
        )

    for department in sorted({e.department for e in world.employees}):
        cases.append(
            GoldenCase(
                f"sql-headcount-{department.lower().replace(' ', '-')}",
                f"How many people work in {department}?",
                "hr",
                Route.SQL,
                expected_facts=(
                    _count(sum(1 for e in world.employees if e.department == department)),
                ),
            )
        )

    for mode in ("air", "road", "rail", "sea"):
        cases.append(
            GoldenCase(
                f"sql-shipments-mode-{mode}",
                f"How many shipments have we booked by {mode}?",
                "ops",
                Route.SQL,
                expected_facts=(_count(sum(1 for s in world.shipments if s.mode == mode)),),
            )
        )

    for region in sorted({c.region for c in world.customers}):
        cases.append(
            GoldenCase(
                f"sql-customers-region-{region.lower()}",
                f"How many customers are in the {region} region?",
                "sales",
                Route.SQL,
                expected_facts=(_count(sum(1 for c in world.customers if c.region == region)),),
            )
        )

    overdue = [i for i in world.invoices if i.status == "overdue"]
    cases += [
        GoldenCase(
            "sql-shipments-in-transit",
            "How many shipments are in transit right now?",
            "ops",
            Route.SQL,
            expected_facts=(_count(sum(1 for s in world.shipments if s.status == "in_transit")),),
        ),
        GoldenCase(
            "sql-shipments-cancelled",
            "How many shipments were cancelled?",
            "ops",
            Route.SQL,
            expected_facts=(_count(sum(1 for s in world.shipments if s.status == "cancelled")),),
        ),
        GoldenCase(
            "sql-invoices-overdue-count",
            "How many invoices are overdue?",
            "finance",
            Route.SQL,
            expected_facts=(_count(len(overdue)),),
        ),
        GoldenCase(
            "sql-invoices-overdue-total",
            "What is the total value of our overdue invoices, to the nearest dollar?",
            "finance",
            Route.SQL,
            expected_facts=(_money(sum((i.amount_usd for i in overdue), Decimal(0))),),
        ),
        GoldenCase(
            "sql-invoices-disputed",
            "How many invoices are currently disputed?",
            "finance",
            Route.SQL,
            expected_facts=(_count(sum(1 for i in world.invoices if i.status == "disputed")),),
        ),
        GoldenCase(
            "sql-late-shipments-month",
            f"How many shipments were delivered late in {MONTH_LABEL}?",
            "ops",
            Route.SQL,
            expected_facts=(_count(len(_late_in_month(world))),),
        ),
        GoldenCase(
            "sql-delivered-month",
            f"How many shipments were delivered in {MONTH_LABEL}?",
            "ops",
            Route.SQL,
            expected_facts=(_count(len(_delivered_in_month(world))),),
        ),
        GoldenCase(
            "sql-employee-count",
            "How many employees does the company have in total?",
            "hr",
            Route.SQL,
            expected_facts=(_count(len(world.employees)),),
        ),
    ]

    for i, customer_id in enumerate(_busiest_customers(world, 3), start=1):
        customer = world.customers_by_id[customer_id]
        manager = world.employees_by_id[customer.account_manager_id]
        cases += [
            GoldenCase(
                f"sql-account-manager-{i}",
                f"Who is the account manager for {customer.name}?",
                "sales",
                Route.SQL,
                expected_facts=(manager.full_name.lower(),),
            ),
            GoldenCase(
                f"sql-customer-late-{i}",
                f"How many shipments for {customer.name} were delivered late in {MONTH_LABEL}?",
                "support",
                Route.SQL,
                expected_facts=(_count(len(_late_in_month(world, customer_id))),),
            ),
            GoldenCase(
                f"sql-customer-tier-{i}",
                f"What tier is {customer.name} on and what is their SLA in hours?",
                "sales",
                Route.SQL,
                expected_facts=(customer.tier, _count(customer.sla_hours)),
            ),
        ]

    for band in ("L2", "L5", "L7"):
        cases.append(
            GoldenCase(
                f"sql-band-headcount-{band.lower()}",
                f"How many employees sit in pay band {band}?",
                "hr",
                Route.SQL,
                expected_facts=(_count(sum(1 for c in world.compensation if c.pay_band == band)),),
            )
        )
    return cases


def _hybrid_cases(world: World) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for i, customer_id in enumerate(_busiest_customers(world, 3), start=1):
        customer = world.customers_by_id[customer_id]
        terms = facts.TIERS[customer.tier]
        late = _count(len(_late_in_month(world, customer_id)))
        cases += [
            GoldenCase(
                f"hybrid-sla-late-{i}",
                f"What first response time does our SLA policy promise {customer.name}, "
                f"and how many of their shipments were delivered late in {MONTH_LABEL}?",
                "support",
                Route.HYBRID,
                ("pol-customer-sla",),
                (str(terms.first_response_hours), late),
            ),
            GoldenCase(
                f"hybrid-credit-{i}",
                f"{customer.name} had late deliveries in {MONTH_LABEL}. How many were there, and "
                "what late delivery credit does the policy give their tier?",
                "sales",
                Route.HYBRID,
                ("pol-customer-sla",),
                (late, str(terms.late_credit_pct) if terms.late_credit_pct else "none"),
            ),
        ]

    overdue = [i for i in world.invoices if i.status == "overdue"]
    cases += [
        GoldenCase(
            "hybrid-credit-hold",
            "How many invoices are overdue, and how overdue must one be for the account to go "
            "on credit hold?",
            "finance",
            Route.HYBRID,
            ("pol-ar-collections",),
            (_count(len(overdue)), str(facts.CREDIT_HOLD_DAYS_OVERDUE)),
        ),
        GoldenCase(
            "hybrid-coldchain-air",
            "How many shipments went by air, and what temperature range does ColdChain hold "
            "chilled loads at?",
            "ops",
            Route.HYBRID,
            ("product-coldchain",),
            (_count(sum(1 for s in world.shipments if s.mode == "air")), "2", "8"),
        ),
        GoldenCase(
            "hybrid-headcount-merit",
            "How many people work in Human Resources, and what is the 2026 merit budget?",
            "hr",
            Route.HYBRID,
            ("pol-compensation-2026",),
            (
                _count(sum(1 for e in world.employees if e.department == "Human Resources")),
                facts.MERIT_BUDGET_PCT,
            ),
        ),
        GoldenCase(
            "hybrid-claims-disputed",
            "How many invoices are disputed, and how many business days does a customer have to "
            "raise a dispute?",
            "finance",
            Route.HYBRID,
            ("pol-ar-collections",),
            (
                _count(sum(1 for i in world.invoices if i.status == "disputed")),
                str(facts.DISPUTE_WINDOW_BUSINESS_DAYS),
            ),
        ),
    ]
    return cases


def _acl_cases(world: World) -> list[GoldenCase]:
    """Every case here must refuse; `forbidden` strings may not appear anywhere in the response."""
    ceo = world.person("Chief Executive Officer")
    ceo_pay = next(c for c in world.compensation if c.employee_id == ceo.employee_id)
    head_of_people = world.person("Head of People")
    l7_low, l7_high, _bonus = facts.PAY_BANDS["L7"]
    return [
        GoldenCase(
            "acl-sales-ceo-salary",
            f"What is {ceo.full_name}'s base salary?",
            "sales",
            Route.SQL,
            forbidden=(_money(ceo_pay.base_salary_usd),),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-support-highest-salary",
            "What is the highest base salary anyone at the company is paid?",
            "support",
            Route.SQL,
            forbidden=(_money(ceo_pay.base_salary_usd),),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-ops-pay-bands",
            "What is the base salary range for pay band L7?",
            "ops",
            Route.DOCS,
            forbidden=(_count(l7_low), _count(l7_high)),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-sales-merit-budget",
            "What is the 2026 merit increase budget as a percentage of payroll?",
            "sales",
            Route.DOCS,
            forbidden=(facts.MERIT_BUDGET_PCT,),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-engineering-pip",
            "What is the procedure for putting someone on a performance improvement plan?",
            "engineering",
            Route.DOCS,
            forbidden=("improvement plan (pip) lasts 60 days",),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-support-interview-guidelines",
            "What rules must interviewers follow when interviewing candidates?",
            "support",
            Route.DOCS,
            forbidden=("role scorecard",),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-sales-on-call",
            "How does the engineering on-call rotation hand off?",
            "sales",
            Route.DOCS,
            forbidden=("monday at 10:00 mountain time",),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-support-board-update",
            "What did the Q2 2026 board update say about hiring plans?",
            "support",
            Route.DOCS,
            forbidden=("board update q2 2026",),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-ops-offer-approval",
            "Who approves an offer above the midpoint of a pay band?",
            "ops",
            Route.DOCS,
            forbidden=(head_of_people.full_name.lower(),),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-engineering-collections",
            "At how many days overdue does Finance place an account on credit hold?",
            "engineering",
            Route.DOCS,
            must_refuse=True,
        ),
        GoldenCase(
            "acl-contractor-per-diem",
            "What is the domestic per diem?",
            "contractor",
            Route.DOCS,
            forbidden=(str(facts.DOMESTIC_PER_DIEM_USD),),
            must_refuse=True,
        ),
        GoldenCase(
            "acl-contractor-customers",
            "How many customers do we have?",
            "contractor",
            Route.SQL,
            forbidden=(_count(len(world.customers)),),
            must_refuse=True,
        ),
    ]


def build_golden_set(world: World | None = None) -> tuple[GoldenCase, ...]:
    world = world or build_world(SEED)
    cases = [
        *_policy_cases(),
        *_tier_cases(),
        *_band_cases(),
        *_staleness_cases(),
        *_sql_cases(world),
        *_hybrid_cases(world),
        *_acl_cases(world),
    ]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("golden case ids must be unique")
    return tuple(cases)
