"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import styles from "./dashboard.module.css";

type Role = "REQUESTER" | "BUYER" | "IT_ASSET_ADMIN" | "TECH_REVIEWER" | "PROCUREMENT_APPROVER";
type Me = { id: string; display_name: string; department_id: string | null; identity_source: string; grants: { role: string; scope_id: string }[] };
type Task = { id: string; request_no: string; business_type: "A" | "B"; task_type: string; role: Role; title: string; subject: string; status: string; detail: string; allowed_actions: string[] };
type RequestItem = { id: string; request_no: string; business_type: "A" | "B"; status: string; title: string };
type ApiEnvelope<T> = { data: T };

const roleLabels: Record<string, string> = {
  REQUESTER: "申请人", BUYER: "采购员", IT_ASSET_ADMIN: "资产管理员",
  TECH_REVIEWER: "技术审核人", PROCUREMENT_APPROVER: "采购审批人",
  AUDITOR: "审计人员", SYS_ADMIN: "系统管理员",
};
const statusLabels: Record<string, string> = {
  DRAFT: "草稿", SUBMITTED: "已提交", FULFILLING: "履约中",
  COMPLETED: "已完成", CANCELLED: "已取消",
};
async function readApi<T>(resource: string): Promise<T> {
  const response = await fetch(`/api/workbench/${resource}`, { cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 401 ? "登录已失效，请重新登录。" : `工作台接口不可用（${response.status}）。`);
  return (await response.json() as ApiEnvelope<T>).data;
}

function Icon({ name, size = 20 }: { name: "grid" | "file" | "check" | "cube" | "book" | "chart" | "arrow" | "search" | "close" | "chevron"; size?: number }) {
  const paths: Record<typeof name, React.ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    file: <><path d="M6 3h9l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" /><path d="M14 3v5h5M8 12h8M8 16h6" /></>,
    check: <><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M8 2v4M16 2v4M3 9h18M8 15l2 2 5-5" /></>,
    cube: <><path d="m12 2 9 5-9 5-9-5 9-5ZM3 7v10l9 5 9-5V7M12 12v10" /></>,
    book: <><path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1ZM12 6v14" /></>,
    chart: <><path d="M4 20V4M4 20h16M8 16v-5M13 16V7M18 16v-8" /></>,
    arrow: <><path d="M5 12h14m-6-6 6 6-6 6" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    close: <path d="M5 5 19 19M19 5 5 19" />,
    chevron: <path d="m9 6 6 6-6 6" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

const nav = [
  { label: "工作台", icon: "grid" },
  { label: "新建采购需求", icon: "file" },
  { label: "审核与采购", icon: "check" },
  { label: "资产与交付", icon: "cube" },
  { label: "知识与数据", icon: "book" },
  { label: "评测与运维", icon: "chart" },
] as const;

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [requests, setRequests] = useState<RequestItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [role, setRole] = useState<Role | "all">("all");
  const [query, setQuery] = useState("");
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      readApi<Me>("me"),
      readApi<{ items: Task[]; has_more: boolean }>("tasks"),
      readApi<{ requests: RequestItem[]; has_more: boolean }>("dashboard"),
    ]).then(([person, taskData, dashboardData]) => {
      if (!active) return;
      setMe(person);
      setTasks(taskData.items);
      setRequests(dashboardData.requests);
      setHasMore(taskData.has_more || dashboardData.has_more);
      setLoading(false);
    }).catch((reason: unknown) => {
      if (!active) return;
      setError(reason instanceof Error ? reason.message : "工作台加载失败。");
      setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const grantedRoles = useMemo(() => [...new Set(me?.grants.map((grant) => grant.role) ?? [])], [me]);
  const visibleTasks = useMemo(() => tasks.filter((task) =>
    (role === "all" || task.role === role) &&
    `${task.title} ${task.subject} ${task.request_no}`.toLowerCase().includes(query.trim().toLowerCase()),
  ), [tasks, role, query]);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
  }

  function openTask(task: Task) {
    setSelectedTask(task);
    dialogRef.current?.showModal();
  }
  function closeTask() { dialogRef.current?.close(); }

  return <div className={styles.shell}>
    <aside className={styles.sidebar} aria-label="主导航">
      <Link className={styles.brand} href="/dashboard"><span className={styles.brandMark}><span /><span /><span /><span /></span><span className={styles.brandText}><strong>IT 采购协作台</strong><small>ASSET &amp; INFRA</small></span></Link>
      <div className={styles.sideGroupLabel}>工作空间</div>
      <nav className={styles.navList} aria-label="页面导航">{nav.map((item, index) => index === 0 ?
        <Link key={item.label} className={`${styles.navItem} ${styles.navActive}`} href="/dashboard" aria-current="page"><Icon name={item.icon} /><span>{item.label}</span><span className={styles.activeDot} /></Link> : index === 1 ?
        grantedRoles.includes("REQUESTER") ? <Link key={item.label} className={styles.navItem} href="/requests/new"><Icon name={item.icon} /><span>{item.label}</span></Link> : null :
        <span key={item.label} className={styles.navItem}><Icon name={item.icon} /><span>{item.label}</span><small>规划中</small></span>)}</nav>
      <div className={styles.sidebarBottom}><span className={styles.eyebrow}>当前账号</span><strong>{me?.display_name ?? "未识别"}</strong><p>{me ? grantedRoles.map((item) => roleLabels[item] ?? item).join(" / ") : "等待后端确认身份"}</p><span className={styles.version}>P08 · 工作台</span></div>
    </aside>
    <div className={styles.contentFrame}>
      <header className={styles.topbar}><div className={styles.breadcrumb}><span>工作空间</span><Icon name="chevron" size={15} /><strong>工作台</strong></div><div className={styles.topActions}><span className={styles.demoPill}><span />{me?.identity_source === "demo_header" ? "演示身份" : "本地数据库账号"}</span><button type="button" className={styles.logoutButton} onClick={logout}>退出登录</button><div className={styles.avatar} aria-label={me?.display_name ?? "未登录"}>{me?.display_name.slice(0, 1) ?? "?"}</div></div></header>
      <main className={styles.main}>
        <div className={styles.intro}><div><span className={styles.eyebrow}>IT ASSET &amp; PROCUREMENT</span><h1>让每一项采购，<br /><em>都有据可循。</em></h1><p>一个账号查看全部待办；职责筛选只改变视图，权限由后端判断。</p></div><div className={styles.introAction}>{grantedRoles.includes("REQUESTER") ? <><Link href="/requests/new" className={styles.primaryAction}>新建采购需求 <Icon name="arrow" size={18} /></Link><span>先选择办公设备或 AI 基础设施；表单目前仅供前端预览</span></> : <><a href="#tasks" className={styles.primaryAction}>查看我的待办 <Icon name="arrow" size={18} /></a><span>当前账号没有申请人职责</span></>}</div></div>
        {loading && <p className={styles.emptyState} role="status">正在加载当前账号和待办…</p>}
        {error && <p className={styles.emptyState} role="alert">{error} <Link href="/login">前往登录</Link></p>}
        {me && !error && <>
          <section className={styles.roleSection} aria-labelledby="role-heading"><div className={styles.sectionHeading}><div><span className={styles.eyebrow}>MY WORK / 我的工作</span><h2 id="role-heading">按职责筛选</h2></div><p>一个账号可兼任多项职责，同一采购单仍按职责分离校验。</p></div><div className={styles.roleTabs} role="group" aria-label="待办职责筛选"><button type="button" className={`${styles.roleTab} ${role === "all" ? styles.roleTabActive : ""}`} aria-pressed={role === "all"} onClick={() => setRole("all")}>全部待办</button>{grantedRoles.map((item) => <button type="button" key={item} className={`${styles.roleTab} ${role === item ? styles.roleTabActive : ""}`} aria-pressed={role === item} onClick={() => setRole(item as Role)}>{roleLabels[item] ?? item}</button>)}</div><p className={styles.roleDescription}><span className={styles.roleLine} />当前账号：{me.display_name}。筛选不会切换登录身份。</p></section>
          <div className={styles.dashboardGrid}><section className={styles.taskSection} id="tasks" aria-labelledby="tasks-heading"><div className={styles.sectionHeading}><div><span className={styles.eyebrow}>ACTION REQUIRED</span><h2 id="tasks-heading">待你处理</h2></div><span className={styles.sectionCount}>{visibleTasks.length} 项</span></div><label className={styles.searchBox}><Icon name="search" size={18} /><span className="sr-only">搜索待办</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索待办或需求编号" /></label>{visibleTasks.length ? <div className={styles.taskList}>{visibleTasks.map((task) => <button type="button" key={`${task.task_type}-${task.id}`} className={styles.taskRow} onClick={() => openTask(task)}><span className={`${styles.taskIcon} ${task.business_type === "B" ? styles.taskIconB : styles.taskIconA}`}>{task.business_type}</span><span className={styles.taskCopy}><strong>{task.title}</strong><small>{task.subject} · {task.request_no}</small></span><span className={`${styles.status} ${styles.neutral}`}>{statusLabels[task.status] ?? task.status}</span><Icon name="chevron" size={17} /></button>)}</div> : <div className={styles.emptyState}>当前没有匹配的待办。审核、收货任务需等正式指派记录建立后才会显示。</div>}<div className={styles.taskFootnote}>仅从当前数据库需求与授权生成；尚未建立的审核、资产和业务 B 调查任务不会伪造。</div></section><aside className={styles.nextSection} aria-labelledby="next-heading"><div className={styles.nextTop}><span className={styles.eyebrow}>HOW IT WORKS</span><h2 id="next-heading">先确认条件，<br />再推进决定。</h2><p>业务 A 处理设备采购与资产复用；业务 B 增加技术调查和兼容性证据。两条流程最终进入同一套审核与交付链路。</p></div><div className={styles.flowMini}><div><span>01</span>需求与资产</div><div><span>02</span>调查与方案</div><div><span>03</span>审核与交付</div></div><a href="#requests" className={styles.textLink}>查看进行中的需求 <Icon name="arrow" size={17} /></a></aside></div>
          <section className={styles.requestsSection} id="requests" aria-labelledby="requests-heading"><div className={styles.sectionHeading}><div><span className={styles.eyebrow}>IN PROGRESS</span><h2 id="requests-heading">可见的需求</h2></div><span className={styles.sectionCount}>{requests.length} 项</span></div>{requests.length ? <div className={styles.requestList}>{requests.map((request) => <article key={request.id} className={styles.requestRow}><div className={styles.requestIdentity}><span className={`${styles.businessBadge} ${request.business_type === "B" ? styles.businessB : styles.businessA}`}>业务 {request.business_type}</span><span className={styles.requestCategory}>{statusLabels[request.status] ?? request.status}</span></div><div className={styles.requestTitleRow}><h3>{request.title}</h3><span>{request.request_no}</span></div><p>状态：{statusLabels[request.status] ?? request.status}</p></article>)}</div> : <div className={styles.emptyState}>当前账号没有可见的需求。</div>}<p className={styles.requestNote}>列表来自后端授权范围；尚无完整流程进度数据，因此不显示推测的百分比。{hasMore && " 当前只显示最近 100 项，分页仍待接入。"}</p></section>
        </>}
        <footer className={styles.footer}><span>IT 采购协作台 · 开发联调</span><span>企业身份接入与审核指派仍待建设。</span></footer>
      </main>
    </div>
    <dialog ref={dialogRef} className={styles.taskDialog} onClose={() => setSelectedTask(null)} onClick={(event) => { if (event.target === dialogRef.current) closeTask(); }} aria-labelledby="dialog-title">{selectedTask && <div className={styles.dialogInner}><div className={styles.dialogHeader}><span className={styles.eyebrow}>{selectedTask.request_no}</span><button type="button" onClick={closeTask} aria-label="关闭详情"><Icon name="close" /></button></div><h2 id="dialog-title">{selectedTask.title}</h2><p className={styles.dialogSubject}>{selectedTask.subject}</p><div className={styles.dialogBlock}><strong>当前情况</strong><p>{selectedTask.detail}</p></div><div className={styles.dialogBlock}><strong>后端允许的动作</strong><p>{selectedTask.allowed_actions.length ? selectedTask.allowed_actions.join("、") : "暂无；相关编辑页面尚未建设。"}</p></div><div className={styles.dialogFooter}><span>操作页尚未建设，本页只展示后端返回的待办。</span><button type="button" onClick={closeTask}>返回工作台</button></div></div>}</dialog>
  </div>;
}
