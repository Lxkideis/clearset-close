-- Rank BEFORE removing retractions: otherwise an old row would reappear.
CREATE TEMP TABLE picked ON COMMIT DROP AS
SELECT * FROM (
 SELECT o.*,row_number() OVER(PARTITION BY source,business_key ORDER BY revision DESC) AS position
 FROM observation o WHERE observed_at<=%(cutoff)s
) ranked WHERE position=1 AND NOT retracted;
CREATE INDEX picked_source_idx ON picked(source);
CREATE TEMP VIEW ledger_all AS SELECT id,business_key,payload->>'source_id' source_id,payload->>'category' category,
 payload->>'currency' currency,(payload->>'gross_minor')::bigint gross,(payload->>'fee_minor')::bigint fee,
 (payload->>'net_minor')::bigint net,payload->>'partner' partner,effective_at FROM picked WHERE source='ledger';
CREATE TEMP VIEW activity_all AS SELECT id,business_key,payload->>'source_id' source_id,payload->>'category' category,
 payload->>'currency' currency,(payload->>'gross_minor')::bigint gross,(payload->>'fee_minor')::bigint fee,
 (payload->>'net_minor')::bigint net,(payload->>'available_on')::timestamptz available_on,effective_at FROM picked WHERE source='activity';
CREATE TEMP VIEW member_all AS SELECT id,business_key,payload->>'transaction_id' transaction_id,payload->>'payout_id' payout_id,
 payload->>'currency' currency,(payload->>'gross_minor')::bigint gross,(payload->>'fee_minor')::bigint fee,
 (payload->>'net_minor')::bigint net,effective_at FROM picked WHERE source='membership';
CREATE TEMP VIEW payout_all AS SELECT id,business_key,payload->>'currency' currency,(payload->>'amount_minor')::bigint amount,
 (payload->>'automatic')::boolean automatic,payload->>'status' payout_status,(payload->>'arrival_date')::date arrival_date,
 effective_at FROM picked WHERE source='payouts';
CREATE TEMP VIEW movement_all AS SELECT id,business_key,payload->>'payout_id' payout_id,payload->>'category' category,
 payload->>'currency' currency,(payload->>'gross_minor')::bigint gross,(payload->>'fee_minor')::bigint fee,
 (payload->>'net_minor')::bigint net,effective_at FROM picked WHERE source='movements';
CREATE TEMP VIEW bank_all AS SELECT id,business_key,payload->>'payout_reference' payout_reference,payload->>'currency' currency,
 (payload->>'amount_minor')::bigint amount,effective_at FROM picked WHERE source='bank';
CREATE TEMP VIEW summary_all AS SELECT id,business_key,payload->>'category' category,payload->>'currency' currency,
 (payload->>'net_minor')::bigint net,effective_at FROM picked WHERE source='summary';
