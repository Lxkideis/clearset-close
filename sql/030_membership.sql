WITH allocation AS (
 SELECT transaction_id,count(DISTINCT payout_id) payout_count FROM member_all
 WHERE effective_at<%(end)s GROUP BY transaction_id
)
SELECT m.business_key entity_key,m.currency,m.payout_id,m.transaction_id,m.id member_record_id,a.id activity_record_id,
 m.gross member_gross_minor,m.fee member_fee_minor,m.net member_net_minor,a.gross activity_gross_minor,
 a.fee activity_fee_minor,a.net activity_net_minor,a.currency activity_currency,
 CASE WHEN p.business_key IS NULL THEN 'unknown_payout'
      WHEN NOT p.automatic THEN 'unsupported_manual_membership'
      WHEN x.payout_count>1 THEN 'multiple_payout_allocation'
      WHEN a.business_key IS NULL THEN 'missing_activity_evidence'
      WHEN m.gross-m.fee<>m.net OR a.gross-a.fee<>a.net THEN 'invalid_source_arithmetic'
      WHEN m.currency<>a.currency OR m.currency<>p.currency THEN 'currency_mismatch'
      WHEN (m.gross,m.fee,m.net) IS DISTINCT FROM (a.gross,a.fee,a.net) THEN 'report_component_mismatch'
      ELSE 'matched' END status
FROM member_all m LEFT JOIN activity_all a ON a.business_key=m.transaction_id
LEFT JOIN payout_all p ON p.business_key=m.payout_id
JOIN allocation x USING(transaction_id) WHERE m.effective_at<%(end)s
