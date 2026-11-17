"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { RequestFrame } from "../components/RequestFrame";
import styles from "../request.module.css";

type OfficeDraft = { category: string; quantity: string; purpose: string; specification: string; budget: string; neededBy: string; deliveryLocation: string; existingAsset: string };
const initial: OfficeDraft = { category: "", quantity: "1", purpose: "", specification: "", budget: "", neededBy: "", deliveryLocation: "", existingAsset: "" };

/** Collect and preview office equipment needs without claiming persistence. */
export default function OfficeRequestPage() {
  const [draft, setDraft] = useState<OfficeDraft>(initial);
  const [preview, setPreview] = useState(false);
  function update<K extends keyof OfficeDraft>(key: K, value: OfficeDraft[K]) { setDraft((current) => ({ ...current, [key]: value })); setPreview(false); }
  function showPreview(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setPreview(true); }

  return <RequestFrame active="a">
    <div className={styles.eyebrow}>BUSINESS A / 办公与开发设备</div>
    <div className={styles.formIntro}><div><h1>采购什么设备？</h1><p>先写清使用场景和必要配置，后续才能核对现有资产、目录型号与有效报价。</p></div><span className={`${styles.moduleBadge} ${styles.badgeA}`}>A 类需求</span></div>
    <div className={styles.progress}><span className={styles.progressCurrent}>01 填写需求</span><span>02 核对资产与报价</span><span>03 审批与采购</span></div>
    <div className={styles.formGrid}><form className={styles.formCard} onSubmit={showPreview}>
      <section className={styles.fieldSection}><div className={styles.sectionTitle}><span>01</span><div><h2>设备与用途</h2><p>选择设备类别，说明具体工作需要。</p></div></div>
        <div className={styles.fieldsTwo}><label className={styles.field}>设备类别 <span>必填</span><select required value={draft.category} onChange={(event) => update("category", event.target.value)}><option value="">请选择设备</option><option>笔记本电脑</option><option>开发工作站</option><option>显示器</option><option>SSD</option><option>内存</option><option>其他办公或开发设备</option></select></label><label className={styles.field}>数量 <span>必填</span><input required min="1" step="1" type="number" value={draft.quantity} onChange={(event) => update("quantity", event.target.value)} /></label></div>
        <label className={styles.field}>采购用途 <span>必填</span><textarea required rows={4} placeholder="例如：新员工开发环境，需同时运行 IDE、容器和本地测试" value={draft.purpose} onChange={(event) => update("purpose", event.target.value)} /></label>
        <label className={styles.field}>必要规格 <small>选填</small><textarea rows={3} placeholder="写必须满足的条件；尚不确定的型号可留空" value={draft.specification} onChange={(event) => update("specification", event.target.value)} /></label>
      </section>
      <section className={styles.fieldSection}><div className={styles.sectionTitle}><span>02</span><div><h2>预算与交付</h2><p>可先留空未知项，后续补齐。</p></div></div>
        <div className={styles.fieldsTwo}><label className={styles.field}>预算上限（人民币） <small>选填</small><input type="number" min="0" step="0.01" placeholder="例如 15000" value={draft.budget} onChange={(event) => update("budget", event.target.value)} /></label><label className={styles.field}>期望到货日期 <small>选填</small><input type="date" value={draft.neededBy} onChange={(event) => update("neededBy", event.target.value)} /></label></div>
        <label className={styles.field}>交付地点 <small>选填</small><input placeholder="例如：上海办公室 / 张江园区" value={draft.deliveryLocation} onChange={(event) => update("deliveryLocation", event.target.value)} /></label>
        <label className={styles.field}>现有设备或资产情况 <small>选填</small><textarea rows={3} placeholder="例如：现有电脑是否可升级内存、复用显示器" value={draft.existingAsset} onChange={(event) => update("existingAsset", event.target.value)} /></label>
      </section>
      <div className={styles.formActions}><Link href="/requests/new">返回选择类型</Link><button type="submit" className={styles.primaryButton}>预览需求 <span aria-hidden="true">→</span></button></div>
    </form><aside className={styles.summaryCard}><div className={styles.summaryTop}><span className={styles.eyebrow}>REQUEST BRIEF</span><h2>需求摘要</h2><p>填写时同步整理，预览只在当前页面生效。</p></div><dl className={styles.summaryList}><div><dt>采购类别</dt><dd>{draft.category || "待选择"}</dd></div><div><dt>设备数量</dt><dd>{draft.quantity ? `${draft.quantity} 件` : "待填写"}</dd></div><div><dt>预算上限</dt><dd>{draft.budget ? `¥ ${Number(draft.budget).toLocaleString("zh-CN")}` : "待确认"}</dd></div><div><dt>期望到货</dt><dd>{draft.neededBy || "待确认"}</dd></div></dl>{preview && <div className={styles.previewBox} role="status"><strong>页面预览已生成</strong><p>{draft.purpose}</p>{draft.specification && <p>必要规格：{draft.specification}</p>}<small>尚未保存到数据库，也未发起审批。</small></div>}<p className={styles.summaryNote}>下一阶段接通后端时，资产查询、目录与报价比较会基于已保存的需求进行。</p></aside></div>
  </RequestFrame>;
}
