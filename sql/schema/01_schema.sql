-- =============================================================================
-- Agentic Data Assistant — demo schema
-- Domain: an IT services company (managed services + helpdesk + projects)
--
-- COMMENT ON statements are part of the design: the agent reads them through
-- the describe_table tool, so they are the "documentation" the LLM relies on.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS ops;
SET search_path TO ops, public;

-- ── Customers ───────────────────────────────────────────────────────────────
CREATE TABLE customers (
    customer_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          text        NOT NULL UNIQUE,
    industry      text        NOT NULL,
    city          text        NOT NULL,
    size_segment  text        NOT NULL CHECK (size_segment IN ('small', 'mid', 'enterprise')),
    created_at    date        NOT NULL
);
COMMENT ON TABLE  customers              IS 'Companies that buy services. One row per customer.';
COMMENT ON COLUMN customers.size_segment IS 'small (<20 employees), mid (20-250), enterprise (>250)';
COMMENT ON COLUMN customers.created_at   IS 'Date the customer signed their first contract';

-- ── Contracts (recurring revenue) ───────────────────────────────────────────
CREATE TABLE contracts (
    contract_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     integer     NOT NULL REFERENCES customers,
    plan            text        NOT NULL CHECK (plan IN ('basic', 'pro', 'premium')),
    monthly_fee     numeric(10,2) NOT NULL CHECK (monthly_fee > 0),
    included_hours  integer     NOT NULL CHECK (included_hours >= 0),
    sla_hours       integer     NOT NULL CHECK (sla_hours > 0),
    start_date      date        NOT NULL,
    end_date        date,
    CHECK (end_date IS NULL OR end_date > start_date)
);
COMMENT ON TABLE  contracts                IS 'Managed-services contracts. A customer can have several over time (upgrades/downgrades). Active contract: end_date IS NULL or end_date > current_date.';
COMMENT ON COLUMN contracts.monthly_fee    IS 'Recurring monthly fee in EUR (MRR contribution while active)';
COMMENT ON COLUMN contracts.included_hours IS 'Support hours per month included in the fee; extra billable time is invoiced separately';
COMMENT ON COLUMN contracts.sla_hours      IS 'Contractual first-response time in hours for priority 3-high and 4-critical tickets';
COMMENT ON COLUMN contracts.end_date       IS 'NULL = active. A customer with no active contract has churned.';

-- ── Agents (support staff) ──────────────────────────────────────────────────
CREATE TABLE agents (
    agent_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name   text NOT NULL,
    team        text NOT NULL CHECK (team IN ('Field Support', 'Service Desk', 'Web Development', 'Marketing', 'Sales', 'Projects')),
    hourly_cost numeric(6,2) NOT NULL,
    hired_at    date NOT NULL
);
COMMENT ON TABLE  agents             IS 'Employees who work on tickets';
COMMENT ON COLUMN agents.hourly_cost IS 'Internal cost per hour in EUR (salary + overhead), used for margin analysis';

-- ── Tickets ─────────────────────────────────────────────────────────────────
CREATE TABLE tickets (
    ticket_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id        integer     NOT NULL REFERENCES customers,
    assigned_agent_id  integer     REFERENCES agents,
    team               text        NOT NULL,
    ticket_type        text        NOT NULL CHECK (ticket_type IN ('Incident', 'Request', 'Question', 'Recurring')),
    priority           smallint    NOT NULL CHECK (priority BETWEEN 1 AND 4),
    channel            text        NOT NULL CHECK (channel IN ('email', 'phone', 'portal', 'voice_agent')),
    subject            text        NOT NULL,
    status             text        NOT NULL CHECK (status IN ('open', 'pending', 'resolved', 'closed')),
    created_at         timestamptz NOT NULL,
    first_response_at  timestamptz,
    resolved_at        timestamptz,
    csat               smallint    CHECK (csat BETWEEN 1 AND 5),
    CHECK (first_response_at IS NULL OR first_response_at >= created_at),
    CHECK (resolved_at IS NULL OR resolved_at >= created_at)
);
COMMENT ON TABLE  tickets            IS 'Helpdesk tickets. One row per ticket.';
COMMENT ON COLUMN tickets.team       IS 'Final team that handled the ticket (after any human correction of the AI triage)';
COMMENT ON COLUMN tickets.priority   IS '1 low, 2 normal, 3 high, 4 critical';
COMMENT ON COLUMN tickets.channel    IS 'voice_agent = created by the AI phone agent';
COMMENT ON COLUMN tickets.status     IS 'open/pending = backlog; resolved/closed = done';
COMMENT ON COLUMN tickets.csat       IS 'Customer satisfaction survey 1-5, NULL if not answered';

