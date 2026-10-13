-- =============================================================================
-- Synthetic demo data — 100% generated, no real companies or people.
-- Deterministic (setseed) and relative to current_date, so questions like
-- "last month" always have data. Pure SQL: generate_series + random().
--
-- Story baked into the data (so the analytics have something to find):
--   * AI ticket triage went live 12 months ago; the model was upgraded 4 months ago
--   * The AI voice agent went live 9 months ago and its share of tickets grows
--   * Some customers churn, and their ticket volume/CSAT degrade before leaving
--   * A slice of billable time is never invoiced (revenue leakage)
-- =============================================================================

SET search_path TO ops, public;
SELECT setseed(0.42);

-- helper: pick a random element from an array
CREATE FUNCTION pg_temp.pick(anyarray) RETURNS anyelement
LANGUAGE sql VOLATILE AS $$ SELECT $1[1 + floor(random() * array_length($1, 1))::int] $$;

-- ── Customers (120) ─────────────────────────────────────────────────────────
-- name suffix and industry are drawn together so names make sense ("Sol Dental" → Healthcare)
CREATE TEMP TABLE name_parts (suffix text, industry text);
INSERT INTO name_parts VALUES
  ('Logistics','Logistics'), ('Transport','Logistics'), ('Dental','Healthcare'), ('Clinic','Healthcare'),
  ('Pharma','Healthcare'), ('Legal','Legal'), ('Advocats','Legal'), ('Foods','Manufacturing'),
  ('Textiles','Manufacturing'), ('Motors','Manufacturing'), ('Print','Manufacturing'), ('Retail','Retail'),
  ('Store','Retail'), ('Wines','Retail'), ('Hotels','Hospitality'), ('Travel','Hospitality'),
  ('Restaurants','Hospitality'), ('Academy','Education'), ('School','Education'), ('Homes','Real Estate'),
  ('Properties','Real Estate'), ('Consulting','Professional Services'), ('Partners','Professional Services'),
  ('Studio','Professional Services'), ('Labs','Professional Services');

INSERT INTO customers (name, industry, city, size_segment, created_at)
SELECT name, industry, city, size_segment, created_at
FROM (
    SELECT DISTINCT ON (name) name, industry, city, size_segment, created_at, random() AS shuffle
    FROM (
        SELECT
            pg_temp.pick(ARRAY['Nova','Blau','Delta','Atlas','Vertex','Lumen','Orbit','Cima','Prisma','Nexo','Alba',
                               'Faro','Roca','Mar','Vela','Sol','Terra','Arco','Pixel','Quantum']) || ' ' || np.suffix AS name,
            np.industry,
            pg_temp.pick(ARRAY['Barcelona','Barcelona','Barcelona','Vilanova i la Geltrú','Sitges','Sabadell',
                               'Terrassa','Girona','Tarragona','Lleida','Madrid','Valencia'])            AS city,
            CASE WHEN s.r < 0.55 THEN 'small' WHEN s.r < 0.90 THEN 'mid' ELSE 'enterprise' END      AS size_segment,
            (current_date - (60 + floor(random() * 1400))::int)                                  AS created_at
        FROM (SELECT g, random() AS r, random() AS r2 FROM generate_series(1, 400) g) s
        CROSS JOIN LATERAL (SELECT * FROM name_parts ORDER BY random() + 0 * s.g LIMIT 1) np
    ) c
    ORDER BY name, created_at
) uniq
ORDER BY shuffle
LIMIT 120;

-- ── Contracts ───────────────────────────────────────────────────────────────
-- Every customer starts on a plan; ~25% upgrade later; ~14% churn in the last 18 months.
CREATE TEMP TABLE plan_catalog (plan text, monthly_fee numeric, included_hours int, sla_hours int);
INSERT INTO plan_catalog VALUES ('basic', 290, 4, 24), ('pro', 690, 12, 8), ('premium', 1490, 30, 4);

