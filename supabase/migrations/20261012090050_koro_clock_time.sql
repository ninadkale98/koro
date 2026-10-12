-- Several pieces of evidence can be written in one transaction (for example
-- marking a whole chapter known), so the log orders by wall-clock time.
alter table public.assessments alter column created_at set default clock_timestamp();
