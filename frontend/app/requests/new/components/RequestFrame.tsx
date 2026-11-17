"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import styles from "../request.module.css";

type Me = { display_name: string; grants: { role: string }[] };
type ApiEnvelope<T> = { data: T };

/** Keep the creation workspace tied to the current authenticated requester. */
export function RequestFrame({ active, children }: { active: "choose" | "a" | "b"; children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "unauthorized" | "error">("loading");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/workbench/me", { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 401) { setState("unauthorized"); return; }
        if (!response.ok) throw new Error("无法读取当前账号。请检查后端服务。");
        const payload = await response.json() as ApiEnvelope<Me>;
        setMe(payload.data);
        setState("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setState("error");
        console.error("Failed to load current requester", reason);
      });
    return () => controller.abort();
  }, []);

  const canCreate = me?.grants.some((grant) => grant.role === "REQUESTER") ?? false;
  return <div className={styles.shell}>
    <aside className={styles.sidebar} aria-label="采购需求导航">
      <Link className={styles.brand} href="/dashboard"><span className={styles.brandMark}>✳</span><span><strong>IT 采购协作台</strong><small>ASSET &amp; INFRA</small></span></Link>
      <p className={styles.sideLabel}>采购需求</p>
      <nav className={styles.sideNav} aria-label="需求类型">
        <Link className={active === "choose" ? styles.sideActive : ""} href="/requests/new">选择采购类型</Link>
        <Link className={active === "a" ? styles.sideActive : ""} href="/requests/new/a">A · 办公与开发设备</Link>
        <Link className={active === "b" ? styles.sideActive : ""} href="/requests/new/b">B · AI 研发基础设施</Link>
      </nav>
      <div className={styles.sideFoot}><span>当前账号</span><strong>{me?.display_name ?? "正在确认身份"}</strong><p>需求创建仅面向有申请人职责的账号。</p></div>
    </aside>
    <div className={styles.content}>
      <header className={styles.topbar}><div><Link href="/dashboard">工作台</Link><span aria-hidden="true"> / </span><Link href="/requests/new">新建需求</Link>{active !== "choose" && <><span aria-hidden="true"> / </span><strong>业务 {active.toUpperCase()}</strong></>}</div><span className={styles.topIdentity}>{me?.display_name ?? "身份核验中"}</span></header>
      <main className={styles.main}>
        {state === "loading" && <p className={styles.stateMessage} role="status">正在确认登录身份…</p>}
        {state === "unauthorized" && <p className={styles.stateMessage} role="alert">登录已失效。<Link href="/login">前往登录</Link></p>}
        {state === "error" && <p className={styles.stateMessage} role="alert">当前无法读取账号信息，请确认后端服务已启动后刷新页面。</p>}
        {state === "ready" && !canCreate && <p className={styles.stateMessage} role="alert">当前账号没有申请人职责，无法创建采购需求。<Link href="/dashboard">返回工作台</Link></p>}
        {state === "ready" && canCreate && children}
      </main>
    </div>
  </div>;
}
