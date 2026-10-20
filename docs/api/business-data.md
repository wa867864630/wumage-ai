# 业务数据表与模拟课表

本项目新增业务数据表统一使用 `biz_` 前缀，与配置、发布、审计等系统核心表区分。现有核心表不重命名、不搬迁。表名区分业务归属；`is_mock` 与 `source_tag` 区分数据是否为模拟，不能把有 `biz_` 前缀的数据都当成真实数据。

| 表 | 用途 | 主要字段 |
| --- | --- | --- |
| `biz_student` | 学生业务档案 | `tenant_id`、`id`、`student_no`、`name`、`is_mock`、`source_tag` |
| `biz_course_schedule` | 学生逐日课程安排 | `tenant_id`、`student_id`、`course_date`、`course_name`、`start_time`、`end_time`、`location`、`teacher`、`is_mock`、`source_tag` |

两张表都有“业务数据表、非系统核心表”的数据库注释。学生按租户和 ID 关联，课表查询索引为租户/学生/日期；外键防止学生跨租户引用。模拟数据统一标记 `is_mock=true`、`source_tag='mock-schedule-v1'`。

## 当前已生成的数据

- 租户：`local-demo`。
- 日期：2026-09-01 至 2027-01-31，共 3 名模拟学生、545 条课程安排。
- 学生一：ID `local-demo-student`、学号 `MOCK2026001`；工作日每天 2 节，是当前固定演示身份对应的学生。
- 学生二：ID `mock-student-002`、学号 `MOCK2026002`；工作日每天 1 节。
- 学生三：ID `mock-student-003`、学号 `MOCK2026003`；工作日每天 2 节。
- 周末没有课程；课程、教室、教师和学生均为模拟，不表示真实节假日、校历或单双周安排。

业务表只表示已存储的课程安排；数据库模式不会在查询时自动生成课程。未生成日期返回空课表，未关联学生返回明确错误。其他两个学生用于后续身份及隔离测试，本次不增加在页面任意选择学生的接口。

## 生成与重新执行

初次部署先执行迁移，然后显式生成模拟数据：

```bash
./.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
./.venv/Scripts/python.exe scripts/seed_mock_data.py
```

可指定其他日期范围，单次最多 366 天：

```bash
./.venv/Scripts/python.exe scripts/seed_mock_data.py --start-date 2027-02-01 --end-date 2027-06-30
```

脚本只补充不存在的学生及课程，不删除、不清空、不覆盖已有内容；模拟学生 ID 被非模拟数据占用时中止。重复执行同一范围新增数量为零；已有课程改名、改教师或调整时间后不会被重置。课程 ID 使用稳定生成规则，租户、学生和时段均参与计算。openGauss 将 DATE 解码为 datetime 时也会规范化比较，防止重复插入。

## 应用读取

当前 `config.local.toml` 已切换为：

```toml
[sis]
mode = "database"
```

重启 API 后自然语言和日期查询共用数据库适配器，响应 `data.source` 为 `database_mock`，页面明确显示“数据库模拟课表”。数据库不可用时返回错误，不回退到代码模拟数据。

`mode="mock"` 仍保留为单元测试和无数据库演示使用；`mode="http"` 用于未来教务接口接入。数据库模拟适配器只读取标记为模拟的学生和课程，不宣称已提供真实教务数据。

查看当前演示学生课表的只读 SQL：

```sql
SELECT course_date, course_name, start_time, end_time, location, teacher,
       is_mock, source_tag
FROM biz_course_schedule
WHERE tenant_id = 'local-demo'
  AND student_id = 'local-demo-student'
  AND course_date BETWEEN '2026-10-19' AND '2026-10-23'
ORDER BY course_date, start_time;
```
