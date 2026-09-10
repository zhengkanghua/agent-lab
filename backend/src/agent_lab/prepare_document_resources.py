"""显式准备或离线核验文档处理资源，不连接业务服务。"""

import argparse

from agent_lab.config.document_processing import get_document_processing_settings
from agent_lab.knowledge.adapters.tokenizer_resources import prepare_tokenizer_resources, verify_tokenizer_resources
from agent_lab.knowledge.processing.contracts import DocumentProcessingError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验本地文件，不下载。")
    parser.add_argument("--directory", help="默认使用 DOCUMENT_TOKENIZER_PATH。")
    args = parser.parse_args()
    directory = args.directory or get_document_processing_settings().tokenizer_path
    try:
        action = verify_tokenizer_resources if args.check else prepare_tokenizer_resources
        action(directory)
    except DocumentProcessingError as exc:
        parser.exit(1, f"{exc.code}\n")
    except Exception:
        parser.exit(1, "document_resources_prepare_failed\n")
    print("document_resources_ready")


if __name__ == "__main__":
    main()
