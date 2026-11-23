-- Charge-only weighted fee rates. Refund/dispute flows never enter the denominator.
WITH l AS(SELECT *,count(*) OVER(PARTITION BY source_id,category) key_count FROM ledger_all
 WHERE effective_at>=%(start)s AND effective_at<%(end)s),
 a AS(SELECT *,count(*) OVER(PARTITION BY source_id,category) key_count FROM activity_all
 WHERE effective_at>=%(start)s AND effective_at<%(end)s)
SELECT l.partner,l.currency,count(*) charges,sum(l.gross)::bigint charge_gross_minor,
 sum(a.fee)::bigint observed_fees_minor,sum(l.fee)::bigint expected_fees_minor,
 sum(a.fee-l.fee)::bigint variance_minor,
 round(10000.0*sum(a.fee)/nullif(sum(l.gross),0),2) observed_fee_bps
FROM l JOIN a USING(source_id,category,currency)
WHERE l.category='charge' AND l.gross>0 AND a.gross-a.fee=a.net AND l.gross=a.gross
AND l.effective_at>=%(start)s AND l.effective_at<%(end)s AND a.effective_at>=%(start)s AND a.effective_at<%(end)s
AND l.key_count=1 AND a.key_count=1
GROUP BY l.partner,l.currency ORDER BY l.currency,l.partner
