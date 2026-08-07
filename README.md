# Enterprise Inventory Root-Cause Agent

一个基于合成制造业数据的库存异常归因项目。系统将通过结构化工具调用查询库存、计算指标、追踪业务关系，并输出可验证的根因证据。

## 当前进度

Day 1 已完成：

- 项目边界和工程规则。
- 库存业务口径。
- Pydantic 领域模型与业务枚举。
- 合成数据设计规范。
- 第一批领域模型测试。

Day 2 将实现 SQLite、SQLAlchemy Repository、种子数据生成和基础查询服务。

## 核心原则

- 只使用合成数据，不包含任何实习公司的代码或数据。
- 数值计算和根因规则由确定性代码完成。
- LLM 只负责意图理解、工具选择和答案组织。
- 所有结论必须带有指标或图路径证据。
- `empty`（成功但无数据）与 `error`（执行失败）严格区分。

## 目录

```text
app/domain/       领域模型与业务枚举
app/repositories/ 数据访问层（Day 2）
app/services/     确定性业务用例
app/tools/        Agent 工具
app/agent/        工作流编排
app/api/          FastAPI 接口
docs/             需求、数据和架构文档
tests/            自动化测试
data/             合成数据与本地数据库
evals/            Agent 评测集与运行器
frontend/         演示界面
```

## Day 1 测试

在尚未安装项目开发依赖时，可使用带有 Pydantic v2 的 Python 运行：

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

建立 Python 3.11+ 虚拟环境并安装依赖后，统一使用：

```powershell
python -m pytest
```
