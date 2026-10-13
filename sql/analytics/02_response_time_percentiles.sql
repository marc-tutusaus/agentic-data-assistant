-- Q: How fast do we answer, per team? Averages hide the tail, so use percentiles.
-- Techniques: ordered-set aggregates (percentile_cont), interval maths.

SET search_path TO ops;

SELECT
    team,
    count(*)                                                                 AS tickets,
    round((percentile_cont(0.5)  WITHIN GROUP (ORDER BY hours))::numeric, 1) AS p50_hours,
    round((percentile_cont(0.9)  WITHIN GROUP (ORDER BY hours))::numeric, 1) AS p90_hours,
    round((percentile_cont(0.99) WITHIN GROUP (ORDER BY hours))::numeric, 1) AS p99_hours
FROM (
    SELECT team, extract(epoch FROM first_response_at - created_at) / 3600 AS hours
    FROM tickets
    WHERE first_response_at IS NOT NULL
      AND created_at >= current_date - 90
) r
GROUP BY team
ORDER BY p90_hours DESC;
