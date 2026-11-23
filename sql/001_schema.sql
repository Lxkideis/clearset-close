CREATE SCHEMA IF NOT EXISTS closeops;
SET search_path TO closeops,public;
CREATE TABLE IF NOT EXISTS currency(code text PRIMARY KEY, exponent smallint NOT NULL CHECK(exponent IN(0,2)));
INSERT INTO currency VALUES('SGD',2),('USD',2),('JPY',0) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS import_batch(
 id uuid PRIMARY KEY, source text NOT NULL CHECK(source IN('ledger','activity','membership','payouts','movements','bank','summary')),
 sha256 text NOT NULL CHECK(length(sha256)=64), filename text NOT NULL,
 observed_at timestamptz NOT NULL, ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 record_count integer NOT NULL CHECK(record_count>0), UNIQUE(source,sha256)
);
CREATE TABLE IF NOT EXISTS observation(
 id uuid PRIMARY KEY,source text NOT NULL,business_key text NOT NULL,revision integer NOT NULL CHECK(revision>=1),
 observed_at timestamptz NOT NULL,effective_at timestamptz NOT NULL,
 retracted boolean NOT NULL DEFAULT false,payload jsonb NOT NULL,
 payload_sha256 text NOT NULL CHECK(length(payload_sha256)=64),batch_id uuid NOT NULL REFERENCES import_batch(id),
 UNIQUE(source,business_key,revision)
);
CREATE INDEX IF NOT EXISTS observation_asof_idx ON observation(source,business_key,revision DESC,observed_at);
CREATE INDEX IF NOT EXISTS observation_known_idx ON observation(observed_at);
CREATE TABLE IF NOT EXISTS close_run(
 id uuid PRIMARY KEY,period_start timestamptz NOT NULL,period_end timestamptz NOT NULL,
 knowledge_cutoff timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 created_by text NOT NULL,fingerprint text NOT NULL UNIQUE,policy jsonb NOT NULL,
 engine_hash text NOT NULL,lineage jsonb NOT NULL,stats jsonb,completed_at timestamptz,
 CHECK(period_start<period_end AND knowledge_cutoff>=period_end)
);
CREATE TABLE IF NOT EXISTS control_result(
 run_id uuid NOT NULL REFERENCES close_run(id),kind text NOT NULL,
 entity_key text NOT NULL,currency text REFERENCES currency(code),status text NOT NULL,
 severity text NOT NULL CHECK(severity IN('pass','pending','advisory','blocker')),
 detail jsonb NOT NULL, PRIMARY KEY(run_id,kind,entity_key)
);
CREATE INDEX IF NOT EXISTS control_result_queue_idx ON control_result(run_id,severity,kind,currency);
CREATE TABLE IF NOT EXISTS case_note(
 id uuid PRIMARY KEY,run_id uuid NOT NULL,kind text NOT NULL,entity_key text NOT NULL,
 actor text NOT NULL,note text NOT NULL CHECK(length(note)>=12),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,kind,entity_key) REFERENCES control_result(run_id,kind,entity_key)
);
CREATE TABLE IF NOT EXISTS certification(
 id uuid PRIMARY KEY,run_id uuid NOT NULL UNIQUE REFERENCES close_run(id),
 reviewer text NOT NULL,note text NOT NULL CHECK(length(note)>=12),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),fingerprint text NOT NULL
);
CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Append-only evidence cannot be updated or deleted'; END $$;
DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['observation','import_batch','control_result','case_note','certification'] LOOP
   EXECUTE format('DROP TRIGGER IF EXISTS append_only ON %I',tab);
   EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_mutation()',tab);
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION protect_completed_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF OLD.completed_at IS NOT NULL THEN RAISE EXCEPTION 'Completed snapshots are immutable'; END IF; RETURN NEW; END $$;
DROP TRIGGER IF EXISTS completed_run ON close_run;
CREATE TRIGGER completed_run BEFORE UPDATE OR DELETE ON close_run FOR EACH ROW EXECUTE FUNCTION protect_completed_run();
GRANT USAGE ON SCHEMA closeops TO close_app;
GRANT SELECT ON ALL TABLES IN SCHEMA closeops TO close_app;
GRANT INSERT ON import_batch,observation,close_run,control_result,case_note,certification TO close_app;
GRANT UPDATE ON close_run TO close_app;
