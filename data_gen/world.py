"""Deterministic structured data for Larkspur Logistics, the operational system of record.

Everything derives from one seed and a fixed as-of date, so the database rows, the documents
that mention them and the evaluation answers computed from them always agree.
"""

import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cached_property

from faker import Faker

from data_gen.facts import PAY_BANDS, TIERS

AS_OF = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
COMPANY_DOMAIN = "larkspur.example"
CENTS = Decimal("0.01")
HQ = "Denver"

HUB_REGIONS: dict[str, str] = {
    "Denver": "West",
    "Los Angeles": "West",
    "Seattle": "West",
    "Chicago": "Central",
    "Dallas": "Central",
    "Memphis": "Central",
    "Atlanta": "East",
    "Newark": "East",
}


@dataclass(frozen=True)
class Employee:
    employee_id: str
    full_name: str
    email: str
    department: str
    title: str
    manager_id: str | None
    location: str
    hire_date: date

    @property
    def first_name(self) -> str:
        return self.full_name.split(" ", 1)[0]


@dataclass(frozen=True)
class Compensation:
    employee_id: str
    base_salary_usd: Decimal
    bonus_pct: Decimal
    pay_band: str
    effective_date: date


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    industry: str
    tier: str
    sla_hours: int
    region: str
    account_manager_id: str
    created_on: date

    @property
    def domain(self) -> str:
        return re.sub(r"[^a-z0-9]+", "", self.name.lower()) + ".example"


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    customer_id: str
    origin: str
    destination: str
    mode: str
    status: str
    weight_kg: Decimal
    booked_at: datetime
    promised_at: datetime
    delivered_at: datetime | None

    @property
    def is_late(self) -> bool:
        return self.delivered_at is not None and self.delivered_at > self.promised_at


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    customer_id: str
    shipment_id: str
    amount_usd: Decimal
    issued_on: date
    due_on: date
    paid_on: date | None
    status: str


@dataclass(frozen=True)
class World:
    employees: tuple[Employee, ...]
    compensation: tuple[Compensation, ...]
    customers: tuple[Customer, ...]
    shipments: tuple[Shipment, ...]
    invoices: tuple[Invoice, ...]

    @cached_property
    def employees_by_id(self) -> dict[str, Employee]:
        return {e.employee_id: e for e in self.employees}

    @cached_property
    def customers_by_id(self) -> dict[str, Customer]:
        return {c.customer_id: c for c in self.customers}

    @cached_property
    def invoices_by_shipment(self) -> dict[str, Invoice]:
        return {i.shipment_id: i for i in self.invoices}

    def person(self, title: str) -> Employee:
        return next(e for e in self.employees if e.title == title)

    def staff(self, department: str) -> list[Employee]:
        return [e for e in self.employees if e.department == department]


@dataclass(frozen=True)
class _Department:
    name: str
    head_title: str
    titles: tuple[str, ...]
    headcount: int  # excluding the head


_DEPARTMENTS = (
    _Department("Sales", "VP of Sales", ("Account Executive", "Senior Account Executive"), 16),
    _Department(
        "Customer Support",
        "Director of Customer Support",
        ("Support Specialist", "Senior Support Specialist", "Support Lead"),
        20,
    ),
    _Department(
        "Operations", "VP of Operations", ("Dispatcher", "Fleet Coordinator", "Hub Supervisor"), 32
    ),
    _Department(
        "Finance", "Controller", ("Financial Analyst", "Accounts Receivable Specialist"), 9
    ),
    _Department(
        "Human Resources",
        "Head of People",
        ("HR Generalist", "HR Business Partner", "Recruiter"),
        6,
    ),
    _Department(
        "Engineering",
        "Director of Engineering",
        ("Software Engineer", "Senior Software Engineer", "Data Engineer"),
        15,
    ),
)

_TITLE_BANDS = {
    "Chief Executive Officer": "L7",
    "Chief Financial Officer": "L6",
    "Chief Operating Officer": "L6",
    "VP of Sales": "L5",
    "VP of Operations": "L5",
    "Director of Customer Support": "L5",
    "Director of Engineering": "L5",
    "Controller": "L5",
    "Head of People": "L5",
    "Support Lead": "L3",
    "Hub Supervisor": "L3",
    "Senior Account Executive": "L3",
    "Senior Support Specialist": "L2",
    "Senior Software Engineer": "L4",
    "Software Engineer": "L3",
    "Data Engineer": "L3",
    "HR Business Partner": "L3",
    "Account Executive": "L2",
    "Fleet Coordinator": "L2",
    "Financial Analyst": "L2",
    "Accounts Receivable Specialist": "L2",
    "HR Generalist": "L2",
    "Recruiter": "L2",
    "Dispatcher": "L1",
    "Support Specialist": "L1",
}

