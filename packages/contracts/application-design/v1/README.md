# 应用设计 JSON 标准

版本：1.0.0 草案。状态：设计契约，待实现运行时。此目录独立于现有课表 `config.schema.json`，不会改变现有发布内容。

目标是让管理员通过对话生成应用草稿，并用属性面板精确调整。运行端只使用经过校验和明确发布的应用定义；模型输出、聊天记录及模拟结果本身都不构成执行授权。

## 优先实施课表专用协议

根据 `plan.md` 最初目标，先使用 course-context/course-proposal 改善已有课表配置自助；下文 application/ai-context/ai-proposal 为通用应用阶段的协议。两者是明确的不同配置类型，不允许验证失败后回退另一种。协议、profile 和应用 ID 由服务端选择并绑定请求，不能由模型切换。

课表专用协议的范围、规则语义、成功指标与开发门槛见 [MVP 对齐说明](../../../../docs/architecture/mvp-design-alignment.md)。它直接使用现有 DisplayConfig，不生成新业务、模型、权限或流程；接受提案仍只修改草稿，发布继续走原有校验/回归和事务。

## 文件与标准

| 文件 | 用途 |
| --- | --- |
| `application.schema.json` | 应用包，涵盖数据、表单、流程、视图、权限、页面和导航 |
| `ai-context.schema.json` | 服务端构造的模型输入上下文和权限上限 |
| `ai-proposal.schema.json` | 模型返回的创建、修改或澄清提案 |
| `course-context.schema.json` 与 `course-proposal.schema.json` | 优先实施的课表配置上下文与受控提案 |
| `examples/course.context.json` 与 `course.proposal.json` | 课表地点改名及教师列隐藏，保持角色和规则不变 |
| `course-conformance-cases.json` | 课表专用结构与语义验收向量 |
| `examples/leave.application.json` | 完整请假应用设计样例 |
| `examples/leave.context.json` 与 `leave.proposal.json` | 根据已选字段修改必填属性 |
| `examples/leave.clarification.json` | 关键规则不明确时只提问 |
| `examples/leave.create-context.json` 与 `leave.create-proposal.json` | 从空设计创建完整草稿 |
| `conformance-cases.json` | 结构及语义反例，供后续验证器和评测使用 |

采用 [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12)。三个 `$id` 分别为 `urn:campus:application-design:v1:application`、`urn:campus:application-design:v1:ai-context`、`urn:campus:application-design:v1:ai-proposal`。验证器预先从本地文件注册这些标识，禁止网络解析未知引用。

课表专用两个 `$id` 为 `urn:campus:application-design:v1:course-context` 和 `urn:campus:application-design:v1:course-proposal`。它们复用通用标准的 ID/摘要结构，并在 course-context 中内嵌现有配置结构快照；部署时同样本地注册，不能从网络下载 Schema。

所有实例对象禁止额外属性。字段顺序、分组、节点和导航顺序使用数组表示。Schema 不填入默认值；必填属性必须显式提供。JSON 解析先拒绝重复对象键、NaN、Infinity，再校验 Schema。日期合法性、时区、跨字段引用、权限和发布资格由语义校验处理；不能把 `format` 注解当作完整业务校验。

## 首版边界与预算

支持有限表单组件、12 栅格响应式布局、顺序审批及有序条件分支、列表/详情/聚合视图。不支持拖拽依赖、任意 HTML/CSS/JavaScript/SQL、自定义 URL、远程脚本、任意表达式、会签、并行、循环或运行中实例自动迁移。

| 项目 | 上限 |
| --- | --- |
| 规范化后单个应用包 | 512 KiB |
| 模型上下文与模型响应 | 各 1 MiB，含草稿和目录 |
| 应用数据模型 | 10 个，每模型最多 100 字段 |
| 表单 | 20 个，每表单最多 20 分组、总计 100 控件 |
| 流程 | 10 个，每流程最多 50 节点 |
| 条件 | 嵌套最多 4 层，单表达式最多 100 节点，每组最多 10 条 |
| 视图、权限、页面、导航 | 每类最多 30 个 |
| 单提案 | 最多 50 个操作、10 个澄清问题、30 个待满足条件 |
| AI 调用 | 单次最多 60 秒，一轮最多 1 次针对校验错误的自动修正 |

数组局部上限由 Schema 执行；总量、深度、序列化字节数由解析及语义层执行。上下文超限时按选中对象和引用范围构建可解释的上下文，不能悄悄截断对象或权限信息；首版无法完整提供依赖时拒绝生成。

## 对象标识和引用

本地设计 ID 为小写字母开头、字母数字下划线或连字符，最长 64 字符。能力 ID 允许点号，最长 96 字符，必须与能力目录精确匹配。禁止 `__proto__`、`prototype`、`constructor` 作为数据字段 key；对象映射使用安全字典。

