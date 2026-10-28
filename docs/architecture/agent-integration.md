# 第三方 Agent 接入：能力中心与接口蓝图

日期：2026-10-23。状态：设计稿，下面外部API、MCP工具、连接授权与确认记录尚未实现；本轮不开放网络端口、不签发凭证、不连接WorkBuddy、不改用户数据。现有业务API不自动构成受支持的外部契约。实施沿用[版本规范](development-and-versioning.md)、[类型扩展路线](type-extension-roadmap.md)和[AI生成规范](ai-generation-spec.md)。

## 1. 平台保有的中心职责

平台是应用身份、配置发布、业务命令、业务记录和权限审计的权威来源。第三方Agent负责理解、收集参数、调用工具和解释结果；平台网页也是同一业务服务的客户端。MCP是协议适配，不新增业务数据库、发布中心或审批执行器。

| 对象 | 权威来源 | Agent可持有内容 |
| --- | --- | --- |
| 用户、租户和权限 | 平台信任的身份提供方与连接授权 | 受限访问令牌，不能自报用户/角色 |
| 应用与配置 | 平台实例、精确类型、草稿与发布快照 | 授权范围内的摘要与草稿提案 |
| 登记/请假记录 | 平台事务及历史快照 | 记录ID和最小必要查询结果 |
| 课程数据 | 当前教务数据源，平台适配与授权 | 查询结果，附数据来源及读取时间 |
| 操作状态 | 平台业务事务和命令结果 | command_id、状态、可继续入口 |

数据中心不是所有数据都复制到同一库。接入外部数据源时逐项声明权威系统、外部对象ID、同步方向、更新时间和冲突处理；没有写入协议时仅查询。Agent对话历史和向量检索结果不能替代最新业务状态。

## 2. 当前基础与缺口

已有：OIDC JWT校验、应用精确版本、配置发布、记录授权、业务幂等、部分持久命令核对、审计及Outbox。现有OIDC校验不是OAuth授权服务器；各域命令记录也不是已统一的外部命令查询服务。

待开发：连接客户端/授权范围管理、MCP资源认证、按权限派生的能力目录、稳定外部响应、统一结果查询适配、可信用户确认入口、外部调用审计关联。不得直接分发本地管理员令牌接入多人环境。

## 3. 接入形态与身份

