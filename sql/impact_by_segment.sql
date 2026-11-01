-- Impact of the proposed rates by driver age band and annual mileage band.
--
-- Runs against a table called "results", built by workbench.compare.segment_impact
-- from the per-policy comparison. One row per age band and mileage band
-- combination that has at least one policy, biggest total change first.
--
-- All rates behind these numbers are illustrative assumptions, not real
-- Hastings Direct pricing.

SELECT
    age_band,
    mileage_band,
    COUNT(*)                                               AS policies,
    SUM(CASE WHEN direction = 'increase' THEN 1 ELSE 0 END) AS increases,
    SUM(CASE WHEN direction = 'decrease' THEN 1 ELSE 0 END) AS decreases,
    ROUND(SUM(current_premium), 2)                         AS current_total,
    ROUND(SUM(proposed_premium), 2)                        AS proposed_total,
    ROUND(SUM(premium_change), 2)                          AS total_change,
    ROUND(AVG(pct_change), 4)                              AS avg_pct_change,
    MAX(pct_change)                                        AS max_pct_change
FROM results
GROUP BY age_band, mileage_band
-- age_band and mileage_band only break ties, so the order is always the same.
ORDER BY total_change DESC, age_band, mileage_band;
