# 企业 IT 资产管理与采购决策 Agent 平台

以 AI 研发基础设施采购（业务 B）为主线，建设可恢复、可审计、可扩展的 Agent 平台；办公设备采购（业务 A）保留确定性分析及共享采购流程。

采用数据中台、RAG 和评测中台作为共享底座，使用确定性 Workflow 控制业务流程，在复杂分析中引入有限范围的 Agent 调查。

## 当前状态

已有本地前后端基础、PostgreSQL 迁移、A 确定性分析、PDF 解析/词项检索、B 的 LangGraph 内存循环及局部评测工具。现有基础词项 RAG 不等于目标混合 RAG；DSPy 尚未接入。Agent 模型契约、工具目录与旧运行器已完成第一批结构拆分；业务 B 核心数据与前后端最小载荷已有 `p17-v1` JSON Schema。业务 B 路由、业务图和持久化仍未实现。CHG-0029 记录了 9 份指南重建和 107 项测试通过，这是历史验证，不能视为生产架构已完成。

当前目标为生产级业务 B 系统：LangGraph＋Temporal、PostgreSQL＋Milvus＋对象存储、权限/预算/恢复/审计、混合及多模态取证。上述组件组合和 HA 尚未落地验收。最新需求、架构与工作流见下方三个主入口；CHG-0038 已确认完整 A/B 前端及文档/证据关系图设计；CHG-0046 已将 `/login` 与 `/dashboard` 接入本地数据库应用账号、可撤销会话和后端只读需求数据；其余页面逐页建设，P17/P18 契约和服务继续接入。

## 目录结构

```text
Agent/
├── backend/                    后端服务（Python、FastAPI、uv）
│   ├── src/it_procurement_api/
│   │   ├── app.py              API 入口与健康检查
│   │   ├── auth/               身份解析、本地登录、密码处理和开发账号初始化
│   │   ├── agent/              B 原型：模型契约、工具目录和 LangGraph 运行器
│   │   ├── business_b/         B 核心、API 与工具输入 Schema 及导出器
│   │   ├── model_client.py     DeepSeek 协议适配
│   │   ├── settings.py         配置，读取 infra/.env
│   │   ├── fixtures.py         合成数据的质量检查与加载
│   │   └── db/                 数据库引擎、会话与表模型
│   ├── tests/                  约束、健康检查与 fixture 测试
│   ├── alembic.ini             迁移工具配置，指向 database/migrations
│   └── pyproject.toml、uv.lock  依赖声明与锁定
├── frontend/                   前端工作台（Next.js、TypeScript、pnpm）；contracts/ 为共享 Schema
├── database/
│   ├── migrations/             Alembic 迁移脚本，versions/ 下按编号排列
│   └── fixtures/               合成演示数据（JSON），全部为 synthetic
├── evals/                      评测题目与预期（不得进入检索库或模型上下文）
├── infra/                      本地依赖：compose.yaml；.env 不入库
├── docs/                       需求、设计、计划与评测规格（不入库）
├── scripts/                    变更日志与辅助脚本（不入库）
├── data/
│   └── raw/                    未处理的厂商与公开原文资料（不入库）
│       ├── manuals/            32 份联想与 NVIDIA 官方 PDF（原 RAG-docs）及其 catalog.json
│       └── hardware/ software/ workload/   业务 B 补充资料，见 data/raw/README.md
├── AGENTS.md                   项目协作约定
└── README.md
```

迁移脚本放在 `database/`，但依赖后端的表模型，所以迁移命令在 `backend/` 下执行。评测、数据分层、知识接入等目录在对应任务开始时再建，不提前放空目录。

## 本地运行

需要 Docker、uv。数据库配置在 `infra/.env`（不入库）。

```bash
cd infra && docker compose up -d                 # 启动 PostgreSQL，端口 127.0.0.1:5433
cd ../backend
uv sync                                          # 首次或移动目录后，建立虚拟环境
uv run python -m it_procurement_api.business_b.export_schema ../frontend/contracts/business_b.schema.json  # 同步 B 前端契约
uv run alembic upgrade head                      # 应用迁移
uv run pytest -q                                 # 约束、健康检查与 fixture 测试
uv run python -m it_procurement_api.fixtures FX-A-BASE   # 加载合成演示数据（会清空演示租户，包括应用测试账号）
DEV_TEST_PASSWORD='<本地测试密码>' uv run python -m it_procurement_api.auth.seed  # fixture 后创建 admin0 应用账号
uv run python -m it_procurement_api.documents ingest      # 解析首批 9 份厂商指南入库（需 data/raw/manuals/）
uv run python -m it_procurement_api.documents show LP0785_XXV710_DA2_Product_Guide 3   # 查看某页片段，对照原文抽查
uv run python -m it_procurement_api.evidence search "XXV710-DA2 7Z57A03557"   # 加权检索，返回片段引用
uv run python -m it_procurement_api.evidence read LP0785_XXV710_DA2_Product_Guide:p4:1   # 读取一个片段
uv run python -m it_procurement_api.evidence probe         # 用入库文档核对 evals/b1_questions.json
IT_PROCUREMENT_LOCAL_LOGIN=1 uv run uvicorn it_procurement_api.app:app --host 127.0.0.1 --port 8000  # 本地应用登录
```

