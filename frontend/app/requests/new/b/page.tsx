"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { RequestFrame } from "../components/RequestFrame";
import styles from "../request.module.css";

type InfraDraft = { mode: string; category: string; workload: string; goal: string; model: string; scale: string; existing: string; constraints: string; budget: string; neededBy: string };
const initial: InfraDraft = { mode: "WORKLOAD", category: "", workload: "", goal: "", model: "", scale: "", existing: "", constraints: "", budget: "", neededBy: "" };

/** Gather a B-class investigation brief with mode-specific context fields. */
export default function InfraRequestPage() {
  const [draft, setDraft] = useState<InfraDraft>(initial);
  const [preview, setPreview] = useState(false);
  function update<K extends keyof InfraDraft>(key: K, value: InfraDraft[K]) { setDraft((current) => ({ ...current, [key]: value })); setPreview(false); }
  function showPreview(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPreview(true); }
  const modeLabel = draft.mode === "WORKLOAD" ? "按工作负载选型" : draft.mode === "VERIFY" ? "验证现有配置" : "扩容现有集群";

  return <RequestFrame active="b">
    <div className={styles.eyebrow}>BUSINESS B / AI 研发基础设施</div>
    <div className={styles.formIntro}><div><h1>先描述要解决的问题。</h1><p>可以从工作负载、待验证配置或扩容目标开始。硬件型号尚未确定也能先填写需求。</p></div><span className={`${styles.moduleBadge} ${styles.badgeB}`}>B 类需求</span></div>
    <div className={styles.progress}><span className={styles.progressCurrent}>01 描述场景</span><span>02 技术调查与证据</span><span>03 方案审批</span></div>
    <div className={styles.formGrid}><form className={styles.formCard} onSubmit={showPreview}>
      <section className={styles.fieldSection}><div className={styles.sectionTitle}><span>01</span><div><h2>采购目标</h2><p>先选一个入口，减少无关字段。</p></div></div>
        <div className={styles.modeGrid} role="group" aria-label="采购目标">{[{ value: "WORKLOAD", title: "按工作负载选型", detail: "从训练或推理任务反推资源" }, { value: "VERIFY", title: "验证现有配置", detail: "核对指定硬件的兼容性" }, { value: "EXPAND", title: "扩容现有集群", detail: "补充算力、存储或网络" }].map((option) => <button key={option.value} type="button" aria-pressed={draft.mode === option.value} className={`${styles.modeOption} ${draft.mode === option.value ? styles.modeSelected : ""}`} onClick={() => update("mode", option.value)}><strong>{option.title}</strong><small>{option.detail}</small></button>)}</div>
        <div className={styles.fieldsTwo}><label className={styles.field}>目标设备类型 <span>必填</span><select required value={draft.category} onChange={(event) => update("category", event.target.value)}><option value="">请选择设备</option><option>GPU 服务器</option><option>存储服务器</option><option>交换机</option><option>其他网络设备</option><option>多设备组合</option></select></label><label className={styles.field}>工作负载 <small>选填</small><select value={draft.workload} onChange={(event) => update("workload", event.target.value)}><option value="">尚未确定</option><option>模型训练</option><option>模型微调</option><option>在线推理</option><option>数据存储与处理</option><option>集群网络</option></select></label></div>
        <label className={styles.field}>目标与使用场景 <span>必填</span><textarea required rows={4} placeholder="例如：支持 70B 模型推理，说明并发、延迟或数据处理目标" value={draft.goal} onChange={(event) => update("goal", event.target.value)} /></label>
      </section>
      <section className={styles.fieldSection}><div className={styles.sectionTitle}><span>02</span><div><h2>{draft.mode === "VERIFY" ? "待验证配置" : draft.mode === "EXPAND" ? "现有集群与扩容" : "规模与现有环境"}</h2><p>没有确认的信息可以留空，后续调查会标为待确认。</p></div></div>
        {draft.mode === "WORKLOAD" && <><label className={styles.field}>模型或软件栈 <small>选填</small><input placeholder="例如：模型名称、框架、精度或版本" value={draft.model} onChange={(event) => update("model", event.target.value)} /></label><label className={styles.field}>目标规模 <small>选填</small><input placeholder="例如：并发、训练数据量、容量或吞吐目标" value={draft.scale} onChange={(event) => update("scale", event.target.value)} /></label></>}
        {draft.mode !== "WORKLOAD" && <label className={styles.field}>{draft.mode === "VERIFY" ? "拟采购或待验证的配置" : "扩容规模或目标配置"} <small>选填</small><textarea rows={3} placeholder="写明型号、数量、接口或期望增加的容量" value={draft.scale} onChange={(event) => update("scale", event.target.value)} /></label>}
        <label className={styles.field}>现有设备与环境 <small>选填</small><textarea rows={3} placeholder="例如：机柜、供电、散热、交换机端口、现有服务器型号" value={draft.existing} onChange={(event) => update("existing", event.target.value)} /></label>
        <label className={styles.field}>不可妥协的条件 <small>选填</small><textarea rows={3} placeholder="例如：必须兼容现有交换机；机柜只能提供 4U 空间" value={draft.constraints} onChange={(event) => update("constraints", event.target.value)} /></label>
      </section>
      <section className={styles.fieldSection}><div className={styles.sectionTitle}><span>03</span><div><h2>商务边界</h2><p>预算和时间会影响可行组合；未知时不猜测。</p></div></div><div className={styles.fieldsTwo}><label className={styles.field}>预算上限（人民币） <small>选填</small><input type="number" min="0" step="0.01" placeholder="含税预算" value={draft.budget} onChange={(event) => update("budget", event.target.value)} /></label><label className={styles.field}>期望到货日期 <small>选填</small><input type="date" value={draft.neededBy} onChange={(event) => update("neededBy", event.target.value)} /></label></div></section>
      <div className={styles.formActions}><Link href="/requests/new">返回选择类型</Link><button type="submit" className={styles.primaryButton}>预览调查需求 <span aria-hidden="true">→</span></button></div>
    </form><aside className={styles.summaryCard}><div className={styles.summaryTop}><span className={styles.eyebrow}>INVESTIGATION BRIEF</span><h2>调查输入摘要</h2><p>目前只整理输入；尚未调用 Agent 或检索技术手册。</p></div><dl className={styles.summaryList}><div><dt>采购目标</dt><dd>{modeLabel}</dd></div><div><dt>设备范围</dt><dd>{draft.category || "待选择"}</dd></div><div><dt>工作负载</dt><dd>{draft.workload || "待确认"}</dd></div><div><dt>预算上限</dt><dd>{draft.budget ? `¥ ${Number(draft.budget).toLocaleString("zh-CN")}` : "待确认"}</dd></div></dl>{preview && <div className={styles.previewBox} role="status"><strong>页面预览已生成</strong><p>{draft.goal}</p>{draft.constraints && <p>硬条件：{draft.constraints}</p>}<small>尚未保存到数据库，也未启动技术调查。</small></div>}<p className={styles.summaryNote}>后续会在需求确认后，基于已发布的技术资料检查规格与兼容性，并保留证据和未知项。</p></aside></div>
  </RequestFrame>;
}
