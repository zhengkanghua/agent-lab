"""拆掉全部 16 处数据库级外键，引用完整性交给业务层。

**为什么一次做完。** 拆约束本身没有先后依赖（``DROP CONSTRAINT`` 之间不存在建约束那种
循环引用），选一条总迁移是为了**原子性**：不会出现「库里有 8 条约束、代码里 0 条」这种
一半一半的中间态。数据库与 ORM 模型必须同时对齐，否则删父表时到底是数据库先拦还是
业务层先管，取决于那次部署恰好落在迁移的哪一侧。

**只删约束，不动列、不动索引。** 16 个外键列全部保留（类型、``nullable`` 不变），为查询
建的索引——单列的和复合的——一个都不删。老板说的「外键索引」指的是把两张表连起来的
那种约束，不是普通索引。

**连带语义搬到哪里。** ``CASCADE`` 五处改由 Repository/Service 在同一事务内显式删子表；
``RESTRICT`` 六处由业务层自己判断并拒绝（``knowledge_bases``、``sources`` 本来就没有物理
删除路径，对应场景不可达）；``SET NULL`` 五处由业务层显式置空。决策与代价见
``docs/adr/0028``。

**本迁移还要改写一批列注释。** 有 9 个外键列原有注释、7 个为空；那些注释里凡是把
「数据库级联」当成事实写进去的（``agent_threads.user_id``、``access_tokens.user_id``），
拆掉约束后就成了假话，必须一并改掉。7 个空注释补上「业务层维护的逻辑外键，库上无约束」，
让这条信息随 DDL 进库，查库的人也能看到。

Revision ID: d4b7c1e93a58
Revises: b6e2f9047a31
"""

from alembic import op
import sqlalchemy as sa

revision = "d4b7c1e93a58"
down_revision = "b6e2f9047a31"
branch_labels = None
depends_on = None


# 库上真实存在的约束名。项目没有配置 naming_convention，模型里也只有 6 处写了 name=，
# 其余 10 处由 PostgreSQL 自动命名——自动名不能靠推断，这里逐个照抄实际运行的库。
# 每项是 (表名, 约束名)。拆约束不关心 ondelete，那种语义在下面的注释和业务代码里。
FOREIGN_KEYS = (
    ("documents", "documents_source_id_fkey"),
    ("documents", "fk_documents_knowledge_base_id"),
    ("documents", "fk_documents_current_version_id"),
    ("documents", "fk_documents_latest_processing_id"),
    ("documents", "fk_documents_draft_processing_id"),
    ("sources", "fk_sources_knowledge_base_id"),
    ("document_processing_records", "document_processing_records_document_id_fkey"),
    ("document_versions", "document_versions_document_id_fkey"),
    ("document_versions", "document_versions_processing_id_fkey"),
    ("document_review_records", "document_review_records_document_id_fkey"),
    ("document_review_records", "document_review_records_processing_id_fkey"),
    ("document_review_records", "document_review_records_actor_id_fkey"),
    ("agent_threads", "agent_threads_user_id_fkey"),
    ("access_tokens", "access_tokens_user_id_fkey"),
    ("scheduled_job_runs", "fk_scheduled_job_runs_job_id_scheduled_jobs"),
    ("scheduled_job_runs", "scheduled_job_runs_retry_of_fkey"),
)

# 拆约束后 16 个逻辑外键列应有的说明，与 ORM 模型的 ``comment=`` 逐字一致。
LOGICAL_KEY = "业务层维护的逻辑外键，库上无约束。"
COMMENTS = (
    ("documents", "knowledge_base_id", f"Document 实际归属的 KnowledgeBase；{LOGICAL_KEY}"),
    ("documents", "source_id", f"可选的外部来源；人工或文件 Document 可以为空。{LOGICAL_KEY}"),
    ("documents", "current_version_id", f"当前正式可见的 DocumentVersion 身份；{LOGICAL_KEY}"),
    ("documents", "latest_processing_id", f"最近接收的候选记录；不会改变当前已采用正文。{LOGICAL_KEY}"),
    ("documents", "draft_processing_id", f"最新人工草稿；来源更新不会覆盖。{LOGICAL_KEY}"),
    ("sources", "knowledge_base_id", f"来源当前绑定的 KnowledgeBase；为空表示尚未配置。{LOGICAL_KEY}"),
    ("document_processing_records", "document_id",
     f"所属 Document；同一 Document 可保留多个来源和采用记录。{LOGICAL_KEY}"),
    ("document_versions", "document_id", f"所属 Document；{LOGICAL_KEY}"),
    ("document_versions", "processing_id", f"产生本版本的候选处理记录；{LOGICAL_KEY}"),
    ("document_review_records", "document_id", f"所属 Document；{LOGICAL_KEY}"),
    ("document_review_records", "processing_id", f"本次决定针对的候选处理记录；{LOGICAL_KEY}"),
    ("document_review_records", "actor_id", f"人工拍板的操作者；自动决策为空。{LOGICAL_KEY}"),
    ("agent_threads", "user_id",
     f"该会话所属的 users.id；{LOGICAL_KEY}删账号时由业务层清理归属记录。"),
    ("access_tokens", "user_id",
     f"该登录 Token 所属的 users.id；{LOGICAL_KEY}删账号时由业务层撤销。"),
    ("scheduled_job_runs", "job_id", f"原周期配置；删除配置后由业务层置空。{LOGICAL_KEY}"),
    ("scheduled_job_runs", "retry_of",
     f"人工重试所关联的原失败执行；{LOGICAL_KEY}仍被关联的记录由 prune_history 保护。"),
)

