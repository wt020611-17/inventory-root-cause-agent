# 呆滞料智能体

一个基于**纯合成制造业数据**的呆滞料识别与库存根因分析项目。项目通过确定性代码计算库存指标、追踪业务关系和执行归因规则，再由 Agent 负责理解问题、编排工具并组织带证据的回答。

> 项目目录沿用原名“智能库存归因Agent”；“呆滞料智能体”是当前业务名称，两者指同一项目。

## 当前状态

当前 **Phase 3 工程基线已完成**。2026-08-14 已完成 DeepSeek-compatible LLM 适配器、自然语言解析、会话补参、三工具编排、证据校验、一次受控重试、最大步数保护、模板降级和自然语言 API。真实 DeepSeek 调用等待本人配置 API Key 后补验。

已完成：

- 项目目标、范围边界与角色定义。
- MVP 呆滞料口径、三类候选根因和待确认业务参数。
- 五个持久化实体、最小关系图、数据质量规则与合成场景清单。
- 功能、质量与演示验收标准。
- 工程约束、依赖清单和环境差距盘点。
- 默认业务阈值已接受为合成数据 MVP v0.2 的可配置基线。
- Phase 1 分层骨架、领域枚举、阈值、五个实体和统一结果模型已实现。
- 固定 seed 纯合成数据生成器与整批数据质量检查已实现。
- SQLite/SQLAlchemy 五表持久化、Repository、事务回滚和初始化命令已实现。
- 不依赖 LLM 的库存指标、基础风险、风险清单筛选与金额排序已实现。
- FastAPI `/health`、`/api/v1/analysis`、`/api/v1/risks` 已实现并完成真实 Uvicorn 请求验证。
- 三类确定性根因、受限 NetworkX 证据图和三个强类型 Agent Tool 已实现。
- Phase 3 DeepSeek 配置、Agent State、输入解析、会话存储和完整 LangGraph 工作流已实现。
- `/api/v1/chat` 与 `/api/v1/tools` 已实现；无密钥时核心分析仍可用。
- 207 项自动化测试、Ruff、`pip check` 和 SDK 导入检查已通过。
- Python 3.11.9 隔离环境、精确依赖锁文件、Git 与远程仓库已就绪。

尚未完成：

- 本地 DeepSeek API Key 配置和真实调用验收，以及 Phase 4 评测集和演示页面。

## MVP 能力

1. 识别：按物料与仓库计算库龄、无消耗天数、库存覆盖天数和呆滞金额。
2. 归因：基于确定性规则识别需求下降、超量采购和生产延期三类候选根因。
3. 解释：输出结论、命中规则、指标、关联单据和证据路径。
4. 建议：提供只读、可解释的处置建议，不自动执行报废、调拨或订单修改。

MVP 固定为：5 张业务表、3 类根因、4 类图节点、3 类图关系和 3 个用例级 Agent Tool。销售取消、工程变更、完整 BOM/工序模型、外部图数据库和生产级权限系统均作为后续扩展，不进入首版。

## 项目导航

- 内部总入口：[项目索引](00_项目索引.md)
- 前期决策：[项目完整方案](项目完整方案.md)、[实习资料借鉴映射](实习资料借鉴映射.md)
- 业务与技术设计：[项目章程](docs/00_项目章程.md)、[业务口径与需求](docs/01_业务口径与需求.md)、[数据准备清单](docs/02_数据准备清单.md)、[验收标准](docs/03_验收标准.md)、[技术架构与接口](docs/04_技术架构与接口.md)
- 项目执行：[项目阶段任务清单](项目阶段任务清单.md)、[项目学习与面试地图](项目学习与面试地图.md)
- 成果交付：[项目复盘与成果证据](项目复盘与成果证据.md)

`PREPARATION_CHECKLIST.md` 是早期开工准备的历史记录；后续进度统一维护在“项目阶段任务清单”中。

## 技术路线

```text
Web UI / API
      |
FastAPI + Agent Workflow
      |
用例级工具：风险清单 / 单物料归因 / 证据追踪
      |                                   |
SQLite / SQLAlchemy               NetworkX
      |
纯合成数据
```

核心原则：

- 数值、阈值命中和根因得分由确定性代码产生，LLM 不得改写。
- 每个结论必须能追溯到指标、规则或关系路径。
- `empty`（成功但无数据）与 `error`（执行失败）严格区分。
- 没有 LLM 密钥时，核心分析链路仍可运行。
- 不复制任何公司代码、真实表名、客户数据、凭据或内部地址。

## 环境状态

项目要求 Python 3.11+。独立项目仓库使用 Python 3.11.9；精确依赖版本记录在 `requirements.lock`。

激活 Windows 虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

生成固定 seed SQLite：

```powershell
python scripts/init_db.py --database data/generated/inventory_agent.db --seed 20260812
```

启动 API：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后可访问：

- `GET http://127.0.0.1:8000/health`
- `POST http://127.0.0.1:8000/api/v1/analysis`
- `POST http://127.0.0.1:8000/api/v1/risks`
- `POST http://127.0.0.1:8000/api/v1/chat`
- `GET http://127.0.0.1:8000/api/v1/tools`

配置 DeepSeek（可选；无密钥时自动使用确定性模板降级）：

```powershell
Copy-Item .env.example .env
# 仅在本地 .env 中填写：DEEPSEEK_API_KEY=你的密钥
```

`.env` 已被 Git 忽略。不要把密钥写入源码、文档、命令历史或聊天记录。

运行质量门：

安装开发依赖后，计划统一使用：

```powershell
python -m pytest -p no:cacheprovider
python -m ruff check .
```

当前下一步是本人配置 DeepSeek API Key 后执行一次真实调用验收；无密钥 Phase 3 基线已经可运行。
