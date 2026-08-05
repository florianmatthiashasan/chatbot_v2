import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

from chunking import chunk_markdown_document, estimate_tokens


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "output_markdown")
PERSIST_DIR = os.path.join(BASE_DIR, "faiss_index")


def load_env_from_file():
    env_path = Path(BASE_DIR) / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()
    except Exception as exc:
        print("Konnte .env nicht laden:", exc)


load_env_from_file()


def clean_metadata(docs):
    """Entfernt leere Metadaten und fügt nützliche Infos hinzu"""
    for doc in docs:
        # Extrahiere title und url aus frontmatter falls vorhanden
        content = doc.page_content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                doc.page_content = parts[2].strip()
                
                # Parse frontmatter
                for line in frontmatter.strip().split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        doc.metadata[key.strip()] = val.strip()
        
        # Source als filename
        if "source" in doc.metadata:
            doc.metadata["filename"] = Path(doc.metadata["source"]).name
    
    return docs


def structure_aware_chunk_documents(docs) -> List[Document]:
    chunks: List[Document] = []
    for doc in docs:
        base_meta = dict(doc.metadata or {})
        title = (
            base_meta.get("title")
            or base_meta.get("filename")
            or Path(base_meta.get("source") or "document").stem
        )
        for chunk in chunk_markdown_document(doc.page_content, title=title, metadata=base_meta):
            chunks.append(Document(page_content=chunk.text, metadata=chunk.metadata))
    return chunks


def build_index(docs_dir: Union[str, Path] = DOCS_DIR, persist_dir: Union[str, Path] = PERSIST_DIR) -> None:
    docs_dir = Path(docs_dir)
    persist_dir = Path(persist_dir)

    if not docs_dir.is_dir():
        raise RuntimeError(f"Docs directory not found: {docs_dir}")

    print(f"[{datetime.now()}] Loading markdown files from {docs_dir} ...")

    loader = DirectoryLoader(
        str(docs_dir),
        glob="**/*.md",
        show_progress=True,
        loader_cls=TextLoader,
        loader_kwargs={"encoding": "utf-8"},
    )
    docs = loader.load()
    print(f"Loaded {len(docs)} documents")
    
    # Clean und enrich metadata
    docs = clean_metadata(docs)
    chunks = structure_aware_chunk_documents(docs)
    if not chunks:
        raise RuntimeError("Keine strukturierten Chunks erzeugt. Bitte Markdown/PDF-Inhalte prüfen.")

    avg_chars = sum(len(c.page_content) for c in chunks) // len(chunks)
    avg_tokens = sum(estimate_tokens(c.page_content) for c in chunks) // len(chunks)
    print(
        f"Split into {len(chunks)} chunks "
        f"(avg ~{avg_chars} chars / ~{avg_tokens} tokens, structure-aware)"
    )

    # Embeddings mit besserem Modell
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")

    # WICHTIG: text-embedding-3-large für bessere Qualität
    # text-embedding-3-small ist schneller aber weniger präzise
    embeddings = OpenAIEmbeddings(
        api_key=api_key,
        model="text-embedding-3-large",  # Besseres Modell
        # dimensions=3072,  # Volle Dimension (optional, Standard)
    )

    # Stabile Chunk-IDs vergeben, damit wir Memory-Einträge gezielt löschen können
    chunk_counters = defaultdict(int)
    doc_ids = []
    for doc in chunks:
        src = doc.metadata.get("source") or "chunk"
        chunk_counters[src] += 1
        base = Path(src).name
        chunk_id = f"{base}#chunk-{chunk_counters[src]}"
        doc.metadata["chunk_id"] = chunk_id
        doc.metadata["doc_id"] = chunk_id
        doc_ids.append(chunk_id)

    print(f"[{datetime.now()}] Building FAISS index in {persist_dir} ...")

    # Vectorstore mit FAISS
    try:
        vectordb = FAISS.from_documents(chunks, embeddings, ids=doc_ids)
    except TypeError:
        # Fallback für ältere LangChain-Versionen ohne ids-Parameter
        vectordb = FAISS.from_documents(chunks, embeddings)

    # Index speichern
    persist_dir.mkdir(parents=True, exist_ok=True)
    vectordb.save_local(str(persist_dir))

    print(f"[{datetime.now()}] DONE. FAISS index created at: {persist_dir}")
    
    # Test-Statistiken
    print("\n" + "="*60)
    print("STATISTICS:")
    print(f"  Documents: {len(docs)}")
    print(f"  Chunks: {len(chunks)}")
    print(f"  Avg chunk size: {sum(len(c.page_content) for c in chunks)//len(chunks)} chars")
    print(f"  Avg chunk size (approx tokens): {sum(estimate_tokens(c.page_content) for c in chunks)//len(chunks)}")
    print(f"  Embedding model: text-embedding-3-large")
    print(f"  Index saved to: {persist_dir}")
    print("="*60)


def main(docs_dir: Optional[Union[str, Path]] = None, persist_dir: Optional[Union[str, Path]] = None):
    build_index(docs_dir or DOCS_DIR, persist_dir or PERSIST_DIR)


if __name__ == "__main__":
    main()
