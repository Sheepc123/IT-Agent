import { cookies } from "next/headers";

export async function POST() {
  const cookieStore = await cookies();
  const token = cookieStore.get("it_agent_dev_session")?.value;
  if (token && process.env.NODE_ENV === "development") {
    try {
      const origin = process.env.BACKEND_API_ORIGIN ?? "http://127.0.0.1:8000";
      await fetch(new URL("/api/v1/auth/logout", origin), {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
        cache: "no-store", signal: AbortSignal.timeout(5000),
      });
    } catch { /* Always clear the browser cookie. */ }
  }
  cookieStore.delete("it_agent_dev_session");
  return Response.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
}