CREATE TEMP TABLE customer_fate AS
SELECT customer_id, size_segment, created_at,
       random() < 0.25 AS upgrades,
       random() < 0.14 AS churns,
       random()        AS r
FROM customers;

INSERT INTO contracts (customer_id, plan, monthly_fee, included_hours, sla_hours, start_date, end_date)
SELECT f.customer_id, p.plan, p.monthly_fee * (CASE f.size_segment WHEN 'enterprise' THEN 1.6 ELSE 1 END),
       p.included_hours, p.sla_hours, f.created_at,
       CASE
         WHEN f.upgrades THEN f.created_at + ((current_date - f.created_at) * (0.3 + 0.4 * f.r))::int
         WHEN f.churns   THEN greatest(f.created_at + 45, current_date - (20 + floor(f.r * 520))::int)
       END
FROM customer_fate f
JOIN plan_catalog p ON p.plan = CASE
       WHEN f.size_segment = 'enterprise' THEN 'premium'
       WHEN f.size_segment = 'mid'        THEN (CASE WHEN f.r < 0.6 THEN 'pro' ELSE 'basic' END)
       ELSE 'basic' END;

-- upgrade contracts start when the first one ends (churn may still happen afterwards)
INSERT INTO contracts (customer_id, plan, monthly_fee, included_hours, sla_hours, start_date, end_date)
SELECT c.customer_id, p.plan, p.monthly_fee * (CASE f.size_segment WHEN 'enterprise' THEN 1.6 ELSE 1 END),
       p.included_hours, p.sla_hours, c.end_date,
       CASE WHEN f.churns THEN greatest(c.end_date + 45, current_date - (20 + floor(random() * 200))::int) END
FROM contracts c
JOIN customer_fate f USING (customer_id)
JOIN plan_catalog p ON p.plan = CASE c.plan WHEN 'basic' THEN 'pro' ELSE 'premium' END
WHERE f.upgrades AND c.end_date IS NOT NULL;

-- churned customers that ended up with an end_date in the future are simply still active
UPDATE contracts SET end_date = NULL WHERE end_date > current_date;

-- ── Agents (18) ─────────────────────────────────────────────────────────────
INSERT INTO agents (full_name, team, hourly_cost, hired_at)
SELECT first || ' ' || last, team, cost, current_date - (200 + floor(random() * 2000))::int
FROM (VALUES
  ('Laia','Puig','Field Support',32), ('Jordi','Ferrer','Field Support',30), ('Pau','Soler','Field Support',29),
  ('Marta','Vidal','Field Support',33),
  ('Anna','Roca','Service Desk',27), ('David','Serra','Service Desk',26), ('Núria','Pons','Service Desk',28),
  ('Carlos','Martín','Service Desk',25), ('Elena','Ruiz','Service Desk',27),
  ('Oriol','Costa','Web Development',38), ('Sara','Gil','Web Development',40), ('Marc','Riera','Web Development',36),
  ('Júlia','Mas','Marketing',31), ('Pol','Camps','Marketing',30),
  ('Irene','Font','Sales',34), ('Xavier','Bosch','Sales',35),
  ('Clara','Prat','Projects',41), ('Albert','Rius','Projects',42)
) AS a(first, last, team, cost);