首选HTTPS Streamable HTTP MCP服务，映射到平台业务服务；本地开发可以用stdio适配，但两者都不能绕过授权。WorkBuddy连接器支持MCP与可选Skill；Skill说明调用顺序，不授予权限。参考[WorkBuddy官方连接器文档](https://open.workbuddy.cn/docs/connector)。具体客户端版本、认证流程及传输方式要在联调时验证。

正式多人接入优先复用可信身份提供方的OAuth授权码+PKCE与用户委托，或独立认证代理；不在项目中临时自制OAuth协议。接口校验访问令牌的issuer、audience、有效期和scope；MCP服务不能将上游令牌无条件透传给另一个受众的API。调用链内部走已认证上下文，确需跨服务则采用明确的令牌交换/下游凭证策略。

内部只读试点可采用专门的受限Token，但必须有到期、撤销、tenant/user/client绑定和服务端范围检查。认证方案未完成前，只用合成身份与隔离数据测试，不以demo身份对外开放。

有效权限取以下范围的交集：用户权限、连接授权、客户端允许能力、租户/应用/记录权限及当前业务前置条件。application_id由参数选择，tenant和actor从可信身份获得；查询参数不能覆盖后者。

建议首批scope：applications:read、capabilities:read、records:self:read、course:self:read。写入另授予records:self:submit；审批与配置发布必须独立授权，不随查询权限自动获得。创建连接与撤销在平台设置页完成，不能由Agent自行扩大授权。

## 4. 外部接口草案

拟使用独立命名空间`/api/v1/integrations`，协议版本独立演进。以下路径用于设计评审，不代表已有路由；实现时复用应用/配置/记录服务，避免HTTP回调自身管理接口。

| 方法与路径 | 功能 | 首次开放阶段 |
| --- | --- | --- |
| GET /me | 当前主体、客户端及授权范围 | 只读 |
| GET /applications?q=&cursor=&limit= | 搜索可见应用 | 只读 |
| GET /applications/{id}/capabilities | 可发现能力及可用状态 | 只读 |
| GET /applications/{id}/capabilities/{capability}/schema | 固定版本输入/输出Schema | 只读 |
| POST /applications/{id}/queries/{capability} | 有界查询，复杂筛选放请求体 | 只读，无业务写入 |
| POST /applications/{id}/operations/prepare | 校验并准备不可变操作摘要 | 单条写入闭环 |
| GET /operations/{operation_id} | 读取本人原操作及确认/结果状态 | 单条写入闭环 |
| POST /operations/{operation_id}/execute | 执行已授权且已确认的原操作 | 单条写入闭环 |
| GET /commands/{command_id} | 核对原命令的权威结果 | 单条写入闭环 |

另设平台用户确认页面及后端确认动作，只接受实际登录用户的可信交互，包含CSRF等会话保护；不将确认API作为MCP工具，也不依赖来自Agent的confirmed布尔值。连接管理/撤销接口属于平台管理面，不暴露为普通Agent业务工具。

所有列表限制limit（建议默认20、上限100），使用稳定排序和不透明cursor；只返回授权字段，拒绝任意SQL、任意URL/路径和任意代码。参数经严格Schema及语义校验。响应包含request_id便于排查，日志不记录令牌和不必要的原始业务正文。

## 5. 能力描述与工具

能力目录绑定精确type_id/type_version/type_digest、实例和能力协议版本；运行操作另绑定发布ID。目录是授权后的派生视图，不是执行凭证。缺适配器、未发布、停用、依赖不可用应分别表达；不把无权限的应用通过错误或搜索结果泄露。

示意响应（ID为占位符）：

```json
{
  "application_id": "<application-id>",
  "capability_id": "records.self.read",
  "capability_version": "1.0.0",
  "effect": "read",
  "availability": "available",
  "input_schema_ref": "<versioned-schema-ref>",
  "output_schema_ref": "<versioned-schema-ref>",
  "required_scopes": ["records:self:read"],
  "confirmation": "none"
}
```

MCP首批只开放search_applications、list_application_capabilities、query_my_records、query_my_courses。后续写入开放prepare_leave_request、get_operation_status、execute_prepared_operation、get_command_status，execute只能执行已有准备记录，不接收替代目标或新参数。能力ID与工具名由编译期白名单映射，客户端输入不能解析成Python模块或任意HTTP路径。

MCP与REST共用服务端能力注册及输入输出定义；适配错误映射为稳定机器码和可读说明。Skill介绍日期/时区、缺参数澄清、确认链接、结果未知恢复及数据最小化。工具描述中的只读/幂等声明仅辅助模型，服务端仍强制执行。

## 6. 写入准备、用户确认和幂等

1. prepare在已授权上下文中校验参数，固定用户、tenant、client、应用、精确类型/发布、业务对象、参数摘要、修订/前置条件、过期时间和prepare幂等键；不提交业务记录。
2. 服务端返回operation_id、可读变更摘要、expires_at和平台confirmation_url。URL中的标识不单独授予查看或确认权限，仍须登录并验证原用户/租户；页面不自动执行写入。
3. 用户在平台查看准确内容后确认。修改参数需重新prepare并确认；确认记录绑定原摘要，Agent不能自行生成或修改。
4. execute只引用operation_id和原幂等键。重新核验用户授权、连接撤销状态、过期时间、业务前置条件和发布变化；变化则明确冲突并重新准备，不默默使用新发布。
5. 成功返回command_id和业务记录ID，记录用户、client、operation及业务事务关联。超时只能查原命令或用原键重试。

prepare本身也可能丢响应：相同主体/客户端/动作/幂等键/参数重试必须返回原准备记录，不能重复产生不同确认。键相同而参数摘要不同返回冲突。审计trace不能代替幂等键。

外部执行记录与既有业务幂等采用确定性映射，复用既有业务事务。不能先标外部成功再提交业务；若需要异步，用持久任务/Outbox及业务原键收敛结果，不声称跨服务exactly-once。外部状态读取绑定原用户/租户/客户端和目标，不允许凭任意command_id枚举他人结果。

拟定操作状态：awaiting_confirmation、ready、executing、succeeded、rejected、expired。unknown表示客户端尚不知道结果，不直接当服务端业务终态；暂时无法核对返回明确的可重试错误。查询not_found不能证明从未执行，客户端仍保留原键核对，不自行另发操作。凭证撤销后不能为结果查询绕过授权，可引导原用户到平台查看。

## 7. 响应、错误与数据范围

查询响应建议包含items、next_cursor、source和read_at；命令响应包含command_id、state、result_reference和request_id。不得返回模型猜测的记录号或伪造成功。失败采用稳定error.code、message、retryable、request_id，不回传堆栈或敏感配置。

| 场景 | 处理 |
| --- | --- |
| 令牌失效/权限不足 | 401/403，重新连接或申请授权，不自动换管理员身份 |
| 不存在或不可见对象 | 统一404，避免泄露跨租户对象 |
| 发布/修订/幂等参数冲突 | 409，原操作保留，新方案须重新准备确认 |
| 缺参数、类型/业务不合法 | 422，返回受限字段错误供用户修正 |
| 超额调用/服务暂不可用 | 429/503及适当重试信息，保留原命令 |

审计追加client_id、connection_id、request_id、operation_id/command_id与原业务审计关联，避免在共享旧表上随意改历史语义。需新增存储时采用递增迁移及明确保留/清理策略；确认与幂等数据保留期限必须覆盖约定重试窗口，到期后不能假装仍可去重。

## 8. 开发分段与验收

| 阶段 | 交付 | 必须证明 |
| --- | --- | --- |
| I0 | 编译期只读能力描述与隔离契约测试 | 未注册能力不可执行，精确版本和Schema一致；此时不对外开放 |
| I1 | 受限连接授权、只读REST和MCP | 网页与Agent同用户数据一致；跨租户/应用/记录越权拒绝；撤销立即阻断；分页/超额/脱敏 |
| I2 | 请假prepare→平台确认→execute→结果核对 | 未确认/过期/改参/换发布拒绝；准备和执行丢响应均原键恢复；业务只提交一次；原审批人/历史快照保持 |
| I3 | WorkBuddy真实联调 | 建连、授权、查记录、提交、断线恢复和撤销全流程，确认具体客户端支持；不得仅以模拟MCP测试宣称通过 |
| I4 | 管理员创建草稿与配置维护 | 复用建设需求、暂存/校验/发布；发布独立权限与确认；AI仅提案 |

原先类型展示收拢仍在架构待办中；本接入优先从I0只读能力定义开始，不同时重构编辑会话、冻结类型运行包或增加多级审批。不存在“先把所有管理API自动转为MCP再补权限”的阶段。

首个用户验收：同一用户在网页和WorkBuddy搜索到相同可用应用，查询到同一范围记录；提交请假经平台确认后网页可见，丢响应重试不会新增第二条，撤销连接后Agent不能再查询或提交。

源码恢复仅恢复代码；外部授权撤销、已提交记录与已执行迁移分别处理。回退MCP适配不删除业务记录，已确认外部命令仍须可在平台核对。正式开放前补可构建产物、访问部署、真实浏览器和备份恢复证据。