应用包顶层集合的对象 ID 全局唯一；数据字段 ID 跨模型唯一。控件、分组、节点、分支 case、视图列与指标 ID 在各自父对象中唯一，不要求不同流程的 `start` 节点互异。控件由 `(form_id, control_id)` 定位，节点由 `(workflow_id, node_id)` 定位。字段持久化 key 在所属模型内唯一，首次有业务数据后不得改名；显示名称可改。

表单、流程和视图显式绑定模型；字段条件只能引用所属模型的字段。页面 `kind=form` 引用表单，`kind=view` 引用视图。导航引用页面。视图、页面与权限的模型必须一致。相互引用必须在整个提案应用完成后检查，不能依赖数组中的声明顺序。

`required_capability_ids` 必须恰好包含应用全部外部依赖，不能遗漏或添加未使用能力。能力目录包含版本与状态，发布时服务器固定实际解析版本。样例中全部目录项为 `planned`，因此只能作为设计和结构验证材料，不能直接发布。

## 数据与表单语义

数据类型包括 string、integer、decimal、boolean、date、datetime、enum、enum_list。枚举值唯一且稳定，显示名称独立。decimal 以运行时定点十进制处理，API 传输采用十进制字符串；Schema 中数值边界属于设计元数据，不能据此使用浮点数计算业务金额。

`nullable` 控制是否允许 null；表单 `required` 控制有效输入。缺失、null、空字符串、空数组分别处理：必填字符串 trim 后不能为空；必填多选不能为空数组；false 和 0 是有效值。未填写的可选字段由模型适配器约定规范化，不能由 AI 决定；若字段非空且无服务端缺省值，允许空字符串的类型可提交空字符串，否则发布校验要求表单必填或提供可靠的服务端赋值来源。

constraints 的 `max_length` 只用于 string；minimum/maximum 用于 integer/decimal，前者不得大于后者；precision 只用于 decimal。日期先后、业务长度等跨字段约束由已注册动作/派生字段的契约校验，首版不接受临时脚本。

组件类型必须与数据类型匹配：text/textarea→string，number→integer/decimal，checkbox→boolean，select→enum，multiselect→enum_list，date→date，datetime→datetime。字段选项在数据模型定义，控件不保存另一份副本。默认值必须符合类型、枚举与范围；用户不能通过默认值写入状态、租户、申请人等服务端字段。derived_by 字段只能由已登记的派生能力计算，不可出现在可写权限集合中，也不可被可编辑控件绑定。

布局为 mobile/tablet/desktop 三档，各占 1～12 格。首版断点：小于 768px 为 mobile，768～1199px 为 tablet，1200px 起为 desktop；控件按数组顺序从左到右排列，不足一行自动换行，分组另起一行。标签换行与内容高度由组件管理，不保存任意像素定位。

条件支持 eq/ne/gt/gte/lt/lte/in/contains/is_empty/is_not_empty/all/any。比较不进行字符串到数字等隐式转换；in 的右值为字符串数组，左值为字符串；contains 的左值为字符串或字符串数组，右值为字符串。数值比较要求同类数值语义与单位；date/datetime 的关系比较首版由注册能力转换为可比较的值，不直接比较任意格式字符串。条件缺值或类型错误返回验证错误，不静默判 false。

联动依赖必须无环；隐藏字段不绕过模型必填或后端授权。disabled/readonly 是交互属性，后端仍按权限和动作契约接受字段。切换隐藏不自动删除已填值，提交采用适配器声明的接受规则，页面必须明确反馈。

## 流程执行契约

流程恰好一个 start、至少一个 approval 与一个 end。入口必须为 start。所有节点可从入口到达，所有路径必须在 end 终止，禁止循环、悬空引用和 start 入边。流程模型与表单一致，执行器能力支持该模型及动作。

审批节点首版只选择一个实际办理人，禁止自批。角色与组织关系必须来自目录，找不到、多于一个候选或命中申请人自身时进入可审计的阻塞状态，不能随机分配或自动通过。节点 on_approve/on_reject 必须存在；驳回路径首版必须直接到 rejected end。

分支按 cases 数组顺序匹配第一条，必须提供 default_next。可证明的重叠和不可达分支拒绝发布；无法静态证明的复杂规则必须提供边界模拟，不能只凭 AI 表述认定无冲突。空值或条件计算失败阻塞执行，不走默认分支。

撤回由已注册 action 处理，allowed_at_node_ids 只能包含 approval 节点。撤回成功产生独立的 withdrawn 实例状态，不使用 approved/rejected end 冒充。草稿保存、提交、通过、驳回、撤回均走服务端命令，禁止直接更新 status。

申请实例绑定不可变发布 ID、表单与流程定义、提交快照、审批依据。每次操作重新校验当前操作者、租户、数据范围、任务归属和业务修订号。命令使用持久化幂等键；状态、审批记录、审计和 Outbox 同事务提交。新设计版本不修改运行中实例；旧版本存在实例时禁止删除对应执行能力。

