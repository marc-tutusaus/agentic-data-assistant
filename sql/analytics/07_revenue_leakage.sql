-- Q: How much billable work was never invoiced, and for which customers?
-- Techniques: anti-join (invoice_id IS NULL) on closed periods, running total window.

SET search_path TO ops;

WITH leaked AS (
    SELECT t.customer_id,
           count(*)                AS entries,
           sum(te.minutes) / 60.0  AS hours,
           min(te.logged_at)::date AS oldest
    FROM time_entries te
    JOIN tickets t USING (ticket_id)
    WHERE te.billable
      AND te.invoice_id IS NULL
      AND te.logged_at < date_trunc('month', current_date)  -- the current month is not billed yet
    GROUP BY t.customer_id
)
SELECT
    cu.name,
    l.entries,
    round(l.hours, 1)                                        AS unbilled_hours,
    round(l.hours * 65, 2)                                   AS unbilled_eur,  -- 65 EUR/h billing rate
    l.oldest,
    round(sum(l.hours * 65) OVER (ORDER BY l.hours DESC), 2) AS running_total_eur
FROM leaked l
JOIN customers cu USING (customer_id)
ORDER BY l.hours DESC
LIMIT 20;
