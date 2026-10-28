# 应用类型扩展协议与多应用配置隔离

2026-10-23 当前源码校准：已安装课程1.0、登记1.0/1.1、单级请假1.0/1.1，具备精确版本解析；新建登记/请假默认统一版。后文“只有课程”等属于早期阶段记录，不代表当前可用范围。类型扩展仍含工作台/创建/运行分支，公共UI复用尚未等于完整插件协议。后续收敛顺序、冻结摘要边界和验收门槛见[扩展架构路线](type-extension-roadmap.md)。

后续实施与兼容验收按 [分阶段交付计划](closed-loop-delivery-plan.md) 和 [开发与版本规范](development-and-versioning.md) 执行。类型升级须先补齐精确版本并存、实际实现产物摘要及恢复路径，不直接修改既有1.0.0摘要让旧实例通过。

版本：0.1，设计基线，按阶段实施。日期：2026-10-20。

契约进展：已落实 [application-types/v1 Schema、示例和结构用例](../../packages/contracts/application-types/v1/README.md)，结构校验通过。课程类型注册、配置隔离及首个模板闭环已实现；对话自动发现/管理引导仍为后续交互方向。

实施进展（2026-10-20）：课程类型注册、应用身份表及配置/发布/问法/缓存/审计/Outbox隔离已实现，0005迁移已在开发库验证。之后已接入模板创建、动态管理界面、新实例发布与运行路由，0006为新发布固定类型/能力绑定；旧课表保留原内容。类型升级、生命周期、新实例AI页面设计和其他应用类型仍待实施。完整操作见 [运行手册](../runbooks/application-management.md)。

本文件同时保留完整目标和阶段实施记录，下文设计接口并非全部已上线，当前类型范围以上方最新校准及源码为准。沿用现有配置发布、审计、Outbox 和规则优先策略，不新建另一套发布中心。与历史蓝图有差异时，本文件优先约束多应用管理及类型扩展；既有表单、流程和 AI 提案 Schema 的内部语义继续有效。

## 1. 核心对象

| 对象 | 职责 | 例子 |
| --- | --- | --- |
| 类型族 family | 展示分类，不承担执行或校验 | query、form、approval、dashboard |
| 应用类型 type_id | 可安装的完整配置/运行协议 | campus.course-query、platform.form |
| 类型版本 type_version | 精确绑定 Schema、编辑器、验证器、运行适配器 | 1.0.0 |
| 应用实例 application_id | 某租户实际维护的应用，服务端生成 UUID | 本科课表、研究生课表 |
| 应用发布 release_id | 实例的不可变配置快照 | 某次课表发布 |

“查询类”是类型族，不表示库存可以直接使用课程字段和日期规则。首个正式类型为 campus.course-query；通用查询类型待参数、数据绑定和结果协议验证后再提取。相同类型可以创建多个实例，但数据连接能力必须确实支持各实例的业务范围，复制配置不产生新的业务数据源。

application_id 永久不变；名称可重复、可改。可选 slug 在租户内唯一，仅作可读别名。实例创建后禁止直接修改 type_id，跨类型转换通过新建实例和显式导入完成。显示分类与类型族分开，避免把“校园事务”误当执行类型。

## 2. 类型扩展包

类型由开发者提供、测试并随平台部署，服务端注册表与前端组件注册表共同组成扩展包。首版只允许编译期白名单，不提供远程脚本、上传插件或任意模块路径加载。数据库只存类型引用和配置，不存可执行代码。

类型清单的设计示例：

```json
{
  "protocol_version": "1.0.0",
  "type_id": "campus.course-query",
  "type_version": "1.0.0",
  "family": "query",
  "title": "课程查询",
  "config_schema_id": "urn:campus:types:course-query:1:config",
  "config_schema_version": "1.0.0",
  "runtime_adapter_id": "course-query-v1",
  "editor_sections": [
    {"id": "display", "title": "结果展示", "editor_id": "course-columns-v1"},
    {"id": "questions", "title": "问法与规则", "editor_id": "course-questions-v1"}
  ],
  "features": {"ai_design": true, "question_library": true},
  "required_capability_ids": ["course.schedule.read"]
}
```

清单、Schema 和实现构成不可变版本，发布时计算规范化摘要 type_digest。相同 type_id/type_version 的内容变化必须拒绝注册。editor_id/runtime_adapter_id 是本地代码映射键；不允许将这些值解释为路径、URL、Python 导入表达式或 JavaScript。

