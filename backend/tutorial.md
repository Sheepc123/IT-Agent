# Backend 源码导学：从请求到结论

本文件依据当前工作区源码、基准提交 `4868200` 编写；工作区有尚未提交的 B 业务代码与文档解析改动，故以实际文件内容为准。学习目标是能自己追踪一次 API 请求、一次技术文档调查和一次评测，知道每一步的输入、函数、数据库读写与停止条件。

前置知识：Python 类型标注与 dataclass、FastAPI 依赖注入、SQLAlchemy Session、PostgreSQL 外键与事务、HTTP JSON、LangGraph 的节点/边。本文覆盖 `backend/` 内所有应用源码、测试和配置；仓库外的迁移文件、前端、PDF 与种子 JSON 仅解释其接口，不逐文件展开。本文不代表真实模型或硬件兼容性已验证。

## 先看全貌

当前有两条相互独立的主链：

```text
A：HTTP 表单 → 身份/范围检查 → 需求与资产/报价查询 → 纯规则计算 → TaskRun 快照 → HTTP 推荐结果
B：PDF 与目录 → 解析并入库 → 检索/读取证据 → Agent 调用模型与只读工具 → 结论
评测：同一道题 → 固定 Top-5 RAG 与 Agent 分别运行 → 确定性比较预期 → JSON 结果
```

`app.py` 只注册了 A 的请求路由；B 目前通过 `documents`、`evidence`、`evaluate` 命令行入口使用。A 不调用模型，B 的 `evaluate check/ask/run` 会调用真实模型。Agent 的状态只在本次 Python 调用的内存中，不会写入 `TaskRun`。数据库里存在 `PlanVersion` 等表，不等于审批、下单、组合优化接口已经实现。

## 01 服务如何启动，一次采购请求如何落库（已展开）

要能回答：用户提交一张 A 业务采购表单，程序在哪些函数里校验身份、创建草稿、提交需求？

主链：`it-procurement-api` → `__init__.main` → `app.app` → `requests_api.create_request` → `current_actor` → `PurchaseRequest/RequestRevision/RequestLine/BusinessEvent` → `submit_request`。

1. `backend/pyproject.toml` 把脚本 `it-procurement-api` 指向 `it_procurement_api:main`。`__init__.py:main()` 从 `settings.get_settings()` 取主机和端口，并用 Uvicorn 加载 `it_procurement_api.app:app`。
2. `settings.py:Settings` 从 `infra/.env` 和进程环境读取 PostgreSQL、API、DeepSeek 参数；`database_url` 拼出 `postgresql+psycopg` URL，`get_settings()` 缓存实例。密码与 API key 用 `SecretStr` 保存。`db/__init__.py:get_engine()` 缓存带 `pool_pre_ping`、3 秒连接超时的 Engine；`new_session()` 创建 SQLAlchemy Session。
3. `app.py` 注册 `ApiError` 的响应处理器和 `requests_api.router`。`live()` 只证明进程可答复；`ready()` 执行 `SELECT 1`，数据库不可用时返回 503。两个健康端点的含义不同。
4. `identity.py:get_session()` 为请求打开并在结束时关闭 Session。`current_actor(session, x_demo_principal)` 从 `X-Demo-Principal` 头查询启用的演示用户和当前有效、未撤销的 `RoleGrant`，产生 `Actor(id,key,tenant_id,department_id,grants)`。`Actor.has(role,scope_id)` 检查指定范围权限，`scopes(role)` 列出该角色的范围。这个头由调用方自行填写，**不是生产认证**。
5. `schemas.py:MinimumSpecs` 要求正整数内存 GiB 与存储字节数；`RequestDraft` 只允许业务 A，允许草稿字段缺失，拒绝额外字段；`missing_fields(draft)` 列出提交时仍缺失的字段，`iso(moment)` 用于响应时间格式。设计上把“允许保存草稿”与“允许提交”分开，未知值保持 `null`。
6. `requests_api.create_request(draft,session,actor)` 先要求 `REQUESTER` 范围和部门，再写主单、第一版需求、单条需求行，并通过 `record_event` 写 `REQUEST_CREATED`，最后一次提交事务。`request_view` 把数据和缺失字段整理给前端；`envelope` 加请求 ID 与服务时间。`get_request` 经 `load_request` 查出可见主单；`can_see` 允许本人或该范围 `BUYER` 查看，无权与不存在都用 `not_found()` 返回 404。
7. `submit_request(request_id,session,actor)` 只允许本人提交 `DRAFT`；从 `current_draft` 还原当前需求，调用 `missing_fields`。缺字段返回 422，状态不变；完整则改为 `SUBMITTED`、增加 `row_version`、写 `REQUEST_SUBMITTED` 事件并提交。

