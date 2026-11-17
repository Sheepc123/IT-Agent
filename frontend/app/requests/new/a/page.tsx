"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { RequestFrame } from "../components/RequestFrame";
import shared from "../request.module.css";
import styles from "./office.module.css";

type OfficeDraft = {
  category: string;
  quantity: string;
  purpose: string;
  specification: string;
  budget: string;
  neededBy: string;
  deliveryLocation: string;
  existingAsset: string;
};

type EntryMode = "manual" | "assistant";

const emptyDraft: OfficeDraft = {
  category: "",
  quantity: "",
  purpose: "",
  specification: "",
  budget: "",
  neededBy: "",
  deliveryLocation: "",
  existingAsset: "",
};

/** Present the manual intake flow and the planned AI-assisted entry point. */
export default function OfficeRequestPage() {
  const [mode, setMode] = useState<EntryMode>("manual");
  const [sourceText, setSourceText] = useState("");
  const [draft, setDraft] = useState<OfficeDraft>(emptyDraft);
  const [preview, setPreview] = useState(false);
  const [notice, setNotice] = useState("");

  function update<K extends keyof OfficeDraft>(key: K, value: OfficeDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setPreview(false);
  }

  /** Preserve existing purpose text when the requester copies raw input into the form. */
  function carryTextToForm() {
    const trimmed = sourceText.trim();
    if (!trimmed) return;
    setDraft((current) => ({
      ...current,
      purpose: [current.purpose.trim(), trimmed].filter(Boolean).join("\n"),
    }));
    setPreview(false);
    setMode("manual");
    setNotice("原文已放入采购用途。设备、数量、预算等字段仍需你手动填写。");
  }

  function showPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPreview(true);
    setNotice("请在右侧核对本页预览；当前内容尚未保存到数据库。");
  }

  const budget = draft.budget === "" ? null : Number(draft.budget);
  const budgetLabel = budget !== null && Number.isFinite(budget)
    ? `¥ ${budget.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "待确认";

  return <RequestFrame active="a">
    <div className={shared.eyebrow}>BUSINESS A / 办公与开发设备</div>
    <div className={shared.formIntro}>
      <div>
        <h1>把采购需求说清楚。</h1>
        <p>你可以直接填写，也可以先粘贴一段描述。最终的设备、数量、预算和交付要求由申请人核对。</p>
      </div>
      <span className={`${shared.moduleBadge} ${shared.badgeA}`}>A 类需求</span>
    </div>
    <div className={shared.progress} aria-label="采购流程">
      <span className={shared.progressCurrent}>01 整理申请</span>
      <span>02 核对资产与报价</span>
      <span>03 审批与采购</span>
    </div>

    <div className={styles.intakeGrid}>
      <div className={styles.intakeMain}>
        <section className={styles.methodCard} aria-labelledby="entry-heading">
          <div className={styles.methodHeader}>
            <div>
              <span className={shared.eyebrow}>HOW TO START / 填写方式</span>
              <h2 id="entry-heading">从你方便的方式开始</h2>
            </div>
            <span className={styles.localPill}>页面内编辑</span>
          </div>
          <div className={styles.methodSwitch} role="group" aria-label="填写方式">
            <button type="button" aria-pressed={mode === "manual"} className={mode === "manual" ? styles.methodActive : ""} onClick={() => { setMode("manual"); setNotice(""); }}>手动填写 <span>逐项录入</span></button>
            <button type="button" aria-pressed={mode === "assistant"} className={mode === "assistant" ? styles.methodActive : ""} onClick={() => { setMode("assistant"); setNotice(""); }}>粘贴文字 <span>助手入口</span></button>
          </div>
          {mode === "assistant" && <div className={styles.assistantArea}>
            <div className={styles.assistantIntro}><span className={styles.assistantMark} aria-hidden="true">✳</span><div><strong>采购填写助手</strong><p>将邮件或聊天中的采购描述粘贴进来。模型解析接口接通后，会提取字段供你逐项修改。</p></div></div>
            <label htmlFor="office-source" className={styles.sourceLabel}>原始采购描述</label>
            <textarea id="office-source" className={styles.sourceInput} rows={7} maxLength={2000} placeholder="例如：为新入职的三位开发人员采购笔记本，主要运行 IDE 和容器；总预算 4.5 万元，希望月底前送到上海办公室。" value={sourceText} onChange={(event) => setSourceText(event.target.value)} />
            <div className={styles.sourceMeta}><span>可输入最多 2,000 字。原文只保留在当前页面。</span><span>{sourceText.length} / 2,000</span></div>
            <div className={styles.assistantActions}>
              <button type="button" className={styles.generateDisabled} disabled aria-describedby="assistant-status">自动生成申请草稿 <span aria-hidden="true">↗</span></button>
              <button type="button" className={styles.textAction} disabled={!sourceText.trim()} onClick={carryTextToForm}>把原文放入用途并继续手填</button>
            </div>
            <p id="assistant-status" className={styles.pendingNote}>模型解析接口尚未接通，因此“自动生成”暂不可用；此页不会假装已经识别设备、预算或型号。</p>
          </div>}
        </section>

        {notice && <p className={styles.inlineNotice} role="status">{notice}</p>}
        {mode === "manual" && <form className={shared.formCard} onSubmit={showPreview}>
          <section className={shared.fieldSection}>
            <div className={shared.sectionTitle}><span>01</span><div><h2>设备与用途</h2><p>写明需要什么、多少件，以及为何需要。</p></div></div>
            <div className={shared.fieldsTwo}>
              <label className={shared.field}>设备类别 <span>必填</span><select required value={draft.category} onChange={(event) => update("category", event.target.value)}><option value="">请选择设备</option><option>笔记本电脑</option><option>开发工作站</option><option>显示器</option><option>SSD</option><option>内存</option><option>其他办公或开发设备</option></select></label>
              <label className={shared.field}>数量 <span>必填</span><input required min="1" step="1" type="number" placeholder="例如 3" value={draft.quantity} onChange={(event) => update("quantity", event.target.value)} /></label>
            </div>
            <label className={shared.field}>采购用途 <span>必填</span><textarea required rows={4} placeholder="例如：为新员工建立开发环境，需运行 IDE、容器和本地测试" value={draft.purpose} onChange={(event) => update("purpose", event.target.value)} /></label>
            <label className={shared.field}>必要规格 <small>选填</small><textarea rows={3} placeholder="写必须满足的条件；型号不确定时可留空" value={draft.specification} onChange={(event) => update("specification", event.target.value)} /></label>
          </section>
          <section className={shared.fieldSection}>
            <div className={shared.sectionTitle}><span>02</span><div><h2>预算与交付</h2><p>未知项可以留空；预算请填写本次采购的总额。</p></div></div>
            <div className={shared.fieldsTwo}>
              <label className={shared.field}>总预算上限（人民币） <small>选填</small><input type="number" min="0" step="0.01" placeholder="例如 45000" value={draft.budget} onChange={(event) => update("budget", event.target.value)} /></label>
              <label className={shared.field}>期望到货日期 <small>选填</small><input type="date" value={draft.neededBy} onChange={(event) => update("neededBy", event.target.value)} /></label>
            </div>
            <label className={shared.field}>交付地点 <small>选填</small><input placeholder="例如：上海办公室 / 张江园区" value={draft.deliveryLocation} onChange={(event) => update("deliveryLocation", event.target.value)} /></label>
            <label className={shared.field}>现有设备或资产情况 <small>选填</small><textarea rows={3} placeholder="例如：现有电脑能否升级内存，显示器是否可复用" value={draft.existingAsset} onChange={(event) => update("existingAsset", event.target.value)} /></label>
          </section>
          <div className={shared.formActions}><Link href="/requests/new">返回选择类型</Link><button type="submit" className={shared.primaryButton}>预览申请草稿 <span aria-hidden="true">→</span></button></div>
        </form>}
      </div>

      <aside className={`${shared.summaryCard} ${styles.summary}`} aria-label="申请草稿摘要">
        <div className={shared.summaryTop}><span className={shared.eyebrow}>REQUEST BRIEF</span><h2>申请草稿</h2><p>这里只显示你已经填写的内容；空白项不会被自动猜测。</p></div>
        <dl className={shared.summaryList}>
          <div><dt>设备类别</dt><dd>{draft.category || "待选择"}</dd></div>
          <div><dt>数量</dt><dd>{draft.quantity ? `${draft.quantity} 件` : "待填写"}</dd></div>
          <div><dt>总预算上限</dt><dd>{budgetLabel}</dd></div>
          <div><dt>期望到货</dt><dd>{draft.neededBy || "待确认"}</dd></div>
          <div><dt>交付地点</dt><dd>{draft.deliveryLocation || "待填写"}</dd></div>
        </dl>
        {preview && <div className={shared.previewBox} role="status"><strong>当前页面的申请预览</strong><p>采购用途：{draft.purpose}</p>{draft.specification && <p>必要规格：{draft.specification}</p>}{draft.existingAsset && <p>现有设备：{draft.existingAsset}</p>}<small>未保存到数据库，也未发起审批。</small></div>}
        <div className={styles.summaryBoundary}><strong>当前进度</strong><p>手动表单与文字输入可用；模型解析、数据库保存和资产报价查询仍待接入。</p></div>
      </aside>
    </div>
  </RequestFrame>;
}
