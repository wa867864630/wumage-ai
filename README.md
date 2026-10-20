# 校园 AI 一体化平台

后续开发依据：[分阶段闭环交付计划](docs/architecture/closed-loop-delivery-plan.md)、[开发与版本规范](docs/architecture/development-and-versioning.md)，项目级AI开发入口为 [AGENTS.md](AGENTS.md)。先建立可恢复的版本基线，再逐步开放任务引导、表单及请假等完整流程；规划内容不等于已经实现。

应用工作台支持“新建应用 → 选择课程查询模板 → 独立配置暂存 → 试跑发布 → 从微服务广场或对话入口使用”。左侧真实应用列表支持切换，右侧默认基础信息并提供类型对应的配置；问法、版本及审计按应用隔离。当前开放标准/简洁课程查询模板，表单和审批类型待接入。开发验收地址 `http://127.0.0.1:5173/#workbench`，操作及限制见[工作台说明](docs/runbooks/application-management.md)。

校园助手支持先匹配应用再匹配功能：“有哪些应用”“打开某应用”“某应用查明天课表”，锁定后可追问“明天呢”。多个候选先选择，管理员还能通过对话定位指定应用的配置和问法页；管理入口只导航，不自动写入或发布。

锁定后可直接点击查询和配置功能按钮，也可用“搜索应用＋关键词”筛选目录。历史功能按钮始终绑定原实例，连续提交会去重，减少重复请求。

2026-10-20 新增课表问法库：应用工作台可手工维护、AI批量生成、查看成功兜底查询积累的待审核问法；审核加入规则草稿，试跑并发布后直接匹配，减少重复AI调用。支持来源/状态/频次筛选与排序、拒绝/停用/删除、审核审计和版本回滚；首版只支持现有五种相对日期，不能自动启用。新增迁移`0004_question_library`已在本地开发库执行，原草稿及发布保持不变。操作及限制见[模型与问法库说明](docs/runbooks/ai-model-configuration.md)。以下较早的“无需迁移”“不调用AI”说明只适用于对应历史增量。

AI 管理端设计标准已形成 1.0.0 草案，课表专用设计已接入模型生成和提案接受；通用表单/流程/视图仍为待实现契约：[开发蓝图](docs/architecture/ai-application-blueprint.md)、[JSON 标准与示例](packages/contracts/application-design/v1/README.md)、[AI 生成规范](docs/architecture/ai-generation-spec.md)。后续方向为对话生成 JSON、组件选择/排序/响应式配置及流程图预览，不依赖拖拽。Docker 离线部署继续暂缓。

按 `plan.md` 最初目标，先实施课表配置设计助手，验证管理员自助及学生规则请求成本，再扩展通用表单/流程；专用协议复用现有 DisplayConfig 和发布链路。范围、阶段门槛及原验收指标见 [MVP 目标对齐](docs/architecture/mvp-design-alignment.md)。

课表配置现已支持 AI 生成及人工 JSON 提案：在“应用管理 → AI 模型配置”保存模型，应用工作台描述需求后生成提案，校验差异并接受为草稿，之后按原流程试跑和发布。不自动发布、不修改访问角色，无新增迁移；详见 [模型配置](docs/runbooks/ai-model-configuration.md)及 [提案验收](docs/runbooks/mvp-acceptance.md)。

管理员现可通过“微服务广场 → 应用管理”进入应用工作台：编辑应用信息/可见角色、暂存设计、预览、发布即生效和回滚。旧侧栏配置入口已合并到这里。服务器部署入口支持默认关闭、独立授权的 SSH 指纹核对、只读检查与安装/更新计划审阅，自动安装/代码更新尚未完成；参见 [应用管理与本地服务器检查](docs/runbooks/application-management.md)。无需新增数据库迁移。

最新课表规则已支持“本周五 / 下周三 / 下星期一 / 下礼拜日”和“今日 / 明日 / 后日”，继续不调用 AI。未明确本周或下周的“周五课表”先澄清，避免误查。规则版本 v1.2；51 项回归和 227 条 v2 合成样例通过，无需迁移数据库。

## 最新主线进度：服务端登录门禁与自动验收

未登录、会话过期或已退出时，课表、身份和发布配置读取接口均返回 401，不再自动使用默认学生。先重启 API，再执行 `./.venv/Scripts/python.exe scripts/verify_mvp.py`，自动检查登录 → 课表查询 → 学生隔离 → 退出闭环；无需填写管理令牌，不修改业务数据或发布配置。该脚本仅支持本机演示环境，默认查询已生成的 `2026-10-19` 课表。

