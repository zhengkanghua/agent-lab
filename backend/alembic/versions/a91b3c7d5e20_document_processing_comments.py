"""补全文档处理的表与字段说明，消除迁移和 ORM 的注释差异。"""

from alembic import op

revision = "a91b3c7d5e20"
down_revision = "f7c1d2e3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只写 PostgreSQL 注释；不改变字段、约束或业务数据。
    op.alter_column("document_deletions", "cutoff_date", comment="保留期清理的截止时刻；为空表示用户按明确 ID 删除文档。")
    op.alter_column("document_deletions", "objects", comment="冻结的原件引用及逐项删除确认，失败时保留恢复依据。")
    op.alter_column("document_processing_records", "document_id", comment="所属 Document；同一 Document 可保留多个来源和采用记录。")
    op.alter_column("document_processing_records", "source_kind", comment="file、freshrss 或 manual。")
    op.alter_column("document_processing_records", "state", comment="处理阶段。")
    op.alter_column("document_processing_records", "source_object_key", comment="S3 私有原件对象键。")
    op.alter_column("document_processing_records", "source_object_version", comment="S3 对象版本标识。")
    op.alter_column("document_processing_records", "source_sha256", comment="原始字节 SHA-256。")
    op.alter_column("document_processing_records", "source_size", comment="原始字节数。")
    op.alter_column("document_processing_records", "index_target", comment="采用决定冻结的索引写入目标。")
    op.alter_column("document_processing_records", "index_location", comment="重建的物理目标、原 Alias 目标和原指向；用于发布核验与人工恢复。")
    op.alter_column("document_processing_records", "created_at", comment="记录首次写入 PostgreSQL 的时间。")
    op.alter_column("document_processing_records", "updated_at", comment="记录最后一次通过 ORM 更新的时间。")
    op.create_table_comment("document_processing_records", "文档原件接收、解析预览、索引候选与失败恢复记录。", existing_comment=None)
    op.alter_column("document_review_records", "decision", comment="adopt、reject 或 retry。")
    op.alter_column("document_review_records", "decision_source", comment="automatic 或 manual。")
    op.alter_column("document_review_records", "content_snapshot", comment="作出决定时的正文与格式；之后修正草稿不会改写该结论依据。")
    op.alter_column("document_review_records", "created_at", comment="记录首次写入 PostgreSQL 的时间。")
    op.alter_column("document_review_records", "updated_at", comment="记录最后一次通过 ORM 更新的时间。")
    op.create_table_comment("document_review_records", "文档候选采用、拒绝及自动采用审核结论。", existing_comment=None)
    op.alter_column("document_versions", "created_at", comment="记录首次写入 PostgreSQL 的时间。")
    op.alter_column("document_versions", "updated_at", comment="记录最后一次通过 ORM 更新的时间。")
    op.create_table_comment("document_versions", "文档已采用版本的不可变正文、结构与 Chunk 快照。", existing_comment=None)
    op.alter_column("documents", "management_revision", comment="管理修改的并发修订；接收候选不推进正式业务版本。")
    op.alter_column("documents", "latest_processing_id", comment="最近接收的候选记录；不会改变当前已采用正文。")
    op.alter_column("documents", "draft_processing_id", comment="最新人工草稿；来源更新不会覆盖。")
    op.alter_column("documents", "current_index_instance_id", comment="当前正式读取的物理索引实例；重建不改写已采用历史。")
    op.alter_column("documents", "manual_review_required", comment="主动复核、人工修正或拒绝后，新来源必须人工确认。")
    op.alter_column("documents", "usage_status", comment="active、rejected 或 deleting；候选处理不会改变正式可见性。")
    op.alter_column("knowledge_bases", "visibility_revision", comment="索引可见性修订；候选写入意图、采用、停止使用与回收时推进。")


def downgrade() -> None:
    op.alter_column("knowledge_bases", "visibility_revision", comment=None)
    op.alter_column("documents", "usage_status", comment="active、rejected 或 deleting。")
    op.alter_column("documents", "manual_review_required", comment=None)
    op.alter_column("documents", "current_index_instance_id", comment=None)
    op.alter_column("documents", "draft_processing_id", comment=None)
    op.alter_column("documents", "latest_processing_id", comment=None)
    op.alter_column("documents", "management_revision", comment=None)
    op.drop_table_comment("document_versions", existing_comment="文档已采用版本的不可变正文、结构与 Chunk 快照。")
    op.alter_column("document_versions", "updated_at", comment=None)
    op.alter_column("document_versions", "created_at", comment=None)
    op.drop_table_comment("document_review_records", existing_comment="文档候选采用、拒绝及自动采用审核结论。")
    op.alter_column("document_review_records", "updated_at", comment=None)
    op.alter_column("document_review_records", "created_at", comment=None)
    op.alter_column("document_review_records", "content_snapshot", comment=None)
    op.alter_column("document_review_records", "decision_source", comment=None)
    op.alter_column("document_review_records", "decision", comment=None)
    op.drop_table_comment("document_processing_records", existing_comment="文档原件接收、解析预览、索引候选与失败恢复记录。")
    op.alter_column("document_processing_records", "updated_at", comment=None)
    op.alter_column("document_processing_records", "created_at", comment=None)
    op.alter_column("document_processing_records", "index_location", comment=None)
    op.alter_column("document_processing_records", "index_target", comment=None)
    op.alter_column("document_processing_records", "source_size", comment=None)
    op.alter_column("document_processing_records", "source_sha256", comment=None)
    op.alter_column("document_processing_records", "source_object_version", comment=None)
    op.alter_column("document_processing_records", "source_object_key", comment=None)
    op.alter_column("document_processing_records", "state", comment=None)
    op.alter_column("document_processing_records", "source_kind", comment=None)
    op.alter_column("document_processing_records", "document_id", comment=None)
    op.alter_column("document_deletions", "objects", comment=None)
    op.alter_column("document_deletions", "cutoff_date", comment="保留期清理的截止时刻；为空表示用户按明确 ID 删除上传文档。")
