// Koro remote MCP connector, served by Supabase Edge Functions.
// URL: https://<project>.supabase.co/functions/v1/koro-mcp/<connector token>
// Create a token with `koro connector new`. Deploy with verify_jwt disabled:
// the function authenticates requests itself with the token in its URL.
import { cachedTokenCheck, createHandler, makeRpc, makeStorage, secretKeyFromEnv } from "./mcp.ts";

const url = Deno.env.get("SUPABASE_URL")!;
const key = secretKeyFromEnv((name) => Deno.env.get(name));
const rpc = makeRpc(url, key);

Deno.serve(createHandler({ rpc, storage: makeStorage(url, key), checkToken: cachedTokenCheck(rpc), functionName: "koro-mcp" }));
