-- Q: What is in the backlog right now and how old is it?
-- Techniques: age bucketing with FILTER aggregates.

SET search_path TO ops;

SELECT
    team,
    count(*)                                                                      AS open_tickets,
    count(*) FILTER (WHERE age <  interval '2 days')                              AS "<2d",
    count(*) FILTER (WHERE age >= interval '2 days' AND age < interval '7 days')  AS "2-7d",
    count(*) FILTER (WHERE age >= interval '7 days' AND age < interval '14 days') AS "7-14d",
    count(*) FILTER (WHERE age >= interval '14 days')                             AS "14d+",
    count(*) FILTER (WHERE priority >= 3)                                         AS high_or_critical,
    count(*) FILTER (WHERE first_response_at IS NULL)                             AS never_answered
FROM (
    SELECT team, priority, first_response_at, now() - created_at AS age
    FROM tickets
    WHERE status IN ('open', 'pending')
) backlog
GROUP BY team
ORDER BY open_tickets DESC;
