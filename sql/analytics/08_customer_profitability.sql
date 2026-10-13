-- Q: Which customers are profitable once we count the support time they use?
--    Revenue of the last 6 closed months vs internal cost of agent time.
-- Techniques: pre-aggregate each side before joining (no fan-out), NTILE.

SET search_path TO ops;

WITH period AS (
    SELECT date_trunc('month', current_date) - interval '6 months' AS from_m,
           date_trunc('month', current_date)                      AS to_m
),
revenue AS (
    SELECT i.customer_id, sum(i.total) AS revenue
    FROM invoices i
    CROSS JOIN period p
    WHERE i.period >= p.from_m AND i.period < p.to_m
    GROUP BY i.customer_id
),
cost AS (
    SELECT t.customer_id,
           sum(te.minutes) / 60.0                 AS hours,
           sum(te.minutes / 60.0 * a.hourly_cost) AS cost
    FROM time_entries te
    JOIN tickets t USING (ticket_id)
    JOIN agents a ON a.agent_id = te.agent_id
    CROSS JOIN period p
    WHERE te.logged_at >= p.from_m AND te.logged_at < p.to_m
    GROUP BY t.customer_id
)
SELECT
    cu.name,
    cu.size_segment,
    round(r.revenue)                                                       AS revenue_eur,
    round(coalesce(c.cost, 0))                                             AS support_cost_eur,
    round(coalesce(c.hours, 0), 1)                                         AS support_hours,
    round(100 * (r.revenue - coalesce(c.cost, 0)) / r.revenue, 1)          AS margin_pct,
    ntile(4) OVER (ORDER BY (r.revenue - coalesce(c.cost, 0)) / r.revenue) AS margin_quartile
FROM revenue r
JOIN customers cu USING (customer_id)
LEFT JOIN cost c USING (customer_id)
ORDER BY margin_pct
LIMIT 20;