-- ── Tickets (~9k over 18 months) ────────────────────────────────────────────
CREATE TEMP TABLE subjects (team text, subject text);
INSERT INTO subjects VALUES
  ('Field Support','Server not responding at the office'), ('Field Support','Network switch failure'),
  ('Field Support','Printer offline on second floor'), ('Field Support','On-site visit to install new workstations'),
  ('Field Support','Wi-Fi drops in meeting room'), ('Field Support','NAS backup failed'),
  ('Service Desk','Password reset for new employee'), ('Service Desk','Outlook not syncing mailbox'),
  ('Service Desk','VPN connection fails from home'), ('Service Desk','Request Microsoft 365 license'),
  ('Service Desk','Shared folder permissions'), ('Service Desk','Teams calls dropping'),
  ('Web Development','Online store checkout error'), ('Web Development','Order confirmation emails not sent'),
  ('Web Development','Update WordPress plugins'), ('Web Development','SSL certificate expired'),
  ('Web Development','New landing page for campaign'), ('Web Development','Website very slow'),
  ('Marketing','Pages excluded in Search Console'), ('Marketing','Monthly SEO report'),
  ('Marketing','Set up Google Ads conversion tracking'), ('Marketing','Newsletter template change'),
  ('Sales','Quote for additional support hours'), ('Sales','Contract renewal'),
  ('Sales','Upgrade plan request'),
  ('Projects','Kick-off for intranet project'), ('Projects','Migration planning to Microsoft 365'),
  ('Projects','Timeline review for ERP integration');

CREATE TEMP TABLE ticket_draft AS
WITH active_window AS (
    SELECT cu.customer_id, cu.size_segment,
           min(co.sla_hours)                                          AS sla_hours,
           greatest(min(co.start_date), current_date - 548)          AS from_d,
           CASE WHEN bool_or(co.end_date IS NULL) THEN current_date ELSE max(co.end_date) END AS to_d,
           CASE WHEN bool_or(co.end_date IS NULL) THEN NULL ELSE max(co.end_date) END       AS churn_d
    FROM customers cu JOIN contracts co USING (customer_id)
    GROUP BY cu.customer_id, cu.size_segment
),
volume AS (
    SELECT *, ((to_d - from_d) / 30.0 *
               CASE size_segment WHEN 'enterprise' THEN 14 WHEN 'mid' THEN 6 ELSE 2.5 END)::int AS n
    FROM active_window WHERE to_d > from_d
)
SELECT v.customer_id, v.churn_d, v.sla_hours,
       -- churning customers concentrate tickets in their final months
       v.from_d::timestamp + (CASE WHEN v.churn_d IS NOT NULL THEN sqrt(random()) ELSE random() END * (v.to_d - v.from_d)) * interval '1 day'
         + make_interval(hours => 8 + floor(random() * 10)::int, mins => floor(random() * 60)::int) AS created_at,
       random() AS r_team, random() AS r_prio, random() AS r_chan, random() AS r_resp, random() AS r_res, random() AS r_csat
FROM volume v, generate_series(1, v.n);

INSERT INTO tickets (customer_id, assigned_agent_id, team, ticket_type, priority, channel, subject, status,
                     created_at, first_response_at, resolved_at, csat)
SELECT
    d.customer_id, NULL, x.team, x.ticket_type, x.priority, x.channel,
    (SELECT s.subject FROM subjects s WHERE s.team = x.team ORDER BY random() LIMIT 1),
    CASE WHEN rs.resolved_at IS NULL THEN (CASE WHEN d.r_res < 0.5 THEN 'open' ELSE 'pending' END)
         WHEN rs.resolved_at < now() - interval '7 days' THEN 'closed' ELSE 'resolved' END,
    d.created_at,
    CASE WHEN fr.ts <= now() THEN fr.ts END,
    rs.resolved_at,
    CASE WHEN rs.resolved_at IS NOT NULL AND d.r_csat < 0.6 THEN
         greatest(1, least(5, round(4.6 - (extract(epoch FROM fr.ts - d.created_at) / 3600) / 12
                                     - CASE WHEN d.churn_d IS NOT NULL THEN 1.2 ELSE 0 END
                                     + (random() - 0.5) * 1.5)))::smallint
    END