当前 47 项后端回归（含 openGauss 隔离集成）通过；真实教务、校园 SSO 与正式 UAT 仍待外部资料。界面不在本轮扩展。

## 业务模拟数据已入库（2026-10-19）

当前数据库已增加 `biz_student` 和 `biz_course_schedule`，统一以 `biz_` 标明业务表；现有核心表未重命名。已写入 3 名模拟学生及 545 条课表，覆盖 2026-09-01～2027-01-31，全部带 `is_mock`/`source_tag` 标识。配置已改为 `[sis] mode = "database"`，重启后查询直接读取业务库；当前演示身份对应 `local-demo-student`，工作日每天 2 节。详见 [业务表说明](docs/api/business-data.md)。

已执行迁移 `0003_biz_schedule` 和模拟数据生成，重复执行新增数量为零，不覆盖已有配置或人工修改的课程。其他环境可用 `python scripts/seed_mock_data.py` 显式生成；启动脚本只做迁移，不自动生成业务数据。本轮 39 项测试全部通过，包含数据库课表查询、重复生成、编辑保留和学生/租户隔离；前端类型及契约检查通过。

## 当前入口：MVP 开发版统一验收（2026-10-18）

规则与展示已合并为一个发布包，管理页面支持规则编辑、试跑、差异及回归检查、发布、回滚来源和操作审计；增加租户/RBAC 边界、OIDC 访问令牌验证接口、HTTP 教务适配器、可选 Redis 与持久化 Outbox、基础运行指标和自动评测。详细验收步骤与外部接入缺口见 [MVP 统一验收清单](docs/runbooks/mvp-acceptance.md)。以下历史步骤保留作学习记录，以本节和验收清单为准。

先停止原 API，在 Git Bash 项目根目录运行：

```bash
./.venv/Scripts/python.exe -m pip install -r apps/api/requirements-opengauss.txt
./.venv/Scripts/python.exe scripts/start_api.py
```

另一个终端 `pnpm dev`。启动脚本会执行增量迁移并启动 API/Worker，保留原配置和历史；当前库已升级到 `0002_mvp`。先“加载配置 → 编辑规则/字段 → 校验并试跑 → 保存草稿 → 发布”，再到学生区查询；回滚同时恢复规则与展示。

最新复验（2026-10-19）：PyJWT 与 Redis 客户端依赖已安装，35 项自动检查全部通过，包含真实 RSA JWT 签名、过期/issuer/audience 校验，以及 openGauss 实库隔离 Schema 的迁移、并发、租户、队列与管理接口链路；146 条合成问句及契约检查通过。生产构建仍受执行环境 `spawn EPERM` 限制，须在本机 `pnpm build`。真实 Redis、校园 SSO/教务、独立真实问句集及三名管理员 UAT 尚未验收，不能声称完整上线 MVP 已通过。AI 是条件触发的可选项，目前始终关闭。

从 `config.example.toml` 可查看 `[auth]`、`[sis]`、`[redis]`、`[telemetry]` 的可选配置；实际密码文件不会被覆盖。默认仍是模拟身份与模拟课表。OIDC 前端为访问令牌入口，自动跳转校园 SSO 登录不在本次已验证实现中。

```bash
./.venv/Scripts/python.exe scripts/check.py --database
./.venv/Scripts/python.exe scripts/evaluate.py
./.venv/Scripts/python.exe scripts/export_contracts.py --check
pnpm build
```

## 历史实施记录

最新进度（2026-10-18）：用户已验收配置保存、发布、回滚和重启保留。现已增加自然语言规则查询（见下方“第三步”），继续使用 Mock 身份与课表。当前规则由代码维护，尚未实现管理员编辑规则、规则与展示配置原子发布，以及真实认证。

当前进度：React 查询页面 → FastAPI → Mock 课表适配器；已增加 PostgreSQL 展示配置草稿、发布、回滚与本地管理页面的实现。原 Mock 页面已由用户验证；新数据库链路尚待安装依赖、准备 PostgreSQL 后联调。尚未实现真实认证、自然语言路由或 AI，不可用真实学生数据，不用于生产部署。

本机实施记录：Python 3.12 的虚拟环境初始化受临时目录权限限制，且依赖源不可用；当前 `.venv` 临时使用 Python 3.11 并复用本机已有依赖，接口测试已通过。当前可直接执行下面的启动命令，不必重新创建环境。正式开发环境仍按计划使用 Python 3.12 和独立依赖，恢复安装条件后重新建立隔离环境。

验证记录：用户已验证原查询页面。新增配置功能的 TypeScript 检查和 7 项非数据库测试通过；PostgreSQL 迁移/发布集成测试因缺少依赖与数据库而跳过，完整前端构建在代理执行环境中遇到 `spawn EPERM`，需在用户终端运行 `pnpm build`。