实际边界：`errors.py:ApiError(status,code,message,details,retryable)` 与 `handle_api_error` 统一业务错误格式。`clock.py:now()` 提供 UTC 时间，`frozen(moment)` 供测试固定时间。当前没有编辑草稿的端点，`row_version` 字段尚未形成 `If-Match` 并发控制；`Idempotency-Key` 也未实现。验证可读 `tests/test_seed_a.py` 的身份、缺字段和提交场景，`tests/test_health.py` 的健康端点测试。下一课接着解释分析；B 文档暂缓到第 03 课。

## 02 推荐为什么是确定性计算（已展开）

要能回答：现有设备和报价怎样变成最多三个候选，为什么不能用模型算金额？

主链：`POST /requests/{id}/analysis-runs` → `start_analysis` → `load_assets/load_quotes` → `analysis.analyze` → `to_snapshot` → `TaskRun` → `GET /runs/{id}/recommendations`。

1. `requests_api.start_analysis(request_id,session,actor)` 只接受 `SUBMITTED` 请求；把当前需求转成 `analysis.Need(quantity,min_memory_gib,min_storage_bytes,budget_minor,required_by,currency)`。`load_assets` 只读需求范围的资产，并以资产的 `config_snapshot` 为当前配置；`load_quotes` 联结报价版本、报价行、SKU、供应商，只收活跃且已标记 `VERIFIED` 的 SKU，转换成 `QuoteInfo`。这里的 `VERIFIED` 是目录字段，不是实时商城验证。
2. `analysis.py:meets_spec(memory_gib,storage_bytes,need)` 比较最低配置，未知规格不算满足。`match_assets(need,assets)` 按资产编号排序，只复用 `DEVICE`、`AVAILABLE`、规格合格的资产，返回复用编号和排除原因；最多复用需求数量。
3. `analyze(need,assets,quotes,now)` 先算 `gap = quantity - 已复用数量`；缺口为 0 就无需购买。再筛符合最低规格的报价，逐个交给 `classify_quote(quote,need,gap,now)`：仅接受有效区间 `[valid_from, valid_until)`、相同币种、含税、已知价格/运费/库存/交期、库存不少于缺口、按时交付、总价不超预算的报价。总价公式是 `unit_price_minor × gap + shipping_minor`，金额单位是“分”的整数。
4. `classify_quote` 返回 `item`、`excluded` 或 `pending` 加原因。未知值进 `pending`，不会当 0。`analyze` 按到货总价、交期、报价键排序，仅取前三；没有候选时区分 `NO_MATCH`、`NEEDS_QUOTE`、`NEEDS_INFO`。`to_snapshot(result,need,now)` 把时间转 ISO 字符串、金额转整数字符串，并写入规则版本、数据时间和 `SYNTHETIC/SIMULATED_STORE` 标签。
5. `start_analysis` 同步完成运算，写 `TaskRun(mode=WORKFLOW,status=SUCCEEDED,input_kind=FORM,result_snapshot=...)` 并提交；HTTP 返回 202 和结果 URL 是预留异步接口形状，当前没有后台队列。`get_recommendations` 再次按请求可见性检查，返回持久化快照；`SUCCEEDED` 只表示分析完成，绝非采购获批或已下单。

为什么这样设计：金额、有效期、库存、权限是可以逐项复算的规则，应当由代码与数据库约束执行；模型不能替代这些判断。`tests/test_analysis.py` 精确测边界时刻、1 分预算差、库存等于缺口、排序和排除理由；`tests/test_seed_a.py` 从 HTTP 入口测种子数据场景。A 的报价是模拟数据，不会联网找商店。数据库细节见第 05 课；B 的模型循环见第 04 课。

## 03 技术 PDF 如何变成可引用证据（已展开）

要能回答：Agent 检索到的一段文字从哪个 PDF、哪一页来，表格为什么可能读错？

主链：`documents.main ingest` → `catalog_entries` → `ingest_document` → `parse_pdf/extract_page_chunks` → `DocumentVersion/DocumentChunk` → `evidence.search_documents/read_evidence`。