FROM ticket_draft d
CROSS JOIN LATERAL (
    SELECT
      CASE WHEN d.r_team < 0.24 THEN 'Field Support' WHEN d.r_team < 0.56 THEN 'Service Desk'
           WHEN d.r_team < 0.80 THEN 'Web Development' WHEN d.r_team < 0.89 THEN 'Marketing'
           WHEN d.r_team < 0.95 THEN 'Sales' ELSE 'Projects' END                         AS team,
      (ARRAY['Incident','Incident','Incident','Request','Request','Question','Recurring'])[1 + floor(d.r_csat * 7)::int] AS ticket_type,
      (CASE WHEN d.r_prio < 0.15 THEN 1 WHEN d.r_prio < 0.70 THEN 2 WHEN d.r_prio < 0.94 THEN 3 ELSE 4 END)::smallint AS priority,
      -- the AI voice agent went live 270 days ago and its share grows from 10% to 40%
      CASE WHEN d.created_at > current_date - 270
                AND d.r_chan < 0.10 + 0.30 * (1 - extract(epoch FROM now() - d.created_at) / (270 * 86400))
             THEN 'voice_agent'
           WHEN d.r_chan < 0.55 THEN 'email' WHEN d.r_chan < 0.80 THEN 'portal' ELSE 'phone' END AS channel
) x
CROSS JOIN LATERAL (
    -- first response: faster for higher priority, with a long tail (=> some SLA breaches)
    SELECT d.created_at + make_interval(secs => (
             CASE x.priority WHEN 4 THEN 1.2 WHEN 3 THEN 2.5 WHEN 2 THEN 6 ELSE 14 END
             * CASE d.sla_hours WHEN 4 THEN 0.35 WHEN 8 THEN 0.6 ELSE 1 END   -- premium customers are served first
             * 3600 * (0.15 + power(d.r_resp, 3) * 6))) AS ts
) fr
CROSS JOIN LATERAL (
    SELECT CASE WHEN res_ts <= now() THEN res_ts END AS resolved_at
    FROM (SELECT CASE WHEN d.created_at < now() - interval '21 days' OR d.r_res < 0.45
                      THEN fr.ts + make_interval(secs => 3600 * (1 + power(d.r_res, 2) * 96)) END AS res_ts) q
) rs;

-- assign each ticket to an agent of its team
UPDATE tickets t SET assigned_agent_id = (
    SELECT a.agent_id FROM agents a
    WHERE a.team = t.team
    ORDER BY md5(a.agent_id::text || t.ticket_id::text) LIMIT 1);

-- ── AI triage (live for the last 12 months) ────────────────────────────────
INSERT INTO ai_triage (ticket_id, model, predicted_team, predicted_priority, confidence,
                       input_tokens, output_tokens, cost_usd, latency_ms, created_at)
SELECT t.ticket_id, m.model,
       CASE WHEN random() * 100 < c.confidence * m.quality THEN t.team
            ELSE pg_temp.pick(ARRAY['Field Support','Service Desk','Web Development','Marketing','Sales','Projects']) END,
       greatest(1, least(4, t.priority + CASE WHEN random() < 0.18 THEN pg_temp.pick(ARRAY[-1, 1]) ELSE 0 END))::smallint,
       c.confidence,
       tok.inp, tok.outp,
       round((tok.inp * m.in_price + tok.outp * m.out_price) / 1e6, 6),
       (m.base_latency + random() * 1800)::int,
       t.created_at + interval '3 seconds'
FROM tickets t
CROSS JOIN LATERAL (
    SELECT CASE WHEN t.created_at >= current_date - 120 THEN 'gpt-4.1-mini' ELSE 'gpt-4o-mini' END AS model
) mm
CROSS JOIN LATERAL (
    SELECT mm.model,
           CASE mm.model WHEN 'gpt-4.1-mini' THEN 1.06 ELSE 0.96 END AS quality,
           CASE mm.model WHEN 'gpt-4.1-mini' THEN 0.40 ELSE 0.15 END AS in_price,
           CASE mm.model WHEN 'gpt-4.1-mini' THEN 1.60 ELSE 0.60 END AS out_price,
           CASE mm.model WHEN 'gpt-4.1-mini' THEN 1100 ELSE 1400 END AS base_latency
) m
CROSS JOIN LATERAL (SELECT (45 + power(random(), 0.45) * 54 + 0 * t.ticket_id)::smallint AS confidence) c
CROSS JOIN LATERAL (SELECT (1650 + random() * 900 + 0 * t.ticket_id)::int AS inp, (180 + random() * 260)::int AS outp) tok
WHERE t.created_at >= current_date - 365;