## 第一次启动（Windows PowerShell）

在项目根目录打开终端，创建独立 Python 3.12 环境并安装依赖：

```powershell
& 'C:\Users\Administrator\AppData\Roaming\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe' -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/api/requirements.txt
pnpm install
```

上面使用本机已发现的 Python 3.12 路径，因为本机 `py -3.12` 未能选中该运行时。其他机器可将第一行替换为 `py -3.12 -m venv .venv`。虚拟环境已创建时无需重复创建。

终端一启动 API（保持终端开启）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir apps/api --reload --reload-dir apps/api/app --host 127.0.0.1 --port 8000
```

终端二在项目根目录启动页面：

```powershell
pnpm dev
```

打开 http://127.0.0.1:5173 。接口文档在 http://127.0.0.1:8000/docs ，健康检查在 http://127.0.0.1:8000/api/health 。终端按 Ctrl+C 停止。无需激活虚拟环境或修改 PowerShell 执行策略。

## 亲手验证第一条链路

1. 选择工作日，点击“查看课程”，应显示一节模拟课程。
2. 选择周末，应显示“没有课程”，这是成功的空结果。
3. 在接口文档查询倒置日期或超过 31 天的范围，应返回 `422` 和错误码。
4. 停止 API 后重新查询，页面应显示错误，而不是“没有课程”。

## 从哪里改起

- `apps/web/src/main.tsx`：日期选择、请求状态与课表展示；先改文案观察热更新。
- `apps/api/app/main.py`：HTTP 参数校验、统一响应及请求编号。
- `apps/api/app/modules/skills/schedule.py`：模拟数据与教务适配器接口；先改课程名观察端到端变化。
- `plan.md`：实施范围与验收，未来场景不能自动视为本期任务。

前端开发服务器将 `/api` 转发到本机 8000 端口，因此无需先处理跨域。页面只使用日期参数，模拟学生 ID 在服务端固定，不接受客户端指定学生身份。

## 验证命令

```powershell
Push-Location apps/api
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
Pop-Location
pnpm build
```

## 接下来按什么顺序做

1. 第 0 周信息收集：确认统一认证负责人、教务接口负责人、测试账号、日期/学期规则与脱敏问句。真实对接与 Mock 并行，拿到资料后再实施认证。
2. 按 `plan.md` 第 1～2 周引入 PostgreSQL、Redis、迁移及配置发布；当前机器未检测到 Docker，先决定本地安装还是使用独立开发实例。
3. 将模拟身份替换为真实认证上下文，运行越权测试，再接真实教务适配器。
4. 在日期查询稳定后增加自然语言规则与日期提取，最后评估是否需要 AI。

当前前端类型为骨架期手写；正式接口契约确定后，从 OpenAPI 生成客户端并添加兼容性检查。此阶段尚未完成计划中的配置中心与生产安全验收。

## 第二步：展示配置持久化与发布（Git Bash）

### 推荐：使用本地配置文件

数据库类型说明：本次提供的远端实例实际返回 openGauss 7.0.0-RC1 版本，不是原生 PostgreSQL。`config.local.toml` 已设置 `backend = "opengauss"`，使用该实例先安装：

```bash
./.venv/Scripts/python.exe -m pip install -r apps/api/requirements-opengauss.txt
```

然后执行下方迁移与启动命令；原生 PostgreSQL 使用 `backend = "postgresql"` 和普通 requirements。若曾设置 DATABASE_URL，先 unset，防止覆盖文件类型配置。openGauss 采用社区维护的 `opengauss-sqlalchemy` 方言和 psycopg2 驱动，Alembic 仅为当前简单建表迁移注册 PostgreSQL 风格的迁移实现；不承诺全部 PostgreSQL 特性兼容，迁移、JSON 读写、锁与回滚仍需在实际实例验证。如果驱动认证失败，不要直接修改服务端认证算法或重置账号密码，应先确认实例要求的连接驱动。

项目根目录已准备 `config.local.toml`，预填了本次开发数据库的地址、账号和库名。只需填写 `[database]` 下的 `password`，以及 `[local_admin]` 下至少 24 字符的 `token`。密码无需 URL 编码；包含反斜杠或双引号时可改用 TOML 单引号字符串。不要把填好后的文件或截图发到聊天中。

该文件已被 Git 忽略；新机器可从 `config.example.toml` 复制。API 与 Alembic 均按项目路径自动读取，与终端当前目录无关；修改后须重启 API。旧环境变量优先于文件，如果之前设置过，请在将启动后端的 Git Bash 中先执行：

```bash
unset DATABASE_URL LOCAL_ADMIN_TOKEN
./.venv/Scripts/python.exe -m pip install -r apps/api/requirements.txt
./.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --reload --reload-dir apps/api/app --host 127.0.0.1 --port 8000
```

请逐条执行，安装或迁移失败时先处理报错，不继续启动。页面配置管理输入文件里的同一 token。数据库仍需可连通且账号有建表权限；文件留空密码时查询会提示配置错误，不会连接远端。以下手工环境变量与 Compose 步骤作为其他环境的可选方式，使用此文件时无需重复设置。

此步继续使用模拟学生和课表，只将展示配置存入 PostgreSQL。不启用 Redis、规则配置、审批平台或 AI。只有配置文件与环境变量均未指定数据库时，学生查询才使用明确标记的内置默认配置；配置数据库后若发生故障则返回错误，不静默退回默认配置。

### 1. 安装数据库依赖

在项目根目录执行：

```bash
./.venv/Scripts/python.exe -m pip install -r apps/api/requirements.txt
```

如果提示找不到 SQLAlchemy 版本，检查本机终端是否设置了 `PIP_NO_INDEX=1`；需要可访问的软件源才能安装。不要因为依赖安装失败就跳过迁移。

### 2. 准备开发用 PostgreSQL

已有 PostgreSQL 时，新建专用开发数据库并准备有建表权限的账号，不连接生产库。没有时先安装并启动 Docker Desktop，再执行：

```bash
cp .env.example .env
# 用编辑器修改 .env 中的 POSTGRES_PASSWORD，保留用户名 campus 和数据库 campus_dev。
docker compose up -d postgres
docker compose ps
```

Compose 只将端口绑定在 `127.0.0.1`，数据存放于持久化卷；不要使用 `docker compose down -v`，它会删除数据库卷。若 5432 被占用，调整 Compose 左侧端口并同步修改连接地址。

### 3. 设置连接、管理令牌并迁移

在将启动后端的同一个 Git Bash 终端执行。下列输入不会显示在终端，避免把真实密码写入命令历史：

```bash
read -r -s -p '输入数据库 URL: ' DATABASE_URL; echo
export DATABASE_URL
read -r -s -p '输入至少 24 字符的本地管理令牌: ' LOCAL_ADMIN_TOKEN; echo
export LOCAL_ADMIN_TOKEN
./.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
```

URL 格式为 `postgresql+psycopg://campus:密码@127.0.0.1:5432/campus_dev`。密码中的 `@`、`:`、`/`、`%` 等特殊字符需要 URL 编码；不要把真实 URL 发到聊天或提交到仓库。此处环境变量只在当前终端有效，Compose 的 `.env` 不会自动加载到 Python API。