1. `documents.py` 的 `B0_PATHS` 列出首批九份指南；`catalog_entries()` 读 `data/raw/manuals/catalog.json`。`ingest_document(session,entry,max_pages)` 检查 PDF 存在且 SHA-256 与清单一致，再建立或重用 `DocumentVersion`，删除该版本旧片段，重新解析并写 `DocumentChunk`。调用者负责提交事务；CLI 每份文档提交一次。
2. `parse_pdf(path,max_pages)` 用 pdfplumber 按页迭代并释放页缓存。`extract_page_chunks(page_no,page)` 将一页拆成 `TEXT` 与若干 `TABLE` 片段。`clean_cell`、`table_to_chunk`、`page_chunks` 负责清洗和编号；`is_page_background` 过滤会干扰网格检测的大白底；`chars_in_box` 按字符中心归属单元格；`extract_oriented_text` 修复 90 度旋转文字；`extract_table_rows` 按几何跨度合并双层表头，空格仍为空，不臆造支持值。邻接脚注作为“需核适用性”的原文保留；不自动合并跨页表格或做 OCR。解析版本为 `pdfplumber-page-table-v2`。
3. `find_document(session,document_key)` 找活动文档；`page_of` 读指定页；`search_chunks` 是简单的全部词项包含过滤，`matching_lines` 只生成预览。`documents.main` 提供 `ingest/list/show/search` 四个命令。这里的简单检索与下面 Agent 用的加权检索不是同一函数。
4. `evidence.py:query_terms(query,max_terms=12)` 提取英文词和料号；`term_weight` 对长料号给更高分，`term_matches` 要求代码整词命中，`score_chunk` 累加。`search_documents(session,query,document_key,limit=5,one_per_page=True)` 先在数据库找含任一词的片段，再在 Python 中算分，表格同分优先且默认每页一个结果。它是加权关键词检索，**不是向量 RAG，也不是 BM25**。
5. `format_ref/parse_ref` 管理 `<document_key>:p<page>:<ordinal>`；`read_evidence(session,ref)` 按引用读取全文、来源 URL、修订、内容哈希、解析版本，返回 `PARSED_NOT_HUMAN_CHECKED` 标签。引用序号可能随重新解析变化，必须连版本和哈希一起保存。`list_documents` 供选择指南，`baseline_context` 做固定的一次检索并截断 Top-N 内容。
6. `load_questions` 读 `evals/b1_questions.json`；`probe_pages/check_probe/baseline_recall/run_probes` 对题目里的预期页做确定性检索检查；`evidence.main` 提供 `search/read/probe`。探针通过仅证明片段中能找到词或页，不证明兼容性结论正确。

验证证据：`tests/test_documents.py` 覆盖真实 PDF 的 SSD、GPU、内存支持矩阵、旋转表头、脚注及重复入库；`tests/test_evidence.py` 覆盖料号精确匹配、排序、引用与探针。解析产物仍可能遗漏或错配文字，尤其脚注与跨页表格；原页与领域专家复核不能省略。模型使用这些证据的方式在第 04 课。

## 04 Agent、模型客户端与评测怎么接起来（已展开）

要能回答：哪一步真正调用大模型，模型能调用哪些工具，如何停下来，评测比较的是什么？

主链：`evaluate.main ask/run` → `client_from_settings` → `agent.start_investigation` → LangGraph `call_model ↔ run_tools` → `DeepSeekClient.complete` → `search_documents/read_evidence` → `submit_findings` → `score_run/save_results`。

