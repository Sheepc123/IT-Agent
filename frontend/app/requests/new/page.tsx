import Link from "next/link";
import { RequestFrame } from "./components/RequestFrame";
import styles from "./request.module.css";

/** Let the requester choose the appropriate business-specific intake form. */
export default function NewRequestPage() {
  return <RequestFrame active="choose">
    <div className={styles.eyebrow}>NEW REQUEST / 新建采购需求</div>
    <div className={styles.pageIntro}><h1>先选采购场景，<br /><em>再说清楚需求。</em></h1><p>办公设备和 AI 基础设施需要核对的信息不同。选择场景后填写对应表单，避免用一张长表处理所有采购。</p></div>
    <div className={styles.choiceGrid}>
      <Link className={`${styles.choiceCard} ${styles.choiceA}`} href="/requests/new/a"><span className={styles.choiceCode}>01 / BUSINESS A</span><span className={styles.choiceIcon}>A</span><h2>办公与开发设备</h2><p>笔记本、开发工作站、显示器、SSD、内存等标准设备。重点说明用途、规格、数量和交付要求。</p><span className={styles.choiceTags}>设备采购 <span>·</span> 资产复用 <span>·</span> 报价比较</span><span className={styles.choiceAction}>填写 A 类需求 <span aria-hidden="true">↗</span></span></Link>
      <Link className={`${styles.choiceCard} ${styles.choiceB}`} href="/requests/new/b"><span className={styles.choiceCode}>02 / BUSINESS B</span><span className={styles.choiceIcon}>B</span><h2>AI 研发基础设施</h2><p>GPU 服务器、存储服务器、交换机和网络设备。先描述工作负载或现有配置，再补充约束与兼容性条件。</p><span className={styles.choiceTags}>工作负载 <span>·</span> 兼容验证 <span>·</span> 组合方案</span><span className={styles.choiceAction}>填写 B 类需求 <span aria-hidden="true">↗</span></span></Link>
    </div>
    <p className={styles.footNote}>当前阶段提供前端填写和预览；需求尚不会保存或提交到数据库。</p>
  </RequestFrame>;
}
