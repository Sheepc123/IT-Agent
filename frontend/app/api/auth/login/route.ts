import { cookies } from "next/headers";

export async function POST(request: Request) {
  if (process.env.NODE_ENV !== "development") {
    return Response.json({ error: "Local login unavailable" }, { status: 503 });
  }
  let input: { username?: string; password?: string };
  try {
    input = await request.json();
  } catch {
    return Response.json({ error: "Invalid request" }, { status: 400 });
  }
  if (!input || typeof input.username !== "string" || typeof input.password !== "string") {
    return Response.json({ error: "Invalid credentials" }, { status: 400 });
  }
  try {
    const origin = process.env.BACKEND_API_ORIGIN ?? "http://127.0.0.1:8000";
    const response = await fetch(new URL("/api/v1/auth/login", origin), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: input.username, password: input.password }),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return Response.json({ error: response.status === 401 ? "账号或密码错误" : "后端登录不可用" }, { status: response.status });
    const result = await response.json() as { access_token: string; expires_in: number };
    (await cookies()).set("it_agent_dev_session", result.access_token, {
      httpOnly: true, sameSite: "strict", secure: false, path: "/", maxAge: result.expires_in,
    });
    return Response.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "后端不可用" }, { status: 503 });
  }
}
