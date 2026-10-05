Handles document indexing and semantic search (RAG).

Tools:
- index_document(path, content) - chunk and index a document
- search_documents(query, top_k=5) - semantic search over indexed documents
- remove_document(path) - remove a document from the index