| 扩展接口 | 输入与输出责任 |
| --- | --- |
| default_config | 生成符合 Schema 的空白/模板配置；不得自动创建业务数据 |
| validate | 结构、引用、能力绑定、业务语义、授权上限；返回阻塞项和警告 |
| editor_sections | 声明专属配置页，按编辑器白名单挂载；通用页由工作台提供 |
| preview | 在指定草稿和身份范围内预览；不得产生真实提交/审批副作用 |
| publication_check | 校验运行依赖和回归案例，输出可发布结论 |
| runtime | 仅按已发布快照执行受控动作，接受服务端构造的执行上下文 |
| ai_profile（可选） | 本类型上下文、提案 Schema、允许操作及解释；无此能力则不显示 AI 页 |
| migrate_config（可选） | 指定起止版本的确定性配置转换，输出差异和校验结果 |

服务端是权威注册源：管理目录返回安装状态、精确版本、协议兼容性及本租户可创建权限。仅声明 family 或 features 不代表能力已实现；缺少前端编辑器、后端运行适配器、Schema 或依赖时，新建和发布均阻塞。其他未来类型显示“未安装”，不能创建看似可运行的空壳。

Schema 本地注册，拒绝未知网络引用和额外字段；Schema 结构校验之后仍需语义校验。复杂界面使用专属 React 组件，简单属性可由 Schema 表单生成，不要求用一个通用表单覆盖所有类型。

## 3. 实例配置封装

实例身份和生命周期属于平台；可发布内容属于版本快照。以下为设计数据形状，不替换当前 DisplayConfig：

```json
{
  "application_id": "d08b2476-eeb0-4c15-aac6-e30ef8c1cd88",
  "type_id": "campus.course-query",
  "type_version": "1.0.0",
  "envelope_version": "1.0.0",
  "metadata": {
    "name": "本科课表",
    "description": "查询本人课程安排",
    "category": "学习服务"
  },
  "access": {"use_roles": ["student", "teacher", "config_admin"]},
  "bindings": {"schedule_reader": "capability-binding-uuid"},
  "config": {
    "schema_version": "1",
    "columns": [{"field": "name", "label": "课程", "visible": true}],
    "rules": []
  }
}
```

tenant_id 来自认证，绝不接受请求正文或 AI 指定。示例 config 为迁移后的类型专属部分，旧 DisplayConfig.application 迁入 metadata/access；旧快照仍由旧解析器读取，不直接删除原字段。迁移实现必须提供往返兼容及回归检查。

bindings 只引用租户内已授权的能力绑定，不放服务 URL、密码、SQL 或模型密钥。发布固定绑定 ID、绑定契约版本和数据范围；凭据轮换经受控凭据服务完成，凭据不进入快照。能力被撤销时运行立即拒绝，旧发布不能恢复已撤销权限。首次迁移沿用现有课程能力，不承诺自由接入任意服务。

envelope_version、type_version、config_schema_version、draft revision、release_id 各自独立；不得用草稿修订号判断类型兼容。发布快照固定整个封装、类型摘要、Schema 摘要、能力契约与配置摘要。

## 4. 多应用隔离和数据库边界

安全作用域统一为 (tenant_id, application_id)。所有存储读写、关联、缓存、审计、AI 上下文和任务都必须显式传入此作用域；禁止在多应用路径缺少 ID 时默认退到 schedule。

| 存储 | 目标约束与处理 |
| --- | --- |
| application（新增） | 主键 (tenant_id, application_id)，type_id、slug、lifecycle、lifecycle_revision、创建人/时间；slug 租户唯一 |
| config_state（复用） | 保留 key 作为配置流标识，增加 application_id；唯一 (tenant_id, application_id, key)，独立 revision/current_release_id |
| config_release（复用） | 增加 application_id；发布请求唯一 (tenant_id, application_id, key, idempotency_key)；快照不可变 |
| config_audit（复用） | 增加 application_id；应用审计严格过滤两维，平台级审计使用独立授权入口 |
| outbox_event（复用） | 增加 application_id；消费者按租户/应用/发布处理，重复投递不得重复副作用 |
| question_candidate（复用） | 增加 application_id；规范化去重、次数、配额、审核和规则 ID 全部按应用；旧 ID 原样保留 |
| 持久命令记录 | 将应用纳入幂等作用域与请求摘要，提案接受和发布继续复用现有持久机制 |
| 后续业务记录/任务 | 必须携带 tenant/application/release；只增加配置隔离不能宣称业务数据已隔离 |

关联使用含 tenant/application 的联合外键或等价事务约束，特别是 current_release_id、source_release_id、问法归属与任务发布引用；不能只凭全局 UUID 查询。发布记录需提供对应联合唯一键供引用。状态行锁只锁当前应用，避免一个应用审核问法阻塞整个租户。

缓存键使用无歧义编码后的 tenant/application/type/version/release/content_hash，不缓存未校验的 JSON；数据库当前指针为权威。Outbox、日志和指标携带应用标识，避免把问句全文、凭据或跨应用上下文写入日志。