-- ── Time entries ────────────────────────────────────────────────────────────
INSERT INTO time_entries (ticket_id, agent_id, minutes, billable, logged_at)
SELECT t.ticket_id, t.assigned_agent_id,
       (5 * ceil((10 + power(random(), 1.8) * CASE t.team WHEN 'Projects' THEN 300 WHEN 'Web Development' THEN 200 ELSE 110 END) / 5))::int,
       random() < CASE t.team WHEN 'Web Development' THEN 0.45 WHEN 'Projects' THEN 0.70 WHEN 'Field Support' THEN 0.35 ELSE 0.15 END,
       t.first_response_at + (t.resolved_at - t.first_response_at) * random()
FROM tickets t
CROSS JOIN LATERAL generate_series(1, 1 + floor(random() * CASE WHEN t.priority >= 3 THEN 4 ELSE 2 END)::int) g
WHERE t.resolved_at IS NOT NULL;

-- ~7% of billable time is never invoiced (revenue leakage the analytics should find)
CREATE TEMP TABLE leaked AS
SELECT entry_id FROM time_entries WHERE billable AND (abs(hashtext(entry_id::text)) % 100) < 7;

-- ── Invoices (monthly, up to last month) ────────────────────────────────────
CREATE TEMP TABLE billing_rate AS SELECT 65::numeric AS eur_per_hour;

INSERT INTO invoices (customer_id, period, issued_at, due_at, subscription_amount, extra_hours_amount, status, paid_at)
SELECT m.customer_id, m.period,
       (m.period + interval '1 month')::date + 2,
       (m.period + interval '1 month')::date + 32,
       m.fee,
       coalesce(round(x.minutes / 60.0 * (SELECT eur_per_hour FROM billing_rate), 2), 0),
       st.status,
       CASE WHEN st.status = 'paid' THEN (m.period + interval '1 month')::date + 2 + (5 + floor(random() * 40))::int END
FROM (
    SELECT co.customer_id, p::date AS period, max(co.monthly_fee) AS fee
    FROM contracts co
    CROSS JOIN LATERAL generate_series(date_trunc('month', greatest(co.start_date, current_date - 548)),
                                       date_trunc('month', coalesce(co.end_date, current_date)) - interval '1 month',
                                       interval '1 month') p
    WHERE p < date_trunc('month', current_date)
    GROUP BY co.customer_id, p
) m
LEFT JOIN LATERAL (
    SELECT sum(te.minutes) AS minutes
    FROM time_entries te JOIN tickets t USING (ticket_id)
    WHERE t.customer_id = m.customer_id AND te.billable
      AND te.logged_at >= m.period AND te.logged_at < m.period + interval '1 month'
      AND te.entry_id NOT IN (SELECT entry_id FROM leaked)
) x ON true
CROSS JOIN LATERAL (
    SELECT CASE
             WHEN (m.period + interval '1 month')::date + 32 > current_date THEN 'issued'
             WHEN m.period >= date_trunc('month', current_date) - interval '5 months' AND random() < 0.12 THEN 'overdue'
             WHEN random() < 0.008 THEN 'overdue'
             ELSE 'paid' END AS status
) st;

-- link billed time to its invoice (leaked entries stay with invoice_id NULL)
UPDATE time_entries te SET invoice_id = i.invoice_id
FROM tickets t, invoices i
WHERE te.ticket_id = t.ticket_id AND te.billable
  AND i.customer_id = t.customer_id
  AND i.period = date_trunc('month', te.logged_at)::date
  AND te.entry_id NOT IN (SELECT entry_id FROM leaked);

ANALYZE;
