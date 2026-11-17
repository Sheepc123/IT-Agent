# IT 采购协作台前端

使用 Next.js 16、React 19、TypeScript 和 pnpm。页面范围见[设计总览](<../docs/frontend design/README.md>)。

## 本地运行

数据库连接沿用 `infra/.env`。先在 `backend/` 运行迁移并加载 `FX-A-BASE`，然后创建应用测试用户。**fixture 重载会清空演示租户，包括测试账号，重载后需重新执行创建命令。**

```bash
cd /home/sheepc/projects/Agent/backend
uv run alembic upgrade head
uv run python -m it_procurement_api.fixtures FX-A-BASE
DEV_TEST_PASSWORD='<你指定的本地测试密码>' uv run python -m it_procurement_api.auth.seed
IT_PROCUREMENT_LOCAL_LOGIN=1 uv run uvicorn it_procurement_api.app:app --host 127.0.0.1 --port 8000
```

另开终端：

```bash
cd /home/sheepc/projects/Agent/frontend
pnpm install
pnpm dev --hostname 127.0.0.1
```

打开 `http://127.0.0.1:3000/login`，使用应用账号 `admin0` 与创建时指定的密码。后端地址可通过 `BACKEND_API_ORIGIN` 覆盖。数据库连接账号和网页登录账号是两种不同身份；不要修改 `infra/.env` 的数据库密码来登录网页。

## 当前交付与边界

- `/login` 经前端同源路由调用后端 `POST /api/v1/auth/login`，后端验证 PostgreSQL 中 `local_credential` 的加盐哈希；浏览器收到 HttpOnly、SameSite=Strict 的会话 Cookie，不收到后端原始令牌。`/api/workbench/*` 将会话转给后端，后端逐次查询 `local_session`、`principal`、`role_grant`。退出时撤销数据库会话并清除 Cookie。
- `admin0` 在演示租户中授予七类现有角色；角色和范围来自数据库，岗位筛选仅改变视图。拥有多个角色也不能绕过审批职责分离。
- 当前本地密码登录仅在后端显式启用 `IT_PROCUREMENT_LOCAL_LOGIN=1` 且前端为开发模式时可用，不是企业认证。生产 OIDC/SSO、会话管理、限流和审计仍须单独建设。测试用弱密码不得用于生产。
- `/dashboard` 从后端三个只读接口读取当前账号、授权待办和持久化需求。现阶段只有申请人草稿提交及业务 A 采购员分析待办有完整数据来源；审核、收货及业务 B 调查不生成假待办。列表上限 100 条，分页待实现。请求失败不回退到静态数据。
- `/requests/new` 选择 A 办公与开发设备或 B AI 研发基础设施；`/requests/new/a`、`/requests/new/b` 是独立的创建表单。页面读取当前登录账号并检查申请人职责，支持必填校验、B 采购目标切换和页面内需求预览。A 页面可在手动表单与粘贴文字之间切换；粘贴原文可复制到“采购用途”后继续手填。自动解析按钮因模型接口未接通而禁用，**不会自动提取设备、数量或预算**。当前表单只在浏览器内预览：输入不会保存到数据库，也不会触发分析、Agent 调查或审批。

## 验证

```bash
pnpm lint
pnpm build
```

变更见 [CHG-0046](../scripts/CHANGELOG.md#chg-0046)、[CHG-0048](../scripts/CHANGELOG.md#chg-0048) 与 [CHG-0050](../scripts/CHANGELOG.md#chg-0050)。