配置锁、配额与成本需分层：每应用限制问法数、规则数和 AI 使用量，同时保留租户总预算和调用并发上限，不能通过新建应用绕过预算。AI 模型配置先保持现有平台级受限入口；实例只引用获授权模型策略，不复制密钥。

## 5. 权限与运行路由

管理权限与使用权限独立：app.create、app.manage、app.publish、app.lifecycle、app.use 是逻辑权限动作，首版可将现有 config_admin 映射为本租户管理员，后续扩展应用级授权。可见角色控制目录及使用入口，数据权限由业务适配器强制执行；隐藏字段不能代替后端字段权限。

服务端每次先认证租户，再按作用域查应用和操作权限；对其他租户及无权查看的应用统一 404，已可见但缺操作权限返回 403。发布内容中的角色不能扩大当前操作者可授予的权限范围。模型不修改访问策略、绑定、生命周期或类型。

运行请求显式指定 application_id，先验证发布状态及 app.use，再调用已注册类型适配器。应用内规则优先，匹配度低时才按既有策略使用 AI；不同应用的问法不混用。全局聊天的候选应用来自用户获授权目录，无法唯一定位时澄清，不仅凭相似问句跨应用执行。

课程适配器继续从当前身份获取学生身份，不接受管理员配置或 AI 输出覆盖身份。两个课程实例即使共享能力，也不能越过各自的数据绑定和用户行权限。

## 6. 生命周期、发布与升级

生命周期 active / disabled / archived 与“尚未发布 / 有未发布修改 / 已发布”分开。新建后为 active 且无发布，只能在管理端配置和预览，不进入用户可用目录。名称、说明、使用角色随配置发布生效；停用是独立的即时受审计命令，不能等候发布，也不能因回滚配置自动恢复。

disabled 阻止新业务请求；archived 隐藏常规列表并禁止配置修改，保留历史，首版不硬删除。未来审批类型必须定义停用后的在途实例策略：默认阻止新发起，已有任务按原发布及当前授权受控办理；类型未定义策略就不能提供该生命周期操作。

暂存校验 expected_revision；发布、回滚同时绑定 expected_revision/current_release_id 和幂等键，原子写入快照、指针、审计及 Outbox。预览凭据绑定应用、修订、配置摘要、类型摘要、能力目录摘要及校验器版本；依赖或草稿变化则重新校验，不能复用其他应用的预览结果。

同键同请求重试返回已持久结果，同键不同请求返回 409；原结果不能覆盖更晚的草稿。回滚只允许同租户同应用历史，仍校验当前能力和支持版本，并产生新发布，不修改历史。

类型代码升级通过平台部署；实例类型版本升级通过“转换草稿 → 展示差异 → 预览回归 → 确认发布”。不批量静默改实例，不支持原地改变正在执行的流程定义。平台应保留被发布/任务引用的旧运行适配器；缺失时展示明确阻塞，不降级到任意可用类型版本。首版只有课表，审批实例版本绑定是后续类型的交付条件。

## 7. 管理接口与工作台

下列接口为待实现契约，不能当作现有可调用地址：

| 接口 | 用途 |
| --- | --- |
| GET /api/v1/admin/application-types | 本租户可查看类型、版本、安装状态及配置区声明 |
| GET/POST /api/v1/admin/applications | 分页搜索管理目录 / 按类型创建实例 |
| GET /api/v1/admin/applications/{id} | 实例、生命周期、草稿及发布状态 |
| GET/PUT /api/v1/admin/applications/{id}/draft | 读取 / 按 expected_revision 暂存整个封装 |
| POST /api/v1/admin/applications/{id}/preview | 校验与隔离预览，返回发布检查摘要 |
| POST /api/v1/admin/applications/{id}/publish | 按预览绑定及幂等键发布 |
| POST /api/v1/admin/applications/{id}/rollback | 校验目标归属后创建恢复版本 |
| POST /api/v1/admin/applications/{id}/lifecycle | 按 lifecycle_revision 幂等停用/恢复/归档 |
| GET /api/v1/admin/applications/{id}/releases、/audit | 当前应用的历史与审计 |
| …/{id}/questions、/design/context、/design/preview、/design/accept | 仅类型支持时提供，沿用受控问法/提案语义 |
| GET /api/v1/applications | 仅返回当前用户可用的已发布实例 |

创建请求只包含可创建的 type_id、精确版本、模板 ID 和基本信息；服务端生成应用 ID 和默认配置，创建命令也需幂等键。不允许由客户端提交任意类型清单。列表使用稳定排序和游标；首次默认选择第一页第一项，空列表显示创建引导。直接链接定位 application_id，不依赖列表序号。

右侧通用页固定为基础信息、访问范围、发布记录；专属页由类型声明，例如查询的结果展示/问法，表单的字段/提交，审批的表单/流程，数据看板的指标/图表。AI 设计仅在类型支持且已授权时显示；模型未配置时说明原因，手工配置继续可用。

