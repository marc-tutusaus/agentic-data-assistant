-- Q: Who owes us money and for how long? (accounts receivable aging)
-- Techniques: FILTER buckets, ordered string_agg.

SET search_path TO ops;

SELECT
    cu.name,
    count(*)                                                              AS overdue_invoices,
    sum(i.total)                                                          AS outstanding_eur,
    sum(i.total) FILTER (WHERE current_date - i.due_at <= 30)             AS "1-30d",
    sum(i.total) FILTER (WHERE current_date - i.due_at BETWEEN 31 AND 90) AS "31-90d",
    sum(i.total) FILTER (WHERE current_date - i.due_at > 90)              AS "90d+",
    string_agg(to_char(i.period, 'YYYY-MM'), ', ' ORDER BY i.period)      AS periods
FROM invoices i
JOIN customers cu USING (customer_id)
WHERE i.status = 'overdue'
GROUP BY cu.name
ORDER BY outstanding_eur DESC;