样例采用 Asia/Shanghai，含首尾自然日数 `end_date - start_date + 1`。3 天不增加院系审批，4 天增加；只允许辅导员待办阶段撤回。这是样例已确认决策，不能推广成所有请假业务的默认规则。

## 视图与权限语义

列表和详情展示 columns；aggregate 只使用 metrics 和 group_by_field_ids。count 不填 field_id，其他聚合必须填；sum/avg 仅用于数值字段，min/max 首版也仅用于数值字段。filterable、sort、group_by 和 metrics 引用字段必须在模型与服务端权限范围内。页面条件只会进一步收窄查询范围，不能替换租户与数据权限条件。

权限默认拒绝。设计 policy 只是授权提案，必须同时受服务端提供的角色、操作、模型、字段、查询、动作与 scope 上限限制。不同 scope 之间不假定包含关系；没有显式允许的组合即拒绝。页面、视图和动作都要鉴权；菜单隐藏不能替代接口授权。聚合先限定明细可见范围，再统计，不能通过统计绕过隐私边界。

配置管理员管理设计的权限不自动授予业务审批或全校数据权限。`can_propose_policy_changes=false` 时禁止新增、删除、替换 policy 以及通过导航/页面/视图关联变相拓宽权限；页面 role_ids 只能是实际授权集合的子集。

## 草稿提案和发布

模型只生成提案，不生成 tenant_id、actor、发布 ID、权限结论、校验通过标记、业务表名、SQL、密钥或生产配置。服务端提供并验证 request_id、application_id、base_revision、base_content_hash 和 catalog_hash，模型原样回传。

新建采用 draft=null、revision=0，base_content_hash 为服务端对 JSON null 规范化后的摘要。create_application 必须是唯一操作，且应用尚不存在。既有应用禁止 create_application。

add_object 只允许不存在的 ID，replace_object 要求已存在且 ID 不变，remove_object 要求目标存在且最终不存在悬空引用。replace_control 保持控件和绑定字段 ID；改绑字段走显式表单替换并做数据影响审查。reorder_controls 必须恰好包含该分组原全部控件，不跨分组、不增删。多个操作按数组顺序应用于内存副本，最后整体校验；任何失败都不写入。相同对象的冲突操作、删除后再修改、仅靠重新排序掩盖删除均拒绝。

增加或删除外部能力引用时同时使用 set_required_capabilities 更新完整依赖列表；该操作不会注册、授权或实现能力。服务端从最终包重新提取所有实际引用并比对列表，不能靠删掉列表项绕过依赖校验。业务动作和流程执行器还需匹配目录 declared model_ids 与 allowed_operations。

accepted proposal ID、操作者、修订号、摘要、时间和审计为服务端信封，不属于模型输出。接受提案接口使用幂等键，校验三个摘要/版本后原子写入新修订；相同键不同内容返回冲突。首次接受后重复请求返回原结果，不能重复增加修订号。撤销是将目标历史内容作为新草稿修订，不能删除审计。

内容哈希由服务端统一计算并作为不透明值传给客户端和模型。固定算法为 SHA256，输入是后端排序对象键、无额外空白、保留 Unicode、拒绝非有限数值后的 UTF-8 JSON；数组顺序保留。客户端和模型不得自行重算，避免浮点序列化差异。目录摘要同样由服务端生成，样例目录按 id 排序后计算。

结构校验、引用与类型校验、能力/权限校验、业务和模拟校验全部通过后才可发布。requirements 和服务端未决项均须解决，planned 能力阻止发布。发布重新读取最新目录及权限，不能信任模型声称的通过结果；整个应用包一次发布，页面、表单、流程、视图和权限不可部分生效。

发布回滚新建发布快照，不修改历史，不回滚业务数据和运行中实例。删除字段、修改类型、枚举值、必填规则或流程策略时，必须评估存量数据；首版禁止有数据后的破坏性变更，直到明确迁移方案实现。

## 后续校验器的输出

每条诊断包含 code、severity、object_id、path 和 message。path 使用 JSON Pointer；业务执行不能只判断返回的自然语言。基本错误码为 DUPLICATE_ID、REFERENCE_MISSING、TYPE_MISMATCH、CAPABILITY_MISSING、CAPABILITY_PLANNED、PERMISSION_ESCALATION、WORKFLOW_CYCLE、ACTOR_UNRESOLVED、DESTRUCTIVE_CHANGE、BUDGET_EXCEEDED、REVISION_CONFLICT、OPEN_QUESTION。

Schema 验证器负责结构；后续语义验证器还必须实现本文件的规则及反例。验证器不得为了“修好”提案而默默删除字段、改变角色或放宽校验。
