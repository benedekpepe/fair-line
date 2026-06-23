// supabase/functions/lemonsqueezy-webhook/index.ts
//
// Receives Lemon Squeezy webhook events and updates profiles.subscription_status.
//
// Deploy:
//   supabase functions deploy lemonsqueezy-webhook --no-verify-jwt
// Secret (the webhook signing secret you set in Lemon Squeezy):
//   supabase secrets set LEMONSQUEEZY_WEBHOOK_SECRET=your_signing_secret
// (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected automatically.)
//
// In the checkout link we pass checkout[custom][user_id]=<supabase user id>,
// which arrives here as meta.custom_data.user_id so we can match the buyer.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY  = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SECRET       = Deno.env.get("LEMONSQUEEZY_WEBHOOK_SECRET") ?? "";

const ACTIVE_STATUSES = ["active", "on_trial"];

function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function validSignature(raw: string, sig: string): Promise<boolean> {
  if (!SECRET || !sig) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(raw));
  return toHex(mac) === sig.toLowerCase();
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

  const raw = await req.text();
  const sig = req.headers.get("X-Signature") ?? "";
  if (!(await validSignature(raw, sig))) {
    return new Response("invalid signature", { status: 401 });
  }

  let body: any;
  try { body = JSON.parse(raw); } catch { return new Response("bad json", { status: 400 }); }

  const userId = body?.meta?.custom_data?.user_id;
  const attrs  = body?.data?.attributes ?? {};
  const status = attrs.status;                       // active | cancelled | expired | past_due ...
  if (!userId) return new Response("no user_id", { status: 200 });

  const isActive = ACTIVE_STATUSES.includes(status);
  const sb = createClient(SUPABASE_URL, SERVICE_KEY);
  const update: Record<string, unknown> = {
    subscription_status: isActive ? "active" : "canceled",
    subscription_tier:   isActive ? "pro" : null,
    current_period_end:  attrs.renews_at ?? attrs.ends_at ?? null,
  };
  const portal = attrs?.urls?.customer_portal;
  if (portal) update.portal_url = portal;   // signed Lemon Squeezy customer portal link
  const { error } = await sb.from("profiles").update(update).eq("id", userId);

  if (error) return new Response("db error: " + error.message, { status: 500 });
  return new Response("ok", { status: 200 });
});
