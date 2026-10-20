# 应用类型扩展契约 v1

状态：课程类型注册、隔离存储、模板工作台和新实例发布运行已接入。运行期绑定随新发布固定类型/能力版本；其他类型、生命周期、类型升级及新实例AI页面提案待实施。依据 [类型扩展与隔离设计](../../../../docs/architecture/application-type-extension.md)。本目录不修改现有课表配置协议和历史发布内容。

## 文件与校验入口

| 文件 | 用途 |
| --- | --- |
| type-manifest.schema.json | 类型标识、精确版本、类型族、编辑区和能力声明 |
| application-envelope.schema.json | 平台配置封装：实例、元数据、使用角色、能力绑定、类型配置 |
| course-config.schema.json | 从现有 DisplayConfig 提取列和规则，不包含旧 application 元数据 |
| course-application.schema.json | 封装与课程专属配置组合，固定类型及版本，要求 schedule_reader 绑定 |
| examples/course-type.json | 课程类型清单，不表示适配器已注册 |
| examples/course-application.json | 虚构实例和绑定 ID，不是用户现有配置 |
| conformance-cases.json | 可执行结构校验用例，针对示例的单项变更 |

Schema 使用 Draft 2020-12，全部 URN 本地注册，禁止下载远程引用。通用封装的 config 只是扩展槽，**单独通过封装校验不表示应用有效**；服务端必须按已安装 type_id/version 选择专属应用 Schema。例如课程实例必须通过 course-application，而非仅 envelope。未知类型或版本直接拒绝，不回退通用结构。

四种版本分别约束封装协议、扩展协议、类型实现和配置内部格式；课程 config.schema_version 仍为字符串 1。manifest.config_schema_id 指向配置内部 Schema，不是整个应用封装。实例不能传 tenant_id、可执行代码、服务地址或密钥；租户和管理权限来自服务端认证。

从仓库根目录运行：

```powershell
node scripts/check_application_types.mjs
```

需要 Ajv 8 的 2020-12 校验器。未在仓库安装时，可设置 APPLICATION_TYPES_AJV_PATH 为已安装的 ajv/dist/2020.js 绝对路径；脚本不下载依赖，不调用模型，不访问数据库。校验器关闭默认值写入，示例必须显式填写必需内容。

## 结构之外必须实现的语义校验

以下是后续服务端验收要求，当前结构脚本不宣称已实现：

| 场景 | 必需行为 |
| --- | --- |
| 编辑区 ID 重复或占用 basic/access/history 等平台页 | 拒绝注册 |
| 相同类型及版本对应不同清单/Schema/实现摘要 | 拒绝注册 |
| 缺少本地编辑器、运行适配器或所需能力 | 阻塞创建/发布，不将清单当作实现 |
| 声明 question_library 但没有问法编辑区/处理器 | 拒绝不完整扩展包 |
| 所有列隐藏、重复列/规则 ID、规则语义冲突 | 复用课程业务验证器拒绝 |
| bindings 引用其他租户或未授权能力 | 拒绝，并在每次运行复核撤销状态 |
| 用 A 的修订/预览/提案发布 B | 拒绝；所有绑定包含 tenant/application/type/draft/目录摘要 |
| 回滚跨应用历史，或引用缺失类型版本 | 拒绝 |
| 规范化重复问法、配额、成本和权限 | 按应用检查，并保留租户总限制 |
| 模型或请求携带重复 JSON 键、超预算内容 | 严格解析层先拒绝，不依赖 JSON Schema |

课程配置 Schema 保留既有结构约束，但日期安全、规范化、非空白名称及授权必须复用服务端验证；不要编写只通过 JSON Schema 就允许发布的路径。旧 course-context/proposal 仍固定 schedule，新实例提案需要独立版本化扩展，不能直接放宽旧协议。

## 后续交互方向

记录本次讨论，暂不实施：管理员主要从模板创建实例，经 AI 引导修改后发布；用户在对话中发现并使用已授权应用，管理员也可通过对话找到设置和数据管理入口。模板是“类型版本＋默认配置＋所需绑定说明”的候选内容，不能夹带凭据或自动授予权限。发布前仍需完成绑定和校验。

对话中的管理入口先做授权能力发现和引导，写入、删除、发布需要展示具体对象和变更后确认；目录搜索结果不构成操作权限。不把管理问法写入普通用户的业务问法规则。本轮不增加模板市场、对话管理路由或执行功能。
