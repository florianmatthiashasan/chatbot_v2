import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSIST_DIR = os.path.join(BASE_DIR, "faiss_index")  # muss zu build_index.py passen
EMBED_MODEL = "text-embedding-3-large"

# Workaround für FAISS / OpenMP unter macOS
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


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


def build_rag():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ist nicht gesetzt. Bitte in .env hinterlegen oder als Umgebungsvariable setzen.")

    # Embeddings – muss dieselbe Klasse wie beim Indexbau sein
    embeddings = OpenAIEmbeddings(api_key=api_key, model=EMBED_MODEL)

    # FAISS-Index laden
    if not os.path.isdir(PERSIST_DIR):
        raise RuntimeError(f"FAISS-Index nicht gefunden in: {PERSIST_DIR}")

    print(f"[{datetime.now()}] Lade FAISS-Index aus {PERSIST_DIR} ...")
    vectordb = FAISS.load_local(
        PERSIST_DIR,
        embeddings,
        allow_dangerous_deserialization=True,
    )

    # Retriever erstellen
    retriever = vectordb.as_retriever(search_kwargs={"k": 4})

    # LLM
    llm = ChatOpenAI(
        api_key=api_key,
        model="gpt-4o-mini",  # ggf. anpassen
        temperature=0,
    )

    def _prepare_history(raw_history: Iterable[Dict[str, Any]] | None) -> List[Any]:
        """Normalisiere History-Einträge in LangChain-Messages."""
        if not raw_history:
            return []

        cleaned: List[Any] = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or item.get("sender") or "").strip().lower()
            content = (item.get("content") or item.get("text") or "").strip()
            if not content:
                continue
            if role in {"assistant", "ai", "bot"}:
                cleaned.append(AIMessage(content=content))
            else:
                cleaned.append(HumanMessage(content=content))

        # Letzte Nachrichten reichen völlig, begrenze Tokens
        return cleaned[-8:]

    def _rewrite_question(question: str, history_msgs: List[Any]) -> str:
        """Mache eine Folgefrage eigenständig verständlich, falls History vorhanden."""
        if not history_msgs:
            return question

        prompt = [
            SystemMessage(
                content=(
                    "Du formulierst Folgefragen aus einem Chatverlauf so um, "
                    "dass sie ohne den Verlauf verständlich sind. "
                    "Füge keine neuen Informationen hinzu."
                )
            ),
            *history_msgs,
            HumanMessage(
                content=(
                    "Formuliere die letzte Nutzerfrage eigenständig verständlich. "
                    "Lass alle Vorgeschichte weg, aber erhalte die Intention. "
                    f"Letzte Frage: {question}"
                )
            ),
        ]

        try:
            rewritten = llm.invoke(prompt)
            new_q = getattr(rewritten, "content", "") if not isinstance(rewritten, str) else rewritten
            return new_q.strip() or question
        except Exception as exc:
            print("Konnte Frage nicht umformulieren:", exc)
            return question

    def _history_as_text(history_msgs: List[Any]) -> str:
        lines = []
        for msg in history_msgs:
            role = "Nutzer" if isinstance(msg, HumanMessage) else "Assistent"
            lines.append(f"{role}: {msg.content}")
        return "\n".join(lines)

    def answer(question: str, history: Iterable[Dict[str, Any]] | None = None) -> str:
        history_msgs = _prepare_history(history)
        effective_question = _rewrite_question(question, history_msgs)

        docs = retriever.invoke(effective_question)

        if not docs:
            return "Ich habe dazu leider keine Informationen im Index gefunden."

        context_parts = []
        for i, d in enumerate(docs, start=1):
            meta = d.metadata or {}
            src = meta.get("source", "unbekannte Quelle")
            context_parts.append(f"[{i}] (Quelle: {src})\n{d.page_content}\n")

        context = "\n\n".join(context_parts)

        system_prompt = (
            "Du bist ein Assistent, der NUR auf Basis des folgenden Website-Kontexts antwortet. "
            "Wenn etwas nicht im Kontext steht, sage ehrlich, dass du es nicht weißt. "
            "Antworte präzise und auf Deutsch. "
            "Verwende Zitate aus dem Kontext, um deine Antworten zu untermauern. "
            "Wenn du eine Quelle angibst, nutze die eckigen Klammern mit der Nummer, z.B. [1]. "
            "Antworte in ganzen Sätzen."
        )

        history_text = _history_as_text(history_msgs) if history_msgs else ""
        history_block = (
            f"Bisheriger Chatverlauf (nur zur Klärung von Pronomen, keine Wissensquelle):\n{history_text}\n\n"
            if history_text
            else ""
        )

        user_prompt = (
            f"{history_block}"
            f"Kontext aus dem Index:\n{context}\n\n"
            f"Aktuelle Frage: {question}\n\n"
            "Antwort (nimm nur Infos aus dem Kontext):"
        )

        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        resp = llm.invoke(messages)
        return resp.content

    return answer


def main():
    qa = build_rag()
    print("RAG-Chat über die gescrapte Website.\nTippe 'exit' zum Beenden.\n")

    history: List[Dict[str, str]] = []

    while True:
        try:
            user_input = input("Du: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTschüss 👋")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Tschüss 👋")
            break

        try:
            answer = qa(user_input, history=history)
            print(f"\nBot:\n{answer}\n")
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": answer})
        except Exception as e:
            print(f"\nFehler: {e}\n")


if __name__ == "__main__":
    main()
