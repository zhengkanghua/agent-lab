# 后端代码与注释规范

适用范围：`backend/` 下的 Python 代码。前端不适用本文，见根 `AGENTS.md`。

## 注释

给代码补上注释，让第一次用到它的人能看懂：这个文件、类、方法做什么，为什么这么写，
边界和代价在哪。方法体里也可以按步骤标 `# 1、查询数据` `# 2、用数据算出…`，让人顺着
序号就能看完整个流程，不用先读懂每一行。步骤别标太细，一个方法大致十步以内；标不完
往往是方法本身该拆了。写成 docstring 还是 `#`、写多少，按哪种读起来顺手定。

实体类和 schema 类值得多写几句——它们被引用的次数最多，比如表的业务粒度、主键和业务
唯一键、`relationship` 和真实数据库列的区别、metadata 里哪些字段会进 Embedding。

本节只是帮助理解代码，不是硬规则，不能影响业务和技术上该怎么写；冲突时按业务和技术来。

注释默认中文，框架类名、字段名和业内固定术语保留英文原名。注释与实现冲突算缺陷。

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
  没给就只写一句话，别按 `Args/Returns/Raises` 展开。项目里几条路由都是前一种做法。
- LangChain `@tool` 装饰的函数的 docstring：整份原样送进模型上下文（默认
  `parse_docstring=False`），内部异常类名之类的东西会跟着泄漏出去。
