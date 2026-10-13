-- Q: Are we meeting the contractual first-response SLA for high/critical tickets?
--    Monthly compliance per plan, last 12 months.
-- Techniques: range join to the contract active at ticket creation, FILTER aggregates.

SET search_path TO ops;

WITH ticket_sla AS (
    SELECT
        date_trunc('month', t.created_at)::date AS month,
        co.plan,
        t.first_response_at - t.created_at      AS response_time,
        make_interval(hours => co.sla_hours)    AS sla
    FROM tickets t
    JOIN contracts co
      ON co.customer_id = t.customer_id
     AND t.created_at::date >= co.start_date
     AND (co.end_date IS NULL OR t.created_at::date < co.end_date)
    WHERE t.priority >= 3
      AND t.first_response_at IS NOT NULL
      AND t.created_at >= date_trunc('month', current_date) - interval '12 months'
)
SELECT
    month,
    plan,
    count(*)                                                                  AS tickets,
    count(*) FILTER (WHERE response_time > sla)                               AS breaches,
    round(100.0 * count(*) FILTER (WHERE response_time <= sla) / count(*), 1) AS compliance_pct
FROM ticket_sla
GROUP BY month, plan
ORDER BY month, plan;
