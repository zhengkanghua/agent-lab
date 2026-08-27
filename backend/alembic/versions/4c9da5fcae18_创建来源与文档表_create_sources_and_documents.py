"""创建来源与文档表 create sources and documents

Revision ID: 4c9da5fcae18
Revises: 
Create Date: 2026-08-11 21:36:06.409082

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4c9da5fcae18'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级数据库结构。"""
    # 以下结构由 Alembic 根据 SQLAlchemy metadata 自动生成，并已人工审查。
    op.create_table('sources',
    sa.Column('id', sa.Uuid(), nullable=False, comment='Python 服务生成的来源主键。'),
    sa.Column('provider', sa.String(length=64), nullable=False, comment='数据提供方稳定标识，例如 freshrss_main、bls、sec_edgar。'),
    sa.Column('external_id', sa.String(length=512), nullable=False, comment='来源在提供方中的标识，例如 FreshRSS 的 feed/2。'),
    sa.Column('name', sa.String(length=255), nullable=False, comment='来源展示名称。'),
    sa.Column('feed_url', sa.Text(), nullable=True, comment='RSS/Atom 地址；非 Feed 来源可以为空。'),
    sa.Column('home_url', sa.Text(), nullable=True, comment='来源主页地址。'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录首次写入 PostgreSQL 的时间。'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录最后一次通过 ORM 更新的时间。'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'external_id', name='uq_sources_provider_external_id'),
    comment='外部新闻、政策、财报等文档来源。'
    )
    op.create_table('documents',
    sa.Column('id', sa.Uuid(), nullable=False, comment='Python 服务生成的文档主键。'),
    sa.Column('source_id', sa.Uuid(), nullable=False, comment='关联 sources.id。'),
    sa.Column('external_id', sa.String(length=512), nullable=False, comment='文档在来源中的唯一标识，例如 FreshRSS article id。'),
    sa.Column('document_type', sa.Enum('article', 'press_release', 'economic_release', 'filing', 'research_report', 'policy_document', name='document_type_values', native_enum=False), server_default='article', nullable=False, comment='文档类型，以受代码约束的字符串保存。'),
    sa.Column('title', sa.Text(), nullable=False, comment='文档标题。'),
    sa.Column('url', sa.Text(), nullable=False, comment='原始文档地址。'),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True, comment='来源声明的发布时间。'),
    sa.Column('source_updated_at', sa.DateTime(timezone=True), nullable=True, comment='数据提供方声明的文档更新时间。'),
    sa.Column('authors', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False, comment='规范化作者列表。'),
    sa.Column('labels', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False, comment='业务标签列表，不包含 FreshRSS 已读等状态。'),
    sa.Column('image_urls', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False, comment='正文图片 URL 列表，不保存图片二进制。'),
    sa.Column('content_text', sa.Text(), nullable=False, comment='清洗后的完整正文纯文本。'),
    sa.Column('content_hash', sa.String(length=64), nullable=False, comment='规范化正文的 SHA-256，用于检测内容变化。'),
    sa.Column('processing_status', sa.Enum('pending', 'processing', 'indexed', 'failed', name='processing_status_values', native_enum=False), server_default='pending', nullable=False, comment='当前处理状态，以受代码约束的字符串保存。'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录首次写入 PostgreSQL 的时间。'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='记录最后一次通过 ORM 更新的时间。'),
    sa.CheckConstraint("document_type IN ('article', 'press_release', 'economic_release', 'filing', 'research_report', 'policy_document')", name='ck_documents_document_type'),
    sa.CheckConstraint("processing_status IN ('pending', 'processing', 'indexed', 'failed')", name='ck_documents_processing_status'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_documents_source_external_id'),
    comment='Python 服务管理的统一文档及其当前处理状态。'
    )
    op.create_index('ix_documents_processing_status', 'documents', ['processing_status'], unique=False)
    op.create_index('ix_documents_published_at', 'documents', ['published_at'], unique=False)
    # 自动生成结构结束。


def downgrade() -> None:
    """回滚数据库结构。"""
    # 回滚顺序必须先删除依赖 sources 的 documents 表。
    op.drop_index('ix_documents_published_at', table_name='documents')
    op.drop_index('ix_documents_processing_status', table_name='documents')
    op.drop_table('documents')
    op.drop_table('sources')
    # 回滚结构结束。
