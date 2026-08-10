"""Repository 层：负责领域对象与持久化存储之间的读写边界。

后续会在这里定义 Repository 抽象接口及 SQLite/SQLAlchemy 实现。应用服务只依赖接口，
不直接拼 SQL，从而让数据库异常、空结果和正常结果具有稳定契约。
"""
