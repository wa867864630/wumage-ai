# P0 基线、检查与恢复

本工具只保护当前开发基线，不发布应用，不改用户草稿、发布记录和类型摘要。`version.json` 的 `0.2.0` 是开发版本声明，不代表已部署版本。真实源码摘要、迁移修订、类型摘要与配置发布关系通过检查报告分别记录。

## 2026-10-20 实施边界

- 已建立源码清单、归档完整性校验和临时目录恢复工具。源码归档排除依赖、dist、本地配置、密钥和数据库内容；恢复演练不覆盖工作区。
- 已建立八张应用表的一致性只读快照、加密存储、迁移重放及隔离Schema逐行恢复比对。实际开发数据的隔离恢复已通过；原Schema不执行恢复写入。
- 已建立统一检查入口和版本关系报告。检查结果与源码摘要绑定，运行中断不会记作成功；具体结果以 `.local/checks/` 最新报告为准。
- Git初始化成功，但写入 `.git/config` 和 `.git/index.lock` 被执行环境拒绝，尚无基线提交。源码归档是恢复副本，不能替代Git历史、分支管理和代码审查。
- 生产构建此前受 `spawn EPERM` 阻塞；旧dist不属于本次源码。真实浏览器/移动端验收、异机恢复、异地备份和完整数据库灾备尚未完成。

## 运行检查

从仓库根目录执行，使用已安装依赖的项目Python；Windows示例：

```powershell
.venv/Scripts/python.exe scripts/run_checks.py --profile quick
.venv/Scripts/python.exe scripts/run_checks.py --profile full --live
.venv/Scripts/python.exe scripts/run_checks.py --profile quick --build --output .local/checks/build.json
.venv/Scripts/python.exe scripts/project_status.py --database --output .local/version-evidence.json
```

quick包含契约、前端类型、SSR/HTTP和应用Schema；full另含隔离数据库后端、DOM操作和合成问句评估。`--live`仅检查本机演示服务，创建并撤销测试会话；`--build`显式执行生产前端构建。默认不会调用真实模型。DOM不等于真实浏览器验收。

应用Schema检查需要Ajv 8，DOM检查需要jsdom 25；当前检查脚本支持 `APPLICATION_TYPES_AJV_PATH` 和 `WORKBENCH_JSDOM_PATH` 指向现有模块。模块缺失会失败，不静默跳过。本机尝试离线补齐根依赖时遭遇pnpm store归属不一致，未强制重装；新环境仍须先安装这些检查依赖，当前不声称全新安装已验证。

逐项耗时、退出码、日志位置和源码摘要保存在报告中。运行时不要继续编辑源码；摘要变化会使结果失败。日志和报告位于忽略目录，分享前自行检查，不能提交本地凭证。

## 源码归档与演练

```powershell
.venv/Scripts/python.exe scripts/baseline.py capture
.venv/Scripts/python.exe scripts/baseline.py verify --archive .local/baselines/source-<摘要>.zip
.venv/Scripts/python.exe scripts/baseline.py drill --archive .local/baselines/source-<摘要>.zip
```

capture返回内容寻址ZIP路径、摘要、文件数及真实Git状态。verify检查路径、文件预算、重复项和逐文件哈希。drill只向新建 `.tmp/baseline-drills/source-*` 目录恢复并逐字节比对，随后清理。哈希提供完整性检查，不是来源签名。归档不含本地运行配置，不能单独恢复生产服务。

## 应用数据备份与演练

```powershell
.venv/Scripts/python.exe scripts/database_baseline.py capture --report .local/backups/capture.json
.venv/Scripts/python.exe scripts/database_baseline.py drill --archive .local/backups/application-<编号>.backup.secret --key .local/backups/restore-key.secret
```

capture在REPEATABLE READ、READ ONLY事务中读取当前0006修订及八张表：application、config_state、config_release、config_audit、outbox_event、question_candidate、biz_student、biz_course_schedule。备份包含全部租户的这些应用数据，应按敏感备份保管。Fernet加密并验证落盘后可解密，恢复密钥不输出到终端。

drill要求迁移文件摘要与备份一致，只创建随机 `baseline_drill_<32位十六进制>` Schema，执行迁移、装载数据、逐行核对并删除该临时Schema。不提供覆盖原数据库的命令；真实事故恢复需要维护窗口、目标环境核验和单独操作方案。演练会短暂创建数据库对象，需要开发数据库建Schema权限。

加密文件和恢复密钥必须成对保留；当前同目录存放只是本机开发便利，不能抵御整台主机失陷或磁盘丢失。需要由运维分别妥善保存备份、密钥和必要本地配置，并在异机再演练。当前环境未发现pg_dump/gs_dump，工具不备份角色、扩展、服务器配置、外部系统、缓存或模型凭证，不能称为完整数据库灾备。

## 开发与部署门槛

新增业务前仍需补齐可写Git环境中的基线提交和分支。不得绕过Git目录权限或伪造提交号。已有类型注册摘要含人工实现版本，新增版本证据中的源码实现摘要不会重写历史类型绑定；类型多版本升级仍是后续工作。

正式部署还要求对应源码的生产构建、产物校验、真实浏览器核心操作、目标数据库兼容及异机恢复证据。当前工具全部通过也不自动批准部署；报告始终明确 `production_ready: false`。