首次迁移创建配置入口、不可变发布记录和审计表，初始化 `local-demo` 租户的课表展示草稿。重复执行 `upgrade head` 不会重置草稿或发布历史。

### 4. 重启后端，打开管理区

先在原后端终端按 Ctrl+C 停止，然后在已设置环境变量的终端执行：

```bash
./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --reload --reload-dir apps/api/app --host 127.0.0.1 --port 8000
```

另一个终端保持 `pnpm dev` 运行。访问 http://127.0.0.1:5173 ，点击“打开本地配置管理”，输入刚才设置的管理令牌，点击“加载”。令牌仅保存在组件内存，关闭管理区后清除；这只是开发期保护，不替代正式认证与授权。

### 5. 按顺序验收

1. 将“地点”改名为“上课地点”，隐藏“教师”，移动字段顺序，点击“保存草稿”。
2. 查询工作日课表：仍显示旧配置，证明草稿未直接影响查询。
3. 点击“发布已保存草稿”，确认发布后重新查询：字段名称、顺序、显隐应生效，结果携带新的配置版本。
4. 再修改并发布一次；在历史列表回滚到第一版，重新查询应恢复第一版展示。回滚创建新发布记录，不覆盖历史，也不修改当前草稿。
5. 重启 API 后重新加载和查询：草稿、历史和当前发布仍存在。
6. 两个浏览器页签加载同一草稿，一个保存后另一个再保存，应提示版本冲突，重新加载后才能继续。

关闭配置管理不会退出正式登录（目前尚无正式登录）；隐藏教师字段也不是字段授权，API 仍返回模拟课程字段。真实字段权限将在认证阶段由服务端实现。

### 验证命令与当前限制

```bash
pnpm build
cd apps/api
../../.venv/Scripts/python.exe -m unittest discover -s tests -v
```

