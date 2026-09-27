# 后端代码与注释规范

适用范围：`backend/` 下的 Python 代码。前端不适用本文，见 `frontend/AGENTS.md`；仓库级协作规则见根 `AGENTS.md`。

## 注释

给代码补上注释，让第一次用到它的人能看懂：这个文件、类、方法做什么，为什么这么写，
边界和代价在哪。方法体里也可以按步骤标 `# 1、查询数据` `# 2、用数据算出…`，让人顺着
序号就能看完整个流程，不用先读懂每一行。步骤别标太细，一个方法大致十步以内；标不完
往往是方法本身该拆了。写成 docstring 还是 `#`、写多少，按哪种读起来顺手定。

实体类和 schema 类值得多写几句——它们被引用的次数最多，比如表的业务粒度、主键和业务
唯一键、`relationship` 和真实数据库列的区别、metadata 里哪些字段会进 Embedding。

本节只是帮助理解代码，不是硬规则，不能影响业务和技术上该怎么写；冲突时按业务和技术来。

注释默认中文，框架类名、字段名和业内固定术语保留英文原名。注释与实现冲突算缺陷。

## 删父表必须连带子表

库里**没有数据库级外键约束**，引用完整性由业务层维护（根 `AGENTS.md`「业务与数据约束」，
决策与代价见 [ADR 0028](../docs/adr/0028-drop-database-foreign-keys.md)）。所以：

- **删一行父表数据前，先处理指向它的子表行。** 该删的删、该置空的置空，全部放在**同一个事务**
  里。数据库不会拦你，写漏了就是一堆查不到也管不了的孤儿数据。
- **连带逻辑写在各聚合自己的 Repository/Service 里，不新建抽象模块。** 要改这些路径前先确认
  是否还有第二条删除路径，测试也走这些路径，不要绕开它们直接 `session.delete()`。具体是哪几个
  入口、各自连带处理什么，见 `docs/architecture.md` 的「删父表必须连带子表」一节——那份清单是
  唯一的一份，不再在本文重列（两个位置各存一份必然漂移）。
- **不要顺手把 `ForeignKey(...)` 加回模型。** 它同时负责生成库上约束和给 `relationship` 提供
  join 条件，加回来会改变删除行为；去掉它则必须先给受影响的 `relationship` 补
  `primaryjoin`/`foreign_keys`，否则应用第一次 ORM 映射就抛 `NoForeignKeysError`。
- **「删父表必须连带子表」不等于「删任何父表都要有级联」。** `knowledge_bases` 和 `sources`
  没有物理删除路径，它们被引用的场景当前不可达，不需要为此新增判断代码。

## 有些注释会被别处读到

下面这几处写下的字会离开代码库，进到 DDL、前端类型或模型上下文里，写错会影响别的地方。
按事实写准，也别在这里堆给维护者看的话——上一节那些内容换成 `#` 写在函数体里。

- SQLAlchemy 列的 `comment=`：进数据库 DDL。
- Pydantic Field 的 `description`：进 `/openapi.json`，前端 `openapi-typescript` 拿它
  生成类型；用在 Tool 的 `args_schema` 上时还会进模型上下文。
- FastAPI 路由 handler 的 docstring：FastAPI 拿它当接口 `description`，进
  `/openapi.json` 和前端生成的类型。这里有个逃生口：装饰器上显式写 `description=`
  时那份优先，docstring 就完全不进 OpenAPI（`description or cleandoc(__doc__)`）。
  所以路由 handler 想写长 docstring 是可以的，前提是装饰器里给了 `description=`；
  没给就只写一句话，别按 `Args/Returns/Raises` 展开。项目里两种做法都有：检索、账号、Agent
  那几条给了 `description=` 配长 docstring，文档审核、任务那几条只写一句 docstring 或用 `summary=`，
  改 handler 前先看清装饰器。
- LangChain `@tool` 装饰的函数的 docstring：进模型上下文。本项目两个工具都开了
  `parse_docstring=True`，`Args:` 段会被拆成各参数的描述，其余文字整份作为工具描述送进去；
  内部异常类名之类的东西会跟着泄漏出去。