# 建回约束时用的 ondelete。走 downgrade 相当于回到「数据库兜底」那套语义，所以原样复原。
ON_DELETE = {
    ("documents", "documents_source_id_fkey"): "RESTRICT",
    ("documents", "fk_documents_knowledge_base_id"): "RESTRICT",
    ("documents", "fk_documents_current_version_id"): "SET NULL",
    ("documents", "fk_documents_latest_processing_id"): "SET NULL",
    ("documents", "fk_documents_draft_processing_id"): "SET NULL",
    ("sources", "fk_sources_knowledge_base_id"): "RESTRICT",
    ("document_processing_records", "document_processing_records_document_id_fkey"): "CASCADE",
    ("document_versions", "document_versions_document_id_fkey"): "CASCADE",
    ("document_versions", "document_versions_processing_id_fkey"): "RESTRICT",
    ("document_review_records", "document_review_records_document_id_fkey"): "CASCADE",
    ("document_review_records", "document_review_records_processing_id_fkey"): "RESTRICT",
    ("document_review_records", "document_review_records_actor_id_fkey"): "SET NULL",
    ("agent_threads", "agent_threads_user_id_fkey"): "CASCADE",
    ("access_tokens", "access_tokens_user_id_fkey"): "CASCADE",
    ("scheduled_job_runs", "fk_scheduled_job_runs_job_id_scheduled_jobs"): "SET NULL",
    ("scheduled_job_runs", "scheduled_job_runs_retry_of_fkey"): "RESTRICT",
}

# (表, 本地列, 被引用表, 被引用列)。顺序与被引用表必须与原约束一致，回滚才能一一对上。
COLUMNS = {
    ("documents", "documents_source_id_fkey"): ("source_id", "sources", "id"),
    ("documents", "fk_documents_knowledge_base_id"): ("knowledge_base_id", "knowledge_bases", "id"),
    ("documents", "fk_documents_current_version_id"): ("current_version_id", "document_versions", "id"),
    ("documents", "fk_documents_latest_processing_id"): ("latest_processing_id", "document_processing_records", "id"),
    ("documents", "fk_documents_draft_processing_id"): ("draft_processing_id", "document_processing_records", "id"),
    ("sources", "fk_sources_knowledge_base_id"): ("knowledge_base_id", "knowledge_bases", "id"),
    ("document_processing_records", "document_processing_records_document_id_fkey"): ("document_id", "documents", "id"),
    ("document_versions", "document_versions_document_id_fkey"): ("document_id", "documents", "id"),
    ("document_versions", "document_versions_processing_id_fkey"): ("processing_id", "document_processing_records", "id"),
    ("document_review_records", "document_review_records_document_id_fkey"): ("document_id", "documents", "id"),
    ("document_review_records", "document_review_records_processing_id_fkey"): ("processing_id", "document_processing_records", "id"),
    ("document_review_records", "document_review_records_actor_id_fkey"): ("actor_id", "users", "id"),
    ("agent_threads", "agent_threads_user_id_fkey"): ("user_id", "users", "id"),
    ("access_tokens", "access_tokens_user_id_fkey"): ("user_id", "users", "id"),
    ("scheduled_job_runs", "fk_scheduled_job_runs_job_id_scheduled_jobs"): ("job_id", "scheduled_jobs", "id"),
    ("scheduled_job_runs", "scheduled_job_runs_retry_of_fkey"): ("retry_of", "scheduled_job_runs", "id"),
}

