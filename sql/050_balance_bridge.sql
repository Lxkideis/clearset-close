WITH a AS(SELECT currency,sum(net)::bigint net FROM activity_all WHERE effective_at>=%(start)s AND effective_at<%(end)s GROUP BY currency),
 w AS(SELECT currency,sum(net)::bigint net FROM movement_all WHERE effective_at>=%(start)s AND effective_at<%(end)s GROUP BY currency),
 s AS(SELECT currency,count(*) n,
 max(net) FILTER(WHERE category='starting_balance') opening,
 max(net) FILTER(WHERE category='ending_balance') ending,
 max(net) FILTER(WHERE category='activity') activity,
 max(net) FILTER(WHERE category='payouts') payouts,array_agg(id) record_ids
 FROM summary_all WHERE effective_at=%(end)s GROUP BY currency)
SELECT c.code entity_key,c.code currency,s.n summary_categories,s.opening opening_minor,s.ending ending_minor,
 s.activity summary_activity_minor,s.payouts summary_payouts_minor,coalesce(a.net,0) activity_minor,coalesce(w.net,0) movement_minor,
 (s.opening+coalesce(a.net,0)+coalesce(w.net,0))::bigint calculated_ending_minor,
 (s.opening+coalesce(a.net,0)+coalesce(w.net,0)-s.ending)::bigint residual_minor,
 (coalesce(a.net,0)-s.activity)::bigint activity_delta_minor,
 (-coalesce(w.net,0)-s.payouts)::bigint payout_delta_minor,
 (s.opening+s.activity-s.payouts-s.ending)::bigint summary_identity_delta_minor,s.record_ids summary_record_ids
FROM currency c LEFT JOIN a ON c.code=a.currency LEFT JOIN w ON c.code=w.currency LEFT JOIN s ON c.code=s.currency