CREATE INDEX tickets_customer_created_idx ON tickets (customer_id, created_at);
CREATE INDEX tickets_created_idx          ON tickets (created_at);
CREATE INDEX tickets_open_idx             ON tickets (status) WHERE status IN ('open', 'pending');

-- ── AI triage log ───────────────────────────────────────────────────────────
CREATE TABLE ai_triage (
    ticket_id           integer PRIMARY KEY REFERENCES tickets,
    model               text        NOT NULL,
    predicted_team      text        NOT NULL,
    predicted_priority  smallint    NOT NULL CHECK (predicted_priority BETWEEN 1 AND 4),
    confidence          smallint    NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    input_tokens        integer     NOT NULL,
    output_tokens       integer     NOT NULL,
    cost_usd            numeric(10,6) NOT NULL,
    latency_ms          integer     NOT NULL,
    created_at          timestamptz NOT NULL
);
COMMENT ON TABLE  ai_triage                IS 'LLM classification of each ticket at creation time. Compare predicted_team with tickets.team to measure accuracy.';
COMMENT ON COLUMN ai_triage.confidence     IS 'Model self-reported confidence 0-100';
COMMENT ON COLUMN ai_triage.cost_usd       IS 'Cost of the LLM call in USD';

-- ── Time entries ────────────────────────────────────────────────────────────
CREATE TABLE time_entries (
    entry_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id   integer     NOT NULL REFERENCES tickets,
    agent_id    integer     NOT NULL REFERENCES agents,
    minutes     integer     NOT NULL CHECK (minutes > 0),
    billable    boolean     NOT NULL,
    logged_at   timestamptz NOT NULL,
    invoice_id  integer
);
COMMENT ON TABLE  time_entries            IS 'Time logged by agents on tickets';
COMMENT ON COLUMN time_entries.billable   IS 'true = billable beyond the contract included hours';
COMMENT ON COLUMN time_entries.invoice_id IS 'Invoice that billed this entry. Each month is invoiced at the start of the next month, so billable entries of the CURRENT month are always NULL (pending, not a problem). Unbilled work / revenue leakage = billable AND invoice_id IS NULL AND logged_at < date_trunc(''month'', current_date)';

CREATE INDEX time_entries_ticket_idx ON time_entries (ticket_id);
CREATE INDEX time_entries_agent_idx  ON time_entries (agent_id, logged_at);

-- ── Invoices ────────────────────────────────────────────────────────────────
CREATE TABLE invoices (
    invoice_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  integer       NOT NULL REFERENCES customers,
    period       date          NOT NULL CHECK (period = date_trunc('month', period)::date),
    issued_at    date          NOT NULL,
    due_at       date          NOT NULL,
    subscription_amount numeric(12,2) NOT NULL DEFAULT 0,
    extra_hours_amount  numeric(12,2) NOT NULL DEFAULT 0,
    total        numeric(12,2) GENERATED ALWAYS AS (subscription_amount + extra_hours_amount) STORED,
    status       text          NOT NULL CHECK (status IN ('issued', 'paid', 'overdue')),
    paid_at      date,
    UNIQUE (customer_id, period)
);
COMMENT ON TABLE  invoices         IS 'Monthly invoices per customer (EUR, VAT excluded)';
COMMENT ON COLUMN invoices.period  IS 'First day of the billed month';
COMMENT ON COLUMN invoices.status  IS 'issued = not yet due, paid, overdue = past due_at and unpaid';

ALTER TABLE time_entries
    ADD CONSTRAINT time_entries_invoice_fk FOREIGN KEY (invoice_id) REFERENCES invoices;