切换应用时处理未暂存内容和未知写入结果，取消旧读取，并用 application_id+请求序号过滤晚到响应。缓存、表单状态、待接受提案和发布弹窗都按实例隔离；不得把 A 的结果显示到 B。创建和切换不自动调用 AI。

## 8. AI 开发与生成边界

已有类型：AI 按类型 Schema 生成配置提案，不生成执行代码。上下文和提案额外绑定 tenant（仅服务端保存）、application_id、type_id/version/digest、草稿 revision/hash、能力目录摘要、允许操作和请求 ID；接受时全部复核。旧 course profile 中固定 schedule 的约束保持旧接口使用，多应用接口必须版本化扩展后才能接受其他 ID，不直接放宽旧校验。

新类型：AI 可以协助开发清单、Schema、编辑器、服务端适配器、迁移和测试，交付物经代码审查与平台构建部署后才注册。注册表、权限、能力绑定和类型安装状态由服务端维护，模型不能自己启用类型。表单/审批可复用原 application-design/v1 内部结构，不另造相互矛盾的字段和流程协议；通用应用包转换由类型适配层负责。

## 9. 迁移与实施顺序

1. **契约冻结**：根据本设计增加类型清单/实例封装 Schema、有效与反例、接口契约。本文 JSON 只是设计示例，不声称已通过新 Schema。
2. **类型注册及管理壳**：注册课程类型；前端按类型装配右侧页，不引入无运行能力的类型。
3. **数据库扩展**：新增 application，相关表增加应用维度；每租户既有课表生成唯一映射。保持旧发布 ID、内容、摘要、修订、问法 ID 和幂等历史，不重写快照。
4. **旧课表兼容**：旧路径固定映射该租户唯一 legacy schedule 实例；新路径明确指定 UUID，两者使用同一状态与锁。旧 ID schedule 保留别名；不双写独立状态。旧发布包装成运行期视图，新发布才使用新封装；记录配置格式判别，禁止解析失败后随意回退。
5. **隔离后再开放创建**：完成跨应用测试和数据范围验证后，开放支持的课程模板创建。原课表保持可用，新实例默认未发布。新增课程实例的数据绑定尚未实现时只展示阻塞原因，不开放可用承诺。
6. **第二类型验证**：实现表单类真实提交闭环，证明注册、动态配置、运行、权限和发布可复用，再进入审批类。

迁移分阶段 expand/backfill/约束/切换，针对实际 openGauss 验证 DDL、索引和锁影响。回填必须唯一且可重复，历史审计/Outbox 无法确定归属的记录进入核查，不猜测跨应用归属；旧数据单课表的假设须先核验。迁移前后比对状态、发布和问法摘要，冲突停止。回切必须考虑新封装和新实例，旧程序不认识这些数据时不能直接降级；首版切换保留兼容开关和备份，禁止承诺数据库自动逆向回滚。

## 10. 必须通过的验收

- 同租户两应用及不同租户：草稿、发布、问法、审核次数、审计、缓存、Outbox、提案、回滚均不串用；伪造对象 ID 或关联 ID 无法越界。
- A 发布/审核不改变 B revision、规则和发布指针；A/B 相同幂等键互不冲突，同应用重复请求不重复写入。
- 工作台切换期间晚到响应、AI 生成、失败重试、未保存内容均保持正确应用归属。
- 新建未发布不出现在用户可用目录；停用立即拒绝新请求，配置回滚不恢复生命周期；使用权限和数据权限独立生效。
- 类型/编辑器/能力缺失、版本摘要不符及跨版本迁移失败都明确阻塞；不得静默选择其他适配器。
- 旧课表路径与新路径读写同一实例；迁移前后原规则回归、发布幂等、问法审核、退出及用户数据隔离全部通过。
- 测试默认模拟模型、不产生真实付费调用；预览不写业务数据，不修改用户现有草稿或发布。

首个交付范围是“课程类型注册 + 多应用存储隔离 + 动态工作台 + 旧课表兼容”。2026-10-20按同一协议增量接入platform.form与platform.leave-approval：各自的精确Schema/清单、配置适配器、受审阅编辑器、运行界面和业务命令；复用身份、发布、审计和隔离。它们是受限登记与单级请假样板，不代表任意表单/审批引擎或插件市场。实际限制与入口见[运行手册](../runbooks/assistant-requirements.md)。

新增能力应声明使用已有发布快照的方式、业务草稿与正式记录的区别、暂停新业务时已有任务的处理，以及原请求核对策略。不得只在工作台登记一个编辑器而缺少运行命令、权限、旧版本解释和异常恢复。类型源码发生变化须先满足版本规范；禁止为了让旧实例通过而覆盖其已保存type_digest。
