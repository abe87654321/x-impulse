"""企业知识库 RAG 平台。

模块按限界上下文划分（见 docs/architecture-positioning.md §2.5）：

  知识管道上下文（共享 Document / Chunk / Citation 模型，可互相依赖）
    ingestion   连接器与同步
    parsing     解析与切分
    indexing    向量化与索引
    retrieval   检索与重排
    generation  生成与引用绑定

  独立上下文（各有独立模型，不得反向依赖管道）
    identity    身份与授权
    compliance  合规与数据主体权利
    governance  治理与运营
    audit       审计（只追加，不与任何上下文共享模型）
"""
