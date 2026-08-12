/**
 * Edge middleware proxy for Yahoo chart data.
 *
 * Serverless functions egress from shared datacenter IPs that Yahoo
 * hard-rate-limits, but edge middleware runs on Vercel's edge network —
 * the same IPs millions of real browsers use — so Yahoo treats it like a
 * normal user. The frontend-visible /api/* stays unchanged; the Python
 * fetcher calls this internal endpoint for FX candles.
 */
export default async function middleware(request: Request) {
  const url = new URL(request.url);
  const target = url.searchParams.get("url");

  if (
    !target ||
    (!target.startsWith("https://query1.finance.yahoo.com/") &&
      !target.startsWith("https://query2.finance.yahoo.com/"))
  ) {
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
