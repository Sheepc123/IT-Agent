import { cookies } from "next/headers";

const paths: Record<string, string> = {
  me: "/api/v1/auth/me",
  tasks: "/api/v1/tasks",
  dashboard: "/api/v1/dashboard",
};

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ resource: string }> },
) {
  const { resource } = await params;
  const path = paths[resource];
  if (!path) return Response.json({ error: "Unknown resource" }, { status: 404 });
  if (process.env.NODE_ENV !== "development") {
    return Response.json({ error: "Local login unavailable" }, { status: 503 });
  }
  const token = (await cookies()).get("it_agent_dev_session")?.value;
  if (!token) return Response.json({ error: "Login required" }, { status: 401 });
  try {
    const origin = process.env.BACKEND_API_ORIGIN ?? "http://127.0.0.1:8000";
    const response = await fetch(new URL(path, origin), {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return new Response(await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ error: "Backend unavailable" }, { status: 503 });
  }
}
