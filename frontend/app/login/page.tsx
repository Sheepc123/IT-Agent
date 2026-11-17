"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./login.module.css";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        const body = await response.json() as { error?: string };
        setError(body.error ?? "登录失败");
        return;
      }
      router.replace("/dashboard");
    } catch { setError("无法连接登录服务"); }
    finally { setBusy(false); }
  }

  return <main className={styles.page}>
    <section className={styles.intro}><span className={styles.eyebrow}>IT ASSET &amp; INFRA</span><h1>让每一项采购，<br /><em>都有据可循。</em></h1><p>从需求到证据、审批和交付，在同一个工作空间协作。</p><div className={styles.mark}>IT 采购协作台</div></section>
    <section className={styles.panel} aria-labelledby="login-title"><div className={styles.card}><span className={styles.eyebrow}>LOCAL DEVELOPMENT / 本地联调</span><h2 id="login-title">登录工作台</h2><p>使用数据库中的测试账号。登录后按账号实际授权展示工作内容。</p><form onSubmit={submit}><label>账号<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></label><label>密码<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>{error && <span role="alert" className={styles.error}>{error}</span>}<button type="submit" disabled={busy}>{busy ? "正在验证…" : "进入工作台 →"}</button></form><small>仅供本地开发测试，企业登录接入后替换。</small></div></section>
  </main>;
}
