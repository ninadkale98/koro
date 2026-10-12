// Koro remote MCP connector, served by Supabase Edge Functions.
// URL: https://<project>.supabase.co/functions/v1/koro-mcp/<connector token>
// Create a token with `koro connector new`. Deploy with verify_jwt disabled:
// the function authenticates requests itself with the token in its URL.
import { cachedTokenCheck, createHandler, makeRpc, secretKeyFromEnv } from "./mcp.ts";

const rpc = makeRpc(Deno.env.get("SUPABASE_URL")!, secretKeyFromEnv((name) => Deno.env.get(name)));

Deno.serve(createHandler({ rpc, checkToken: cachedTokenCheck(rpc), functionName: "koro-mcp" }));