1. `agent/contracts.py:Limits` 默认最多 12 次模型请求、30 次工具请求、累计 200000 个已报告 token、2 次提问、连续 2 次无效输出、连续 3 步无新信息；单个工具结果最多 4000 字符。`ToolCall(id,name,arguments)`、`ModelReply(text,tool_calls,usage)` 是模型交互的数据形状；`ModelClient.complete(messages,tools)` 是接口；`ScriptedModel.complete` 按预设答复顺序返回，专供无真实费用的测试。
2. `agent/tool_catalog.py:TOOL_SPECS` 只开放五个工具：`list_documents` 列目录、`search_documents` 找片段、`read_evidence` 读原文、`ask_user` 暂停待答、`submit_findings` 交最终结论。`tool_definitions` 形成模型可见的描述；`validate_args(parameters,args)` 校验必要字段和基本类型；`check_findings(findings,read_refs)` 校验结构与引用，PASS/FAIL 无引用则降为 INSUFFICIENT。它只检查“引用是否在本轮读过”，**不验证原文是否真的支持结论**。
3. `agent/runner.py:initial_state(question)` 放入系统提示与用户问题，并初始化计数、已读引用、缓存、事件。`build_graph(model,session,limits)` 创建两个节点：`call_model` 先检查调用次数与已报告 token，再请求 `model.complete`；纯文本不能结束，模型须调用工具。`run_tools` 串行执行调用，检查工具名与参数，重复调用直接复用 `call_cache`，记录搜索命中和已读引用；无进展、错误次数或预算到顶便停止。`shrink(result,max_chars)` 裁剪工具返回，`tool_message` 构造工具回复，`_event` 记录轨迹。
4. `ask_user` 产生 `WAITING_INPUT`，`resume_investigation(session,model,state,answer,limits)` 把回答补到消息并延续原计数；`submit_findings` 产生 `SUCCEEDED`。`_run` 调用已编译图；`start_investigation(session,model,question,limits)` 从头执行。没有 LangGraph Checkpointer、状态落库、时间上限或并行工具；进程退出后不能仅凭数据库恢复这次调查。Token 上限是在下一次请求前检查已报告用量，单次请求可能跨过阈值，未知用量会记录为 unknown 而不当成零。
5. `model_client.py:http_post_json(url,headers,body,timeout)` 用 urllib POST JSON，失败转成 `ModelError`，不自动重试；`to_api_messages` 和 `to_api_tools` 把内部消息/工具转为 Chat Completions 形状；`parse_reply(data)` 把返回的文本、工具调用、用量变为 `ModelReply`。`DeepSeekClient.complete(messages,tools)` 发送一次请求；`client_from_settings()` 无 key 即拒绝；`check_model(client)` 用一次真实小请求检查工具调用、参数和用量字段。该 API 形状仍需真实连通验证，替身测试不能替代。
6. `evaluate.py:task_prompt(question)` 将预期约束编号 C1、C2 注入题面。`run_baseline` 用一次固定 Top-5 检索和一次模型请求；`run_agent` 运行调查图，评测中若要问用户，固定回复“无人可答”。`evaluate_questions` 对同一批题分别执行两模式；`score_run` 按约束编号比较预期的 PASS/FAIL/INSUFFICIENT，并统计预期页是否被引用；`summarize` 汇总正确数、成功数、调用数和 token；`save_results` 将配置、记录写到 `data/processed/eval_runs`。`main` 的 `check`、`ask`、`run` **均会调用真实模型并消耗 token**。

CHG-0034 将旧 `agent.py` 拆成 `agent/contracts.py`（模型与状态类型）、`agent/tool_catalog.py`（五工具目录和参数检查）、`agent/runner.py`（原两节点图）。`agent/__init__.py` 按需导出旧入口；`model_client.py` 直接依赖契约和工具目录，不再加载图。此拆分未新增生产工作流。

评测的限制：编号已给模型，不能证明模型自行发现约束；得分只比标签与引用页，不做人类语义蕴含判定；金标准目前来自解析片段，不代表硬件实测或专家审定。`tests/test_agent.py` 用 ScriptedModel 测工具边界与停止条件；`tests/test_model_client.py` 用假 transport 测协议转换；`tests/test_evaluate.py` 用替身测比较流程和 JSON 结果。本项目现有测试未证明 DeepSeek 真正调用成功，更未证明 Agent 比固定 RAG 效果更好。

## 05 数据表、种子数据与测试如何保证可追溯（已展开）

要能回答：数据从哪里来，哪些限制由数据库保证，如何区分“表已建”与“功能已做”？

主链：`database/fixtures/*.json` → `fixtures.load_fixture_set` → SQLAlchemy 模型 → A 分析；`data/raw/manuals/*.pdf` → `documents.ingest_document` → 文档表 → B 调查；`tests/conftest.py` → 回滚事务 → 用例。