前端另开终端运行 `cd frontend && pnpm dev --hostname 127.0.0.1`，打开 `http://127.0.0.1:3000/login`。本地应用账号密码不同于 PostgreSQL 连接凭据；企业认证仍未接入。

当前 `.gitignore` 忽略 `docs/`、`scripts/` 和 `data/raw/`，其中需求文档与日志为本地交付，未纳入 Git 跟踪。数据分工：`data/` 放厂商原文及其处理产物，`database/` 放迁移和合成演示数据。解析后的片段只存数据库；`data/processed/` 等有文件产物（如人工核对过的题目）时再建。

首版使用真实技术资料和明确标记的模拟商务数据；型号/兼容依据须核验，报价与订单不冒充真实交易。A 保留零/单次生成模型路径；B 使用有预算的动态调查。生产认证与高可用纳入交付目标，不等于当前本地环境已经具备。

## 文档导航

推荐先读前三份；其余文档维护共享规则、契约和验证依据。

| 文档 | 用途 |
|---|---|
| [业务 B 需求说明书：MRD/PRD](docs/业务B需求说明书.md) | 用户、价值假设、范围、B01～B12、页面、角色和验收 |
| [技术架构与选型](docs/技术架构与选型.md) | 技术栈、取舍、RAG、上下文、工具协议、可靠性和部署 |
| [业务 B Agent 工作流](docs/业务B-Agent实施方案.md) | 调查循环、工具契约、预算、状态、停止/恢复和实施模块 |
| [后端架构审计与重构顺序](docs/后端架构审计与重构顺序.md) | 原型耦合、LangGraph/RAG/DSPy 差距、目标模块和迁移验收 |
| [P17 业务 B 契约与迁移映射](docs/P17-业务B契约与迁移映射.md) | B01～B12 的角色、`p17-v1` 数据/API Schema、函数迁移及未实现边界 |
| [前端设计总览](<docs/frontend design/README.md>) | 已确认的 A/B 页面、角色、接口状态、原文与图谱可视化；当前仅设计 |
| [项目计划大纲](docs/项目计划大纲.md) | 当前任务顺序、状态、依赖和下一步 |
| [项目计划说明书](docs/项目计划说明书.md) | 交付方式、质量门槛、职责与文档分工 |
| [共享业务规则](docs/业务需求规格说明书.md) | 保留 REQ-A/B/X/R/M 编号与业务不变量 |
| [角色权限矩阵](docs/角色权限矩阵.md) | 资源范围、职责分离、工具和记忆权限 |
| [领域模型与数据字典](docs/领域模型与数据字典.md) | 实体、字段、快照与版本；生产差异待 P17 细化 |
| [业务状态机与事务边界](docs/业务状态机与事务边界.md) | 审批、预占、执行、恢复和履约规则 |
| [接口契约与错误码](docs/接口契约与错误码.md) | API 编号、权限、幂等、错误与恢复 |
| [业务验收场景](docs/业务验收场景.md) | 历史 32 个故事及后续扩展边界 |
| [种子评测规格](docs/种子评测规格.md) | 40 条规格、fixture、判分与复核状态 |
| [种子评测用例明细](docs/种子评测用例明细.md) | 每题前置、操作、预期与禁止副作用 |
| [文档解析修复验证](docs/文档解析修复验证.md) | CHG-0029 的真实解析/检索验证及限制 |
| [后端源码导学](backend/tutorial.md) | 已有源码入口、函数、调用链及实现边界 |
| [原始项目方案（历史背景）](scripts/企业IT资产管理与采购决策Agent平台-项目方案.md) | 早期方案与参考材料，当前选型以上述文档为准 |
| [项目协作约定](AGENTS.md) | 项目规则与逐文件交付要求 |
| [scripts 使用说明](scripts/README.md) | 日志约定 |
| [变更日志](scripts/CHANGELOG.md) | 追加的变更事实、历史验证和本批文档迁移说明 |

## 下一项任务

P08 已进入设计阶段，CHG-0038 的页面/API/可视化设计见[前端设计目录](<docs/frontend design/README.md>)；首个 `/dashboard` 工作台已接入只读身份、待办与需求接口，可按 [前端运行说明](frontend/README.md) 联调；其余页面等待用户逐页指定。当前使用显式启用的开发演示身份，生产认证、审核指派、版本和幂等事件仍归 P17/P18，P09/P10 补授权原文与证据。共享审批/执行页随 P07 接入；GraphRAG 真实检索另经 P20 评测。详细进度以计划大纲为准。
