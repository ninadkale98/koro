-- Synchronous HTTP from SQL, used to test the koro-mcp Edge Function end to end
-- from inside the database (see supabase/tests/connector_http_test.sql).
create extension if not exists http with schema extensions;