_INDUSTRY_SUFFIXES = {
    "Retail": ("Market", "Retail Group", "Stores"),
    "Pharmaceuticals": ("Pharma", "Biologics", "Health"),
    "Automotive": ("Motors", "Auto Parts", "Mobility"),
    "Consumer Electronics": ("Electronics", "Devices", "Technologies"),
    "Food & Beverage": ("Foods", "Beverages", "Provisions"),
    "Industrial Equipment": ("Industrial", "Machinery", "Equipment"),
    "Apparel": ("Apparel", "Outfitters", "Textiles"),
}


@dataclass(frozen=True)
class ModeProfile:
    share: float
    transit_hours: tuple[int, int]
    late_rate: float
    max_delay_hours: int
    weight_kg: tuple[float, float]
    rate_per_kg: Decimal
    base_fee: Decimal


MODES: dict[str, ModeProfile] = {
    "air": ModeProfile(0.10, (18, 48), 0.06, 24, (5, 800), Decimal("4.10"), Decimal("95")),
    "road": ModeProfile(0.60, (24, 120), 0.12, 48, (200, 18_000), Decimal("0.38"), Decimal("140")),
    "rail": ModeProfile(
        0.20, (72, 168), 0.18, 72, (5_000, 60_000), Decimal("0.22"), Decimal("210")
    ),
    "sea": ModeProfile(
        0.10, (336, 720), 0.24, 240, (2_000, 26_000), Decimal("0.09"), Decimal("480")
    ),
}


def build_world(seed: int, *, customer_count: int = 60, shipment_count: int = 4_000) -> World:
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    employees = _build_employees(rng, fake)
    customers = _build_customers(rng, fake, employees, customer_count)
    shipments = _build_shipments(rng, customers, shipment_count)
    return World(
        employees=tuple(employees),
        compensation=tuple(_build_compensation(rng, employees)),
        customers=tuple(customers),
        shipments=tuple(shipments),
        invoices=tuple(_build_invoices(rng, customers, shipments)),
    )


def _build_employees(rng: random.Random, fake: Faker) -> list[Employee]:
    employees: list[Employee] = []
    emails: set[str] = set()

    def add(department: str, title: str, manager: Employee | None, location: str) -> Employee:
        first, last = fake.first_name(), fake.last_name()
        local = re.sub(r"[^a-z.]", "", f"{first}.{last}".lower())
        email, n = f"{local}@{COMPANY_DOMAIN}", 2
        while email in emails:
            email, n = f"{local}{n}@{COMPANY_DOMAIN}", n + 1
        emails.add(email)
        senior = manager is None or manager.manager_id is None
        tenure_days = rng.randint(730, 4_000) if senior else rng.randint(30, 3_000)
        employee = Employee(
            employee_id=f"EMP-{len(employees) + 1:04d}",
            full_name=f"{first} {last}",
            email=email,
            department=department,
            title=title,
            manager_id=manager.employee_id if manager else None,
            location=location,
            hire_date=AS_OF.date() - timedelta(days=tenure_days),
        )
        employees.append(employee)
        return employee

    ceo = add("Executive", "Chief Executive Officer", None, HQ)
    cfo = add("Executive", "Chief Financial Officer", ceo, HQ)
    coo = add("Executive", "Chief Operating Officer", ceo, HQ)
    hubs = list(HUB_REGIONS)
    reports_to = {"Finance": cfo, "Operations": coo}
    for dept in _DEPARTMENTS:
        head = add(dept.name, dept.head_title, reports_to.get(dept.name, ceo), HQ)
        titles = [t for t in dept.titles if t != "Hub Supervisor"]
        for i in range(dept.headcount):
            if dept.name == "Operations" and i < len(hubs):
                add(dept.name, "Hub Supervisor", head, hubs[i])
                continue
            location = HQ if dept.name in ("Finance", "Human Resources") else rng.choice(hubs)
            add(dept.name, rng.choice(titles), head, location)
    return employees


def _build_compensation(rng: random.Random, employees: list[Employee]) -> list[Compensation]:
    rows = []
    for e in employees:
        band = _TITLE_BANDS[e.title]
        low, high, bonus_target = PAY_BANDS[band]
        rows.append(
            Compensation(
                employee_id=e.employee_id,
                base_salary_usd=Decimal(rng.randrange(low, high + 1, 500)).quantize(CENTS),
                bonus_pct=Decimal(bonus_target).quantize(CENTS),
                pay_band=band,
                effective_date=max(date(2026, 4, 1), e.hire_date),
            )
        )
    return rows


