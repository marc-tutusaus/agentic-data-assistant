-- Q: Is the AI voice agent taking load off the phone line?
--    Monthly channel mix with month-over-month change.
-- Techniques: conditional-aggregation pivot, LAG, share of total.

SET search_path TO ops;

WITH monthly AS (
    SELECT
        date_trunc('month', created_at)::date           AS month,
        count(*)                                        AS total,
        count(*) FILTER (WHERE channel = 'voice_agent') AS voice_agent,
        count(*) FILTER (WHERE channel = 'phone')       AS phone,
        count(*) FILTER (WHERE channel = 'email')       AS email,
        count(*) FILTER (WHERE channel = 'portal')      AS portal
    FROM tickets
    WHERE created_at < date_trunc('month', current_date)
    GROUP BY 1
)
SELECT
    month, total, voice_agent, phone, email, portal,
    round(100.0 * voice_agent / total, 1)                AS voice_agent_share_pct,
    round(100.0 * phone / total, 1)                      AS phone_share_pct,
    voice_agent - lag(voice_agent) OVER (ORDER BY month) AS voice_agent_mom
FROM monthly
ORDER BY month;
