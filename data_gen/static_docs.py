"""Policies (PDF), SOPs and product sheets (DOCX) and wiki pages (Markdown and legacy HTML).

Deliberate corpus hazards for retrieval and evaluation:
- restricted documents (pay bands, board update, PIP procedure) that most personas must not see
- a stale 2023 wiki export that contradicts the current expense policy
- a near-duplicate wiki page that deduplication should collapse
"""

from datetime import UTC, datetime

from data_gen.facts import (
    CLAIM_FINANCE_REVIEW_USD,
    CLAIM_LIABILITY_PER_KG_USD,
    CONCEALED_DAMAGE_REPORT_DAYS,
    CREDIT_HOLD_DAYS_OVERDUE,
    DISPUTE_WINDOW_BUSINESS_DAYS,
    DOMESTIC_PER_DIEM_USD,
    EXPENSE_HARD_LIMIT_DAYS,
    EXPENSE_SUBMISSION_DAYS,
    HIGH_COST_HOTEL_CAP_USD,
    HOTEL_CAP_USD,
    INTERNATIONAL_PER_DIEM_USD,
    LEGACY_DOMESTIC_PER_DIEM_USD,
    LEGACY_EXPENSE_SUBMISSION_DAYS,
    LEGACY_HOTEL_CAP_USD,
    MERIT_BUDGET_PCT,
    MILEAGE_RATE_USD,
    PAY_BANDS,
    RECEIPT_REQUIRED_USD,
    SECURITY_INCIDENT_REPORT_HOURS,
    TIER_MIN_ANNUAL_REVENUE_USD,
    TIERS,
)
from data_gen.models import SourceDocument, Table, section
from data_gen.world import World

HUB_CODES = {
    "Denver": ("DEN", "17:00"),
    "Los Angeles": ("LAX", "18:00"),
    "Seattle": ("SEA", "16:30"),
    "Chicago": ("CHI", "18:00"),
    "Dallas": ("DFW", "17:30"),
    "Memphis": ("MEM", "20:00"),
    "Atlanta": ("ATL", "17:30"),
    "Newark": ("EWR", "16:00"),
}
COLDCHAIN_HUBS = ("Chicago", "Memphis", "Newark", "Los Angeles")


def _on(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 9, 0, tzinfo=UTC)


def build_static_documents(world: World) -> list[SourceDocument]:
    return [
        *_policies(world),
        *_sops(world),
        *_wiki(world),
    ]