def _build_customers(
    rng: random.Random, fake: Faker, employees: list[Employee], count: int
) -> list[Customer]:
    managers = [e for e in employees if e.title.endswith("Account Executive")]
    gold, silver = round(count * 0.15), round(count * 0.35)
    tiers = ["gold"] * gold + ["silver"] * silver + ["bronze"] * (count - gold - silver)
    rng.shuffle(tiers)
    names: set[str] = set()
    customers: list[Customer] = []
    while len(customers) < count:
        industry = rng.choice(list(_INDUSTRY_SUFFIXES))
        name = f"{fake.last_name()} {rng.choice(_INDUSTRY_SUFFIXES[industry])}"
        if name in names:
            continue
        names.add(name)
        tier = tiers[len(customers)]
        customers.append(
            Customer(
                customer_id=f"CUST-{len(customers) + 1:03d}",
                name=name,
                industry=industry,
                tier=tier,
                sla_hours=TIERS[tier].first_response_hours,
                region=HUB_REGIONS[rng.choice(list(HUB_REGIONS))],
                account_manager_id=rng.choice(managers).employee_id,
                created_on=AS_OF.date() - timedelta(days=rng.randint(400, 2_500)),
            )
        )
    return customers


def _build_shipments(rng: random.Random, customers: list[Customer], count: int) -> list[Shipment]:
    volume = {"gold": 3, "silver": 2, "bronze": 1}
    weights = [volume[c.tier] for c in customers]
    # A few accounts with chronic lateness give tickets, emails and renewals something to be about.
    troubled = {c.customer_id for c in rng.sample(customers, 3)}
    modes = list(MODES)
    drafts = []
    for _ in range(count):
        customer = rng.choices(customers, weights)[0]
        mode = rng.choices(modes, [MODES[m].share for m in modes])[0]
        profile = MODES[mode]
        origin = rng.choice([h for h, r in HUB_REGIONS.items() if r == customer.region])
        destination = rng.choice([h for h in HUB_REGIONS if h != origin])
        booked = AS_OF - timedelta(minutes=rng.randint(30, 365 * 24 * 60))
        promised = booked + timedelta(hours=rng.randint(*profile.transit_hours))
        late_rate = profile.late_rate * (3 if customer.customer_id in troubled else 1)
        if rng.random() < late_rate:
            arrival = promised + timedelta(minutes=rng.randint(45, profile.max_delay_hours * 60))
        else:
            arrival = max(
                promised - timedelta(minutes=rng.randint(0, 360)), booked + timedelta(hours=1)
            )
        weight = Decimal(str(rng.uniform(*profile.weight_kg))).quantize(CENTS)

        delivered: datetime | None = None
        if rng.random() < 0.02:
            status = "cancelled"
        elif arrival <= AS_OF:
            status, delivered = "delivered", arrival
        elif booked + timedelta(hours=2) > AS_OF:
            status = "booked"
        else:
            status = "in_transit"
        drafts.append(
            (
                booked,
                customer.customer_id,
                origin,
                destination,
                mode,
                status,
                weight,
                promised,
                delivered,
            )
        )

    drafts.sort(key=lambda d: (d[0], d[1]))
    return [
        Shipment(
            f"SHP-{n:06d}", cid, origin, dest, mode, status, weight, booked, promised, delivered
        )
        for n, (booked, cid, origin, dest, mode, status, weight, promised, delivered) in enumerate(
            drafts, start=1
        )
    ]


def _build_invoices(
    rng: random.Random, customers: list[Customer], shipments: list[Shipment]
) -> list[Invoice]:
    tiers = {c.customer_id: c.tier for c in customers}
    today = AS_OF.date()
    invoices: list[Invoice] = []
    for s in shipments:
        if s.delivered_at is None:
            continue
        issued = s.delivered_at.date() + timedelta(days=1)
        if issued > today:
            continue
        terms = TIERS[tiers[s.customer_id]]
        profile = MODES[s.mode]
        amount = (profile.base_fee + s.weight_kg * profile.rate_per_kg) * terms.price_multiplier
        due = issued + timedelta(days=terms.payment_terms_days)
        paid_on: date | None = None
        if s.is_late and terms.late_credit_pct and rng.random() < 0.3:
            status = "disputed"
        else:
            candidate = issued + timedelta(days=rng.randint(3, terms.payment_terms_days + 25))
            if rng.random() < 0.92 and candidate <= today:
                status, paid_on = "paid", candidate
            else:
                status = "overdue" if due < today else "open"
        invoices.append(
            Invoice(
                invoice_id=f"INV-{len(invoices) + 1:06d}",
                customer_id=s.customer_id,
                shipment_id=s.shipment_id,
                amount_usd=amount.quantize(CENTS),
                issued_on=issued,
                due_on=due,
                paid_on=paid_on,
                status=status,
            )
        )
    return invoices