# 回滚时恢复的原文说明。None 表示这一列在原状态里没有说明。
ORIGINAL_COMMENTS = {
    ("documents", "knowledge_base_id"): "Document 实际归属的 KnowledgeBase。",
    ("documents", "source_id"): "可选的外部来源；人工或文件 Document 可以为空。",
    ("documents", "current_version_id"): "当前正式可见的 DocumentVersion 身份。",
    ("documents", "latest_processing_id"): "最近接收的候选记录；不会改变当前已采用正文。",
    ("documents", "draft_processing_id"): "最新人工草稿；来源更新不会覆盖。",
    ("sources", "knowledge_base_id"): "来源当前绑定的 KnowledgeBase；为空表示尚未配置。",
    ("document_processing_records", "document_id"): "所属 Document；同一 Document 可保留多个来源和采用记录。",
    ("document_versions", "document_id"): None,
    ("document_versions", "processing_id"): None,
    ("document_review_records", "document_id"): None,
    ("document_review_records", "processing_id"): None,
    ("document_review_records", "actor_id"): None,
    ("agent_threads", "user_id"): "该会话所属的 users.id；删除账号时级联删除归属记录。",
    ("access_tokens", "user_id"): "该登录 Token 所属的 users.id；删除用户时级联撤销。",
    ("scheduled_job_runs", "job_id"): None,
    ("scheduled_job_runs", "retry_of"): None,
}

def _orphans(connection):
    """逐个逻辑外键数孤儿行，返回「关系描述（几行）」的列表。

    查询从 ``COLUMNS`` 现推，不另抄一份——16 条手写 SQL 是 16 个写错的机会，而这里
    要的信息（哪张表、哪一列、指向谁）在 ``COLUMNS`` 里本来就有。

    ``IS NOT NULL`` 对可空列是必须的：``NULL`` 表示「没有引用」，不该算孤儿；对非空列
    恒真、无害，所以两种列可以共用一条模板。
    """

    found = []
    for (table, name), (column, referred_table, referred_column) in COLUMNS.items():
        count = connection.execute(sa.text(
            f"SELECT count(*) FROM {table} c WHERE c.{column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {referred_table} p WHERE p.{referred_column} = c.{column})"
        )).scalar_one()
        if count:
            found.append(f"{table}.{column} → {referred_table}.{referred_column}（{count} 行）")
    return found


def upgrade() -> None:
    # 1、先拆约束。DROP CONSTRAINT 之间没有依赖，按表分组只是为了读起来顺。
    for table, name in FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")

    # 2、再改列说明。13 处内容是新增的「逻辑外键」表述，3 处是把「数据库级联」那句
    #    改写掉——它们不在「7 个空注释」之列，容易被「已经有 comment 了」漏过去。
    for table, column, comment in COMMENTS:
        op.alter_column(table, column, comment=comment)


def downgrade() -> None:
    """把 16 处约束建回去，并把列说明恢复成拆之前的样子。

    **前提：库里不能有孤儿数据。** 建约束时已有行必须满足它，否则整条回滚会在
    建到某一条时抛约束冲突，而报错只给一个约束名，看不出是哪张表哪一行坏了。
    所以下面先逐个关系数一次孤儿，把「哪几个关系脏了、各有几行」写进异常消息，
    让人能直接去清；确认干净之后再建约束。

    注意回滚**不会**替你删掉孤儿——删数据是不可逆动作，不该藏在一次 schema 回滚里。
    """

    # 1、先体检。有孤儿就直接失败并指名道姓，不要等建约束时报一个看不懂的冲突。
    dirty = _orphans(op.get_bind())
    if dirty:
        raise RuntimeError(
            "回滚前必须先清理孤儿数据，否则建不回外键约束。"
            "以下逻辑外键存在指向不存在父行的记录：" + "；".join(dirty)
        )

    # 2、把列说明恢复原样。放在建约束之前，两类失败互不遮挡。
    for table, column in ((table, column) for table, column, _ in COMMENTS):
        op.alter_column(table, column, comment=ORIGINAL_COMMENTS[(table, column)])

    # 3、建回约束。名字、被引用列和 ondelete 都与拆之前一致。
    for table, name in FOREIGN_KEYS:
        column, referred_table, referred_column = COLUMNS[(table, name)]
        op.create_foreign_key(
            name,
            table,
            referred_table,
            [column],
            [referred_column],
            ondelete=ON_DELETE[(table, name)],
        )
