-- Q: How is recurring revenue evolving? New vs churned customers per month.
-- Techniques: generate_series calendar, range joins, LAG window function.

SET search_path TO ops;

WITH months AS (
    SELECT generate_series(date_trunc('month', current_date) - interval '17 months',
                           date_trunc('month', current_date),
                           interval '1 month')::date AS month
),
customer_span AS (   -- first contract day and churn day (NULL while any contract is active)
    SELECT customer_id,
           min(start_date) AS first_day,
           CASE WHEN bool_or(end_date IS NULL) THEN NULL ELSE max(end_date) END AS churn_day
    FROM contracts
    GROUP BY customer_id
),
mrr AS (
    SELECT m.month,
           sum(co.monthly_fee)            AS mrr,
           count(DISTINCT co.customer_id) AS active_customers
    FROM months m
    JOIN contracts co
      ON co.start_date <= m.month
     AND (co.end_date IS NULL OR co.end_date > m.month)
    GROUP BY m.month
)
SELECT
    mrr.month,
    mrr.active_customers,
    mrr.mrr,
    round(100.0 * (mrr.mrr - lag(mrr.mrr) OVER w) / lag(mrr.mrr) OVER w, 1) AS mrr_growth_pct,
    (SELECT count(*) FROM customer_span s WHERE date_trunc('month', s.first_day) = mrr.month) AS new_customers,
    (SELECT count(*) FROM customer_span s WHERE date_trunc('month', s.churn_day) = mrr.month) AS churned_customers
FROM mrr
WINDOW w AS (ORDER BY mrr.month)
ORDER BY mrr.month;
