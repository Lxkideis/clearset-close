-- Each source is aggregated at payout grain BEFORE joining: no join fan-out.
WITH m AS (
 SELECT payout_id,count(*) n,count(DISTINCT currency) currencies,min(currency) currency,
 sum(net)::bigint net,bool_and(gross-fee=net) arithmetic_valid
 FROM member_all WHERE effective_at<%(end)s GROUP BY payout_id
), w AS (
 SELECT payout_id,count(*) n,count(DISTINCT currency) currencies,min(currency) currency,
 sum(gross)::bigint gross,sum(fee)::bigint fee,sum(net)::bigint net,
 bool_and(gross-fee=net) arithmetic_valid,array_agg(id) record_ids
 FROM movement_all WHERE effective_at<%(end)s GROUP BY payout_id
), b AS (
 SELECT payout_reference,count(*) n,min(currency) currency,sum(amount)::bigint amount,array_agg(id) record_ids
 FROM bank_all WHERE effective_at<%(end)s GROUP BY payout_reference
)
SELECT p.business_key entity_key,p.currency,p.amount payout_amount_minor,p.automatic,p.payout_status,p.arrival_date,
 coalesce(m.n,0) membership_count,m.net membership_net_minor,m.currencies membership_currencies,m.currency membership_currency,
 coalesce(w.n,0) movement_count,w.gross movement_gross_minor,w.fee movement_fee_minor,w.net movement_net_minor,
 w.currencies movement_currencies,w.currency movement_currency,w.arithmetic_valid movement_arithmetic_valid,
 coalesce(b.n,0) bank_count,b.currency bank_currency,b.amount bank_amount_minor,
 p.id payout_record_id,w.record_ids movement_record_ids,b.record_ids bank_record_ids,
 CASE WHEN m.currencies=1 AND m.currency=p.currency THEN m.net-p.amount END batch_delta_minor,
 CASE WHEN b.n=1 AND b.currency=p.currency THEN b.amount-p.amount END bank_delta_minor
FROM payout_all p LEFT JOIN m ON m.payout_id=p.business_key
LEFT JOIN w ON w.payout_id=p.business_key LEFT JOIN b ON b.payout_reference=p.business_key
WHERE p.effective_at<%(end)s