1. `db/models.py:Base` 设统一约束命名；`Row` 为每表提供 id、租户、创建时间/人员；`Mutable` 加版本和更新时间。辅助函数 `_uuid/_ts/_money/_json` 统一列类型，`fk(column,target)` 建 `(tenant_id,外键)` 复合外键，`tenant_key()` 提供复合唯一键，`one_of` 和 `non_negative` 生成数据库 CHECK。金额统一整数分，未知为 NULL，时间带时区。
2. 身份表：`Principal` 是用户/服务身份，`AccessScope` 是个人/任务/部门/组织范围，`RoleGrant` 是角色加范围和有效期；未撤销的同类授权不能重复。主数据表：`SourceBatch` 记录来源批次/哈希/质量，`ProductModel` 是型号，`Sku` 是具体 SKU 与规格，`Asset` 是企业资产及当前配置，`Supplier` 是供应商，`QuoteVersion` 与 `QuoteLine` 是版本化报价和明细。复合外键阻断跨租户关联；资产号等字段有租户内唯一性。
3. 需求与计划表：`PurchaseRequest` 主单，`RequestRevision` 保留版本化需求，`RequestLine` 是需求项；`PlanVersion`、`PlanLine` 预留复用/升级/购买方案，数据库限制购买行要有报价、总价与数量可复算。`BusinessEvent` 为审计事件，数据库迁移有禁止 UPDATE/DELETE 的触发器。`TaskRun` 存分析运行和结果快照；当前 API 只写 A 的 `WORKFLOW` 结果，B Agent 没有写它。`DocumentVersion` 和 `DocumentChunk` 存 PDF 来源、版本、页码、片段及哈希。
4. `fixtures.py:fixture_id(kind,key)` 用固定 UUID 命名空间生成稳定 ID；`read_sets/read_part` 读种子集合和数据并算 SHA-256；`validate(parts,expect)` 检查重复键、引用、非负金额、报价有效期、预期数量与总价。`reset_tenant(session)` 清空演示租户的种子/业务表，但保留真实 PDF 派生的文档表；它暂时关闭审计事件防删触发器以完成演示数据重置，故只应在开发/测试环境使用。`_insert` 写身份、权限、型号、SKU、资产、供应商、模拟店铺报价；`load_fixture_set(session,name)` 先验证再重置与插入，由调用者提交或回滚；`loaded_counts` 返回各表计数；`main` 是命令行加载入口。所有报价和资产种子都是合成的。
5. `tests/conftest.py:session` 用 PostgreSQL 外层事务加 savepoint，让每个用例即使内部 `commit()` 最终也回滚；`client` 用 FastAPI TestClient 并替换 `get_session`，`docs` 安装测试文档。`tests/sample_docs.py:add_sample_docs` 写两份虚构指南，以料号和长文本检索为可控样本。
6. 测试文件职责：`test_health.py` 验证健康端点；`test_schema.py` 验证迁移头、约束、跨租户、唯一性、事件不可改；`test_fixtures.py` 验证种子质量与重复加载；`test_analysis.py` 验证 A 纯规则；`test_seed_a.py` 验证 A 的 API、角色和 A01/A02/A03/A06 场景；`test_documents.py` 验证 PDF 解析和入库；`test_evidence.py` 验证检索、引用、问题探针；`test_agent.py` 验证循环与限制；`test_model_client.py` 验证请求/响应形状；`test_evaluate.py` 验证对照与得分。它们分别测规则、数据库和替身模型，不能相互冒充。
7. `backend/alembic.ini` 把迁移目录指向仓库级 `database/migrations`；注释提到旧的 `.config`，实际配置文件是 `settings.py`，注释已过时。`.python-version` 指定 3.12；`pyproject.toml` 声明 FastAPI、SQLAlchemy、psycopg、Alembic、pdfplumber、LangGraph、Uvicorn 与测试依赖；`uv.lock` 锁定依赖解析结果。`uv.lock` 不包含业务逻辑。

已有验证记录：`scripts/CHANGELOG.md` 的 CHG-0029 记录重建九份指南后的全量 `107 passed`，且注明未调用真实大模型。本导学仅重新核对源码与记录，没有把旧测试结果写成此次新运行。

## 自己复述一次

用自己的话分别追踪：一张完整 A 表单如何从 `create_request` 到 `get_recommendations`；一条 B 兼容性问题如何从 `evaluate ask` 到 `read_evidence`、`submit_findings`。每一步说出输入、输出、是否写数据库、是否调用模型，以及不能保证什么。遇到单个函数看不懂，可以沿上面的文件与行号继续追问。