数据库集成测试默认跳过。准备好开发库与依赖后，在项目根目录设置 `export TEST_DATABASE_URL="$DATABASE_URL"` 再运行测试。集成测试在该库创建随机 `config_test_` Schema 并在结束后仅删除该测试 Schema，需要建 Schema 权限，不操作应用配置表。它覆盖首次迁移、保存不生效、发布、幂等重放、过期修订冲突和回滚；并发竞争和完整浏览器操作仍需补充联调验证。

此步是配置中心的第一段闭环，仅实现展示配置；规则和展示原子打包、完整配置 Schema 契约导出、发布前问句回归、Outbox 与 Redis 仍待后续实现，不算完整第 2 周验收。当前同步数据库操作放在 FastAPI 线程池中，查询固定读取一次发布版本，不阻塞异步事件循环。

## 第三步：自然语言规则查询（2026-10-18）

规则 v1.1 补充：“明天课表”“明天有什么课”“明天有哪些课”“明天有啥课”“明天有什么课呢？”映射到相同查询；完整问句外层的 `【】`、引号或方括号可以省略处理。仍完整匹配问句，不忽略否定或附加操作。日期词与问法模板组合，不需要为每一种日期与句式单独写完整句子，也不需要模型调用。

无需数据库迁移。后端使用 `--reload-dir apps/api/app` 时通常会自动重载；若新接口未出现，Ctrl+C 后重新执行原启动命令。前端刷新 http://127.0.0.1:5173 ，在“用一句话查课表”输入问句并查询。

| 问句 | 预期行为 |
| --- | --- |
| 今天有什么课 / 明天课表 / 后天有课吗 | 查询校园时区对应的单日课表 |
| 本周课表 / 这周有哪些课 | 查询本周一至周日 |
| 下周有什么课 | 查询下周一至周日 |
| 2026-10-19课表 / 2026年10月19日的课表 | 查询明确日期 |
| 查课表 | 默认今天，并明确提示默认范围 |
| 今天和明天的课表 / 下周三课表 / 下一次上课 | 提示明确日期，可改用日期选择，不猜测执行 |
| 2026-02-29课表 | 提示日期不存在 |
| 不要查今天课表 / 今天课表，帮我请假 | 不执行课表查询，提示明确操作 |
| 我要请假 / 查询成绩 | 提示当前仅支持课表，不执行办理操作 |

示例按钮仅填入输入框，仍需点击查询。成功解析会同步日期选择范围并显示规则 ID、规则集版本、实际查询范围及展示配置发布 ID。自然语言与日期查询共享技能调用和配置渲染，整条链路不调用 AI。

新增 `POST /api/v1/course-schedule/query`，请求为 `{"text":"明天课表"}`，最多 200 字符。响应 `routing.status` 为 `resolved`、`clarification` 或 `unsupported`；后两者是正常业务反馈，HTTP 200、`data=null` 且不查询数据。无效请求格式返回 422，上游配置故障仍返回错误，不伪装成无课程。

实现入口：`apps/api/app/modules/intent/router.py`。采用完整问句匹配，防止忽略否定语句或附加命令；支持范围有限，不是通用语义理解。校园时区固定 Asia/Shanghai、每周从周一开始，无会话状态。尚未支持无年份日期、任意时间区间和多意图组合，统一引导使用日期选择。

验证记录：17 项测试通过，1 项数据库集成测试按环境跳过；TypeScript 检查通过。完整 Vite 构建在代理环境仍遇 `spawn EPERM`，需在本机终端运行 `pnpm build` 并完成页面验收。本步没有修改数据库结构或已有配置历史。
# 校园智能工作台：界面入口更新

启动前后端后访问 `http://127.0.0.1:5173`，先登录，再进入工作台。演示学生直接选择已入库的模拟账号；管理员使用已有本地管理令牌。左侧为校园助手、微服务广场、查询历史和退出入口；配置管理仅管理员可见。课表助手为首个可用微服务，其他应用标注“待接入”。

参考 `界面参考图` 实现桌面侧栏/对话和移动端抽屉导航，不新增后端部署服务。刷新需重新登录，令牌和查询历史不写浏览器存储。正式校园认证仍需接入 OIDC，不能将演示登录视作生产认证。

验收步骤见 `docs/runbooks/mvp-acceptance.md` 的“统一工作台入口更新”。

# P0 基线工具

源码归档、加密应用数据备份、隔离恢复演练、统一检查与版本清单见 [基线恢复手册](docs/runbooks/baseline-recovery.md)。当前Git提交仍受目录写权限限制，生产构建和真实浏览器验收单独记录；开发版本声明不代表可部署产物。

