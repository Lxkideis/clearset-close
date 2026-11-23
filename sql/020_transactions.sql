WITH l AS (
 SELECT source_id,category,count(*) n,min(currency) currency,count(DISTINCT currency) currencies,
 min(business_key) ledger_event_id,sum(gross)::bigint gross,sum(fee)::bigint fee,sum(net)::bigint net,
 array_agg(id) record_ids
 FROM ledger_all WHERE effective_at>=%(start)s AND effective_at<%(end)s GROUP BY source_id,category
), a AS (
 SELECT source_id,category,count(*) n,min(currency) currency,count(DISTINCT currency) currencies,
 sum(gross)::bigint gross,sum(fee)::bigint fee,sum(net)::bigint net,
 bool_and(gross-fee=net) arithmetic_valid,array_agg(id) record_ids
 FROM activity_all WHERE effective_at>=%(start)s AND effective_at<%(end)s GROUP BY source_id,category
)
SELECT coalesce(l.source_id,a.source_id)||':'||coalesce(l.category,a.category) entity_key,
 coalesce(l.currency,a.currency) currency,l.ledger_event_id,coalesce(l.source_id,a.source_id) source_id,
 coalesce(l.category,a.category) category,l.n ledger_count,a.n activity_count,
 l.currency ledger_currency,a.currency activity_currency,
 l.gross ledger_gross_minor,a.gross activity_gross_minor,l.fee ledger_fee_minor,a.fee activity_fee_minor,
 l.net ledger_net_minor,a.net activity_net_minor,l.record_ids ledger_record_ids,a.record_ids activity_record_ids,
 CASE WHEN l.n=1 AND a.n=1 AND l.currency=a.currency AND a.arithmetic_valid THEN l.net-a.net END delta_minor,
 CASE WHEN l.n>1 OR l.currencies>1 THEN 'duplicate_ledger_key'
      WHEN a.n>1 OR a.currencies>1 THEN 'duplicate_processor_key'
      WHEN NOT a.arithmetic_valid THEN 'invalid_source_arithmetic'
      WHEN l.source_id IS NULL THEN 'missing_ledger'
      WHEN a.source_id IS NULL THEN 'missing_processor'
      WHEN l.currency<>a.currency THEN 'currency_mismatch'
      WHEN l.gross<>a.gross AND l.fee<>a.fee AND l.net=a.net THEN 'offsetting_gross_fee'
      WHEN l.fee<>a.fee THEN 'fee_mismatch'
      WHEN l.gross<>a.gross THEN 'gross_mismatch'
      WHEN l.net<>a.net THEN 'net_mismatch' ELSE 'matched' END status
FROM l FULL OUTER JOIN a USING(source_id,category)
