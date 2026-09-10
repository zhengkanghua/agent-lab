"""项目当前处理规格；配置与索引契约不从某个具体切分器类导入默认值。"""

from agent_lab.knowledge.processing.contracts import ChunkSpecification

PARSER_ID = "docling-text-2.126.0-v1"
CHUNK_ALGORITHM = "docling-hybrid-2.95.0-v1"
TOKENIZER_ID = "BAAI/bge-m3"
TOKENIZER_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_MAX_TOKENS = 512


def chunk_specification(max_tokens: int = DEFAULT_MAX_TOKENS) -> ChunkSpecification:
    return ChunkSpecification(algorithm=CHUNK_ALGORITHM, tokenizer=TOKENIZER_ID,
                              tokenizer_revision=TOKENIZER_REVISION, max_tokens=max_tokens)
