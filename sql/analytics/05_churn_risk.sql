-- Q: Which ACTIVE customers look like the ones that churned?
--    Signal: ticket volume up and CSAT down (last 90 days vs the previous 90).
-- Techniques: FILTER aggregates over two time windows, NULLIF, a transparent risk score.

SET search_path TO ops;

WITH activity AS (
    SELECT
        customer_id,
        count(*)  FILTER (WHERE created_at >= current_date - 90)                                    AS tickets_recent,
        count(*)  FILTER (WHERE created_at <  current_date - 90 AND created_at >= current_date - 180) AS tickets_prev,
        avg(csat) FILTER (WHERE created_at >= current_date - 90)                                    AS csat_recent,
        avg(csat) FILTER (WHERE created_at <  current_date - 90 AND created_at >= current_date - 180) AS csat_prev
    FROM tickets
    GROUP BY customer_id
)
SELECT
    cu.name,
    cu.size_segment,
    co.plan,
    co.monthly_fee,
    a.tickets_prev,
    a.tickets_recent,
    round(100.0 * (a.tickets_recent - a.tickets_prev) / NULLIF(a.tickets_prev, 0)) AS ticket_change_pct,
    round(a.csat_prev, 2)                                                          AS csat_prev,
    round(a.csat_recent, 2)                                                        AS csat_recent,
    round(  greatest(0, (a.tickets_recent - a.tickets_prev)::numeric / NULLIF(a.tickets_prev, 0)) * 50
          + greatest(0, coalesce(a.csat_prev - a.csat_recent, 0)) * 40
          + CASE WHEN a.csat_recent < 3.5 THEN 20 ELSE 0 END, 1)                   AS risk_score
FROM activity a
JOIN customers cu USING (customer_id)
JOIN contracts co ON co.customer_id = a.customer_id AND co.end_date IS NULL
WHERE a.tickets_prev >= 3
ORDER BY risk_score DESC NULLS LAST
LIMIT 15;
