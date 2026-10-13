-- Q: How good is the LLM ticket triage, and from which confidence can we
--    auto-route without a human? Compare models on accuracy, cost and latency.
-- Techniques: banding, cumulative window frames, ROLLUP.

SET search_path TO ops;

-- 1) Accuracy per confidence band, and what happens if we auto-route
--    every ticket at or above that band (coverage vs accuracy trade-off)
WITH bands AS (
    SELECT (a.confidence / 10) * 10                 AS band_from,
           count(*)                                 AS tickets,
           count(*) FILTER (WHERE a.predicted_team = t.team) AS correct
    FROM ai_triage a
    JOIN tickets t USING (ticket_id)
    GROUP BY 1
)
SELECT
    band_from || '-' || (band_from + 9)                           AS confidence_band,
    tickets,
    round(100.0 * correct / tickets, 1)                           AS accuracy_pct,
    sum(tickets) OVER w                                           AS auto_routed,
    round(100.0 * sum(tickets) OVER w / sum(tickets) OVER (), 1)  AS coverage_pct,
    round(100.0 * sum(correct) OVER w / sum(tickets) OVER w, 1)   AS accuracy_if_auto_pct
FROM bands
WINDOW w AS (ORDER BY band_from DESC ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
ORDER BY band_from DESC;

-- 2) Model comparison (ROLLUP adds the overall row)
SELECT
    coalesce(a.model, 'ALL')                                         AS model,
    count(*)                                                         AS tickets,
    round(100.0 * avg((a.predicted_team = t.team)::int), 1)          AS team_accuracy_pct,
    round(100.0 * avg((a.predicted_priority = t.priority)::int), 1)  AS priority_accuracy_pct,
    round(1000 * avg(a.cost_usd), 3)                                 AS cost_per_1k_tickets_usd,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY a.latency_ms)       AS p95_latency_ms
FROM ai_triage a
JOIN tickets t USING (ticket_id)
GROUP BY ROLLUP (a.model)
ORDER BY a.model NULLS LAST;