def _policies(world: World) -> list[SourceDocument]:
    cfo = world.person("Chief Financial Officer")
    head_of_people = world.person("Head of People")
    tier_rows = tuple(
        (
            tier.title(),
            f"{t.first_response_hours} hours",
            f"{t.late_credit_pct}% of the shipment invoice" if t.late_credit_pct else "None",
            f"Net {t.payment_terms_days}",
        )
        for tier, t in TIERS.items()
    )
    band_rows = tuple(
        (band, f"${low:,}", f"${high:,}", f"{bonus}%")
        for band, (low, high, bonus) in PAY_BANDS.items()
    )
    return [
        SourceDocument(
            doc_id="pol-travel-expense",
            key="documents/policies/travel-and-expense-policy.pdf",
            title="Travel and Expense Policy",
            allowed_groups=("all-staff",),
            owner="Finance",
            updated_at=_on(2026, 2, 1),
            sections=(
                section(
                    "1. Purpose and scope",
                    "This policy applies to all Larkspur Logistics employees and contractors who "
                    "incur travel or other expenses on behalf of the company. It replaces the 2023 "
                    f"Travel Guidelines and is owned by the Chief Financial Officer, {cfo.full_name}.",
                ),
                section(
                    "2. Booking travel",
                    "Book all flights, rail and hotels through the Concur travel portal, at least 14 "
                    "days before departure where possible. Economy class is required for flights "
                    "under 6 hours. Premium economy may be booked for flights of 6 hours or longer "
                    "with manager approval.",
                ),
                section(
                    "3. Lodging, meals and mileage",
                    f"Hotel rates are capped at ${HOTEL_CAP_USD} per night in standard markets and "
                    f"${HIGH_COST_HOTEL_CAP_USD} per night in high-cost markets (New York City, San "
                    "Francisco, Seattle, Los Angeles and Boston).",
                    f"The domestic per diem is ${DOMESTIC_PER_DIEM_USD} per day and the international "
                    f"per diem is ${INTERNATIONAL_PER_DIEM_USD} per day. The per diem covers meals and "
                    "incidentals. Alcohol is not reimbursable unless it is pre-approved client "
                    "entertainment.",
                    f"Personal vehicle use is reimbursed at ${MILEAGE_RATE_USD} per mile. Use a fleet "
                    "vehicle for trips between hubs when one is available.",
                    Table(
                        ("Expense", "Limit", "Approval"),
                        (
                            ("Hotel, standard market", f"${HOTEL_CAP_USD} per night", "Manager"),
                            (
                                "Hotel, high-cost market",
                                f"${HIGH_COST_HOTEL_CAP_USD} per night",
                                "Manager",
                            ),
                            ("Domestic per diem", f"${DOMESTIC_PER_DIEM_USD} per day", "None"),
                            (
                                "International per diem",
                                f"${INTERNATIONAL_PER_DIEM_USD} per day",
                                "None",
                            ),
                            ("Client entertainment", "$150 per attendee", "Vice President"),
                            ("Personal vehicle", f"${MILEAGE_RATE_USD} per mile", "None"),
                        ),
                    ),
                ),
                section(
                    "4. Submitting expenses",
                    f"Submit expense reports in Concur within {EXPENSE_SUBMISSION_DAYS} days of the "
                    f"expense date. Reports submitted after {EXPENSE_SUBMISSION_DAYS} days require "
                    f"written approval from the Controller, and expenses older than "
                    f"{EXPENSE_HARD_LIMIT_DAYS} days will not be reimbursed.",
                    f"Attach an itemized receipt for every expense of ${RECEIPT_REQUIRED_USD} or more. "
                    "Lost receipts require a signed missing receipt declaration.",
                ),
                section(
                    "5. Corporate cards",
                    "Corporate cards are for business expenses only. Personal charges must be repaid "
                    "within 10 days and repeated misuse results in the card being cancelled.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="pol-pto-leave",
            key="documents/policies/pto-and-leave-policy.pdf",
            title="Paid Time Off and Leave Policy",
            allowed_groups=("all-staff",),
            owner="Human Resources",
            updated_at=_on(2026, 1, 5),
            sections=(
                section(
                    "1. Paid time off",
                    "Full-time employees accrue paid time off (PTO) each pay period according to "
                    "their length of service.",
                    Table(
                        ("Years of service", "Annual PTO"),
                        (
                            ("0 to 2 years", "15 days"),
                            ("3 to 5 years", "20 days"),
                            ("6+ years", "25 days"),
                        ),
                    ),
                    "Up to 5 unused PTO days carry over into the next calendar year and must be used "
                    "by March 31. Requests for more than 3 consecutive days must be submitted in "
                    "Workday at least 2 weeks in advance.",
                ),
                section(
                    "2. Sick leave",
                    "Employees receive 10 days of paid sick leave per year, separate from PTO. A "
                    "doctor's note is required only for absences longer than 3 consecutive days.",
                ),
                section(
                    "3. Parental and bereavement leave",
                    "Primary caregivers receive 16 weeks of fully paid parental leave and secondary "
                    "caregivers receive 6 weeks. Leave must begin within 12 months of the birth or "
                    "adoption.",
                    "Employees receive up to 5 days of paid bereavement leave for the loss of an "
                    "immediate family member.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="pol-infosec",
            key="documents/policies/information-security-policy.pdf",
            title="Information Security Policy",
            allowed_groups=("all-staff",),
            owner="Engineering",
            updated_at=_on(2026, 3, 16),
            sections=(
                section(
                    "1. Accounts and authentication",
                    "Multi-factor authentication is required on every company system. Passwords must "
                    "be at least 14 characters and must never be reused across systems. Access "
                    "rights are reviewed quarterly by each system owner.",
                ),
                section(
                    "2. Devices",
                    "Company laptops must use full-disk encryption and lock after 5 minutes of "
                    "inactivity. USB storage devices are blocked by default.",
                ),
                section(
                    "3. Data classification",
                    "Company information is classified as Public, Internal, Confidential or "
                    "Restricted. Salary data, board materials and customer contracts are Restricted. "
                    "Customer shipment data is Confidential and must not be pasted into public AI "
                    "tools or personal email.",
                ),
                section(
                    "4. Reporting incidents",
                    "Report any suspected security incident, lost device or phishing email to "
                    "security@larkspur.example or the #sec-incidents Slack channel within "
                    f"{SECURITY_INCIDENT_REPORT_HOURS} hour of discovery. Do not attempt to "
                    "investigate on your own.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="pol-customer-sla",
            key="documents/policies/customer-service-level-agreement-tiers.pdf",
            title="Customer Service Level Agreement Tiers",
            allowed_groups=("all-staff",),
            owner="Customer Support",
            updated_at=_on(2026, 1, 12),
            sections=(
                section(
                    "1. Tiers",
                    "Every customer account is assigned a Gold, Silver or Bronze tier at onboarding. "
                    "The tier sets the support first response time, the late delivery credit and the "
                    "payment terms.",
                    Table(
                        ("Tier", "First response", "Late delivery credit", "Payment terms"),
                        tier_rows,
                    ),
                ),
                section(
                    "2. Late deliveries",
                    "A shipment is late when it is delivered after its promised delivery time. Late "
                    "delivery credits are calculated on the invoice for that shipment and must be "
                    "claimed within 30 days of delivery. Credits are capped at 25% of the customer's "
                    "invoiced total for the month.",
                    "Credits do not apply to delays caused by force majeure, including declared "
                    "severe weather events, port closures and customs holds caused by the customer's "
                    "documentation.",
                ),
                section(
                    "3. Support response",
                    "First response time is measured from ticket creation to the first reply from a "
                    "Larkspur agent, around the clock. Gold accounts also have a named account "
                    "manager; Bronze accounts are served by the shared account pool.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="pol-ar-collections",
            key="documents/policies/accounts-receivable-collections-policy.pdf",
            title="Accounts Receivable and Collections Policy",
            allowed_groups=("finance", "sales", "exec"),
            owner="Finance",
            updated_at=_on(2026, 4, 20),
            sections=(
                section(
                    "1. Payment terms and reminders",
                    "Standard payment terms are Net 30. Gold tier customers receive Net 45. Automated "
                    "reminders are sent 7 days before the due date, on the due date and 15 days "
                    "after the due date.",
                ),
                section(
                    "2. Credit hold",
                    f"Accounts with any invoice more than {CREDIT_HOLD_DAYS_OVERDUE} days overdue are "
                    "placed on credit hold by Finance. New bookings for an account on credit hold "
                    "require Controller approval. The account manager must be notified the same day.",
                ),
                section(
                    "3. Disputes and write-offs",
                    "Customers must raise invoice disputes within "
                    f"{DISPUTE_WINDOW_BUSINESS_DAYS} business days of the invoice date. Disputed "
                    "invoices are excluded from reminders while under review. Write-offs above "
                    "$10,000 require approval from the Chief Financial Officer.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="pol-compensation-2026",
            key="documents/policies/compensation-and-pay-bands-2026.pdf",
            title="Compensation and Pay Bands 2026",
            allowed_groups=("hr", "exec"),
            owner="Human Resources",
            updated_at=_on(2026, 3, 2),
            sections=(
                section(
                    "1. Pay bands",
                    "Every role maps to a pay band. Base salary must fall within the band range.",
                    Table(("Band", "Minimum base", "Maximum base", "Bonus target"), band_rows),
                ),
                section(
                    "2. 2026 merit cycle",
                    f"The 2026 merit increase budget is {MERIT_BUDGET_PCT}% of base payroll, "
                    "effective April 1. Promotions are reviewed in the April and October cycles.",
                ),
                section(
                    "3. Offers",
                    "Offers above the midpoint of the band require approval from the Head of People, "
                    f"{head_of_people.full_name}. Compensation data is Restricted and may only be "
                    "shared with HR and the executive team.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="exec-board-update-2026-q2",
            key="documents/board/board-update-2026-q2.pdf",
            title="Board Update Q2 2026",
            allowed_groups=("exec",),
            owner="Executive",
            updated_at=_on(2026, 7, 21),
            sections=(
                section(
                    "1. Financial summary",
                    "Q2 2026 revenue was $48.6M, up 11% year over year. Operating margin was 7.9%, "
                    "down from 8.6% in Q1 because of higher linehaul fuel costs. Rail volume fell 6% "
                    "while air volume grew 19%.",
                ),
                section(
                    "2. Service performance",
                    "On-time delivery was 88.4% against a target of 92%. Sea and rail were the main "
                    "contributors to late deliveries. Three accounts with repeated delays are at "
                    "risk at renewal.",
                ),
                section(
                    "3. Project Heron (confidential)",
                    "Management recommends acquiring Cobalt Freight Systems, a regional cold chain "
                    "carrier, for approximately $62M. A letter of intent is targeted for October "
                    "2026. Project Heron must not be discussed outside the executive team.",
                ),
                section(
                    "4. Headcount",
                    "The plan adds 14 Operations roles in H2 2026, mainly hub staff for Memphis and "
                    "Newark ColdChain expansion.",
                ),
            ),
        ),
    ]


def _sops(world: World) -> list[SourceDocument]:
    return [
        SourceDocument(
            doc_id="sop-ops-014-lithium-batteries",
            key="documents/sops/sop-ops-014-lithium-battery-shipments.docx",
            title="SOP-OPS-014 Handling Lithium Battery Shipments",
            allowed_groups=("all-staff",),
            owner="Operations",
            updated_at=_on(2025, 11, 3),
            sections=(
                section(
                    "Purpose",
                    "This procedure covers accepting, labeling and routing shipments that contain "
                    "lithium ion batteries.",
                ),
                section(
                    "Classification",
                    "Batteries shipped on their own are UN3480. Batteries packed with or contained in "
                    "equipment are UN3481. The shipper must state the classification on the booking.",
                    Table(
                        ("Mode", "UN3480 (batteries alone)", "UN3481 (with equipment)"),
                        (
                            ("Air", "Allowed at 30% state of charge or less", "Allowed"),
                            ("Road", "Allowed", "Allowed"),
                            ("Rail", "Allowed", "Allowed"),
                            ("Sea", "Allowed with dangerous goods declaration", "Allowed"),
                        ),
                    ),
                ),
                section(
                    "Procedure",
                    "Apply the Class 9 lithium battery hazard label to every package and confirm the "
                    "shipper's declaration matches the label. The hub supervisor must sign off before "
                    "a UN3480 shipment is loaded.",
                    "Damaged, defective or recalled batteries must never be shipped by air. They may "
                    "only travel by road in approved packaging after Operations approval.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="sop-sup-007-damage-claims",
            key="documents/sops/sop-sup-007-freight-damage-claims.docx",
            title="SOP-SUP-007 Freight Damage Claims",
            allowed_groups=("support", "ops", "finance"),
            owner="Customer Support",
            updated_at=_on(2026, 5, 11),
            sections=(
                section(
                    "Reporting windows",
                    "Visible damage must be noted on the delivery receipt at the time of delivery. "
                    f"Concealed damage must be reported within {CONCEALED_DAMAGE_REPORT_DAYS} days of "
                    "delivery. Claims reported later are declined unless the Director of Customer "
                    "Support approves an exception.",
                ),
                section(
                    "Liability",
                    f"Liability is limited to ${CLAIM_LIABILITY_PER_KG_USD} per kilogram unless the "
                    "customer purchased declared value coverage for the shipment.",
                ),
                section(
                    "Procedure",
                    "Open a claim ticket, collect the delivery receipt and photos of the packaging and "
                    "contents, and request an inspection from the destination hub within 2 business "
                    f"days. Claims above ${CLAIM_FINANCE_REVIEW_USD:,} require Finance review before "
                    "any payment. The target resolution time is 30 days.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="sop-sal-003-onboarding",
            key="documents/sops/sop-sal-003-customer-onboarding.docx",
            title="SOP-SAL-003 Customer Onboarding",
            allowed_groups=("sales", "support", "finance"),
            owner="Sales",
            updated_at=_on(2026, 2, 23),
            sections=(
                section(
                    "Tier assignment",
                    "Assign the tier from projected annual revenue: Gold from "
                    f"${TIER_MIN_ANNUAL_REVENUE_USD['gold']:,}, Silver from "
                    f"${TIER_MIN_ANNUAL_REVENUE_USD['silver']:,}, and Bronze below that.",
                ),
                section(
                    "Checklist",
                    Table(
                        ("Step", "Owner", "Due"),
                        (
                            ("Credit check", "Finance", "Before contract signature"),
                            ("Tier assignment and pricing", "Account manager", "At signature"),
                            ("EDI or API integration", "Engineering", "Within 10 business days"),
                            (
                                "Kickoff call",
                                "Account manager and Support Lead",
                                "Within 5 business days",
                            ),
                        ),
                    ),
                ),
            ),
        ),
        SourceDocument(
            doc_id="product-coldchain",
            key="documents/product/larkspur-coldchain-product-sheet.docx",
            title="Larkspur ColdChain Product Sheet",
            allowed_groups=("all-staff",),
            owner="Operations",
            updated_at=_on(2026, 6, 8),
            sections=(
                section(
                    "Overview",
                    "Larkspur ColdChain is our temperature-controlled freight service for "
                    "pharmaceuticals and fresh food. It is compliant with Good Distribution Practice "
                    "(GDP) guidelines.",
                ),
                section(
                    "Service specification",
                    "Chilled loads are held between 2 and 8 degrees Celsius and frozen loads between "
                    "-25 and -15 degrees Celsius. Trailer telemetry reports temperature every 5 "
                    "minutes, and an excursion alert fires when a load is outside its range for 15 "
                    "minutes.",
                    f"ColdChain is available from the {', '.join(COLDCHAIN_HUBS[:-1])} and "
                    f"{COLDCHAIN_HUBS[-1]} hubs.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="hr-pip-procedure",
            key="documents/hr/performance-improvement-plan-procedure.docx",
            title="Performance Improvement Plan Procedure",
            allowed_groups=("hr",),
            owner="Human Resources",
            updated_at=_on(2025, 9, 15),
            sections=(
                section(
                    "Procedure",
                    "A performance improvement plan (PIP) lasts 60 days, with documented check-ins "
                    "every 2 weeks. An HR Business Partner must review the plan before it is issued, "
                    "and every PIP is recorded in Workday.",
                    "At the end of the plan, the manager and the HR Business Partner decide whether "
                    "to close the plan, extend it by up to 30 days or begin separation.",
                ),
            ),
        ),
    ]


def _wiki(world: World) -> list[SourceDocument]:
    support_director = world.person("Director of Customer Support")
    ops_vp = world.person("VP of Operations")
    supervisors = {e.location: e.full_name for e in world.employees if e.title == "Hub Supervisor"}
    escalation = (
        section(
            "Escalation levels",
            Table(
                ("Level", "Who", "When"),
                (
                    ("1", "Support Specialist", "All new tickets"),
                    ("2", "Senior Support Specialist", "No resolution after 1 business day"),
                    ("3", "Support Lead", "Customer escalation or a breached first response"),
                    ("4", f"Director of Customer Support ({support_director.full_name})", "P1"),
                ),
            ),
        ),
        section(
            "Priority 1 incidents",
            "A P1 is a service outage or a delay affecting ColdChain or medical loads. Gold accounts "
            "escalate straight to level 3 when they have had no response for 2 hours. Hub-wide "
            f"delays go to the VP of Operations, {ops_vp.full_name}.",
        ),
    )
    expense_current = (
        section(
            "How to get reimbursed",
            f"Submit your expense report in Concur within {EXPENSE_SUBMISSION_DAYS} days of the "
            f"expense date. The domestic per diem is ${DOMESTIC_PER_DIEM_USD} per day and hotels "
            f"are capped at ${HOTEL_CAP_USD} per night in standard markets.",
            "Reimbursements are paid with the next payroll run after your manager approves.",
        ),
        section(
            "Where to find the rules",
            "The Travel and Expense Policy (February 2026) is the source of truth. This page is a "
            "quick summary.",
        ),
    )
    expense_legacy = (
        section(
            "Submitting expenses",
            f"Expense reports must be submitted within {LEGACY_EXPENSE_SUBMISSION_DAYS} days. The "
            f"meal per diem is ${LEGACY_DOMESTIC_PER_DIEM_USD} per day and hotel stays are "
            f"reimbursed up to ${LEGACY_HOTEL_CAP_USD} per night.",
            "Email scanned receipts to expenses@larkspur.example.",
        ),
    )
    return [
        SourceDocument(
            doc_id="wiki-general-expense-reimbursement",
            key="wiki/general/expense-reimbursement.md",
            title="Expense Reimbursement",
            allowed_groups=("all-staff",),
            owner="Finance",
            updated_at=_on(2026, 2, 3),
            sections=expense_current,
        ),
        SourceDocument(
            doc_id="wiki-legacy-finance-expense-reimbursement",
            key="wiki/legacy/finance/expense-reimbursement.html",
            title="Expense Reimbursement (Finance Space)",
            allowed_groups=("all-staff",),
            owner="Finance",
            updated_at=_on(2023, 5, 10),
            sections=expense_legacy,
        ),
        SourceDocument(
            doc_id="wiki-ops-hub-directory",
            key="wiki/operations/hub-directory.md",
            title="Hub Directory",
            allowed_groups=("all-staff",),
            owner="Operations",
            updated_at=_on(2026, 7, 1),
            sections=(
                section(
                    "Hubs",
                    "Order cutoff is the latest local time a booking can make the same-day linehaul.",
                    Table(
                        ("Hub", "Code", "Order cutoff", "ColdChain", "Supervisor"),
                        tuple(
                            (
                                hub,
                                code,
                                cutoff,
                                "Yes" if hub in COLDCHAIN_HUBS else "No",
                                supervisors[hub],
                            )
                            for hub, (code, cutoff) in HUB_CODES.items()
                        ),
                    ),
                ),
            ),
        ),
        SourceDocument(
            doc_id="wiki-support-escalation-matrix",
            key="wiki/support/escalation-matrix.md",
            title="Support Escalation Matrix",
            allowed_groups=("support", "sales", "ops"),
            owner="Customer Support",
            updated_at=_on(2026, 4, 2),
            sections=escalation,
        ),
        SourceDocument(
            doc_id="wiki-support-escalation-matrix-copy",
            key="wiki/support/escalation-matrix-copy.md",
            title="Copy of Support Escalation Matrix",
            allowed_groups=("support", "sales", "ops"),
            owner="Customer Support",
            updated_at=_on(2026, 4, 9),
            sections=escalation,
        ),
        SourceDocument(
            doc_id="wiki-ops-coldchain-excursion-runbook",
            key="wiki/operations/coldchain-excursion-runbook.md",
            title="ColdChain Temperature Excursion Runbook",
            allowed_groups=("ops", "support"),
            owner="Operations",
            updated_at=_on(2026, 6, 15),
            sections=(
                section(
                    "When an excursion alert fires",
                    "Call the driver within 10 minutes and confirm the reefer unit setpoint. If the "
                    "unit has failed, divert to the nearest ColdChain hub and move the load to a "
                    "backup trailer.",
                    "Open a P1 ticket, attach the telemetry export and notify the customer's quality "
                    "team within 1 hour. Never relabel or release a load that had an excursion "
                    "without the customer's written approval.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="wiki-eng-on-call",
            key="wiki/engineering/on-call.md",
            title="Engineering On-Call",
            allowed_groups=("engineering",),
            owner="Engineering",
            updated_at=_on(2026, 5, 4),
            sections=(
                section(
                    "Rotation",
                    "On-call rotates weekly with the handoff every Monday at 10:00 Mountain Time. "
                    "Sev1 pages must be acknowledged within 15 minutes and Sev2 pages within 1 hour.",
                    "Every page needs a linked runbook. If a runbook is missing, file a follow-up "
                    "ticket in the postmortem.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="wiki-eng-tracking-api",
            key="wiki/engineering/tracking-api.md",
            title="Shipment Tracking API",
            allowed_groups=("engineering", "support"),
            owner="Engineering",
            updated_at=_on(2026, 3, 30),
            sections=(
                section(
                    "Limits and webhooks",
                    "The tracking API allows 600 requests per minute per customer API key. Webhook "
                    "deliveries are retried 5 times with exponential backoff, starting at 30 seconds.",
                    Table(
                        ("Status code", "Meaning"),
                        (
                            ("BKD", "Booked"),
                            ("ITR", "In transit"),
                            ("DLV", "Delivered"),
                            ("EXC", "Exception, for example a delay or damage"),
                        ),
                    ),
                ),
            ),
        ),
        SourceDocument(
            doc_id="wiki-hr-interview-guidelines",
            key="wiki/hr/interview-guidelines.md",
            title="Interview Guidelines",
            allowed_groups=("hr",),
            owner="Human Resources",
            updated_at=_on(2026, 1, 20),
            sections=(
                section(
                    "Structured interviews",
                    "Every interviewer uses the role scorecard and submits feedback within 24 hours. "
                    "Never ask candidates about age, family status, religion or health.",
                ),
            ),
        ),
        SourceDocument(
            doc_id="wiki-general-holidays-2026",
            key="wiki/general/company-holidays-2026.md",
            title="Company Holidays 2026",
            allowed_groups=("all-staff",),
            owner="Human Resources",
            updated_at=_on(2025, 12, 1),
            sections=(
                section(
                    "Holidays",
                    "Hubs operate on holidays with reduced staffing. Office staff observe these "
                    "holidays.",
                    Table(
                        ("Holiday", "Date"),
                        (
                            ("New Year's Day", "January 1"),
                            ("Memorial Day", "May 25"),
                            ("Independence Day", "July 3 (observed)"),
                            ("Labor Day", "September 7"),
                            ("Thanksgiving", "November 26 and 27"),
                            ("Christmas", "December 24 and 25"),
                        ),
                    ),
                ),
            ),
        ),
    ]
