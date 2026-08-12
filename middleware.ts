/**
 * Edge middleware proxy for Yahoo chart data.
 *
 * Serverless functions egress from shared datacenter IPs that Yahoo
 * hard-rate-limits, but edge middleware runs on Vercel's edge network —
 * the same IPs millions of real browsers use — so Yahoo treats it like a
 * normal user.
 *
 * GET  /api/edge-fetch?url=...      single fetch
 * POST /api/edge-fetch {urls:[...]} batched fetch — all URLs fetched in
 *      parallel inside the edge, one round-trip for the whole FX watchlist.
 */
export default async function middleware(request: Request) {
  const url = new URL(request.url);
  const isYahoo = (u: string) =>
    typeof u === "string" &&
    (u.startsWith("https://query1.finance.yahoo.com/") ||
      u.startsWith("https://query2.finance.yahoo.com/"));

  if (request.method === "POST") {
    try {
      const body = (await request.json()) as { urls?: string[] };
      const urls = (body.urls || []).filter(isYahoo).slice(0, 20);
      if (!urls.length) return new Response("no urls", { status: 400 });
      const results = await Promise.all(
        urls.map(async (u) => {
          try {
            const r = await fetch(u, { headers: { "User-Agent": "Mozilla/5.0" } });
            return { url: u, status: r.status, body: await r.json() };
          } catch (e) {
            return { url: u, status: 0, body: null };
          }
        })
      );
      const map: Record<string, unknown> = {};
      for (const r of results) map[r.url] = r;
      return new Response(JSON.stringify(map), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "public, s-maxage=20, stale-while-revalidate=60",
        },
      });
    } catch (e) {
      return new Response("bad request", { status: 400 });
    }
  }

  const target = url.searchParams.get("url");
  if (!target || !isYahoo(target)) {
    return new Response("invalid target", { status: 400 });
  }
  try {
    const upstream = await fetch(target, {
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, s-maxage=30, stale-while-revalidate=60",
      },
    });
  } catch (e) {
    return new Response("proxy error", { status: 502 });
  }
}

export const config = { matcher: ["/api/edge-fetch"] };
