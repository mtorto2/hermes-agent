from plugins.memory.holographic.retrieval import FactRetriever
from plugins.memory.holographic.store import MemoryStore


FACT = (
    "Matt prefers Hermes long-term structured memory/fact capture to be "
    "highly discerning and conservative rather than automatically saving "
    "lots of low-value session details."
)
QUERY = "discerning conservative memory fact capture low-value session details"


def test_retriever_search_handles_hyphenated_plain_language_query(tmp_path):
    store = MemoryStore(tmp_path / "memory_store.db")
    fact_id = store.add_fact(
        FACT,
        category="user_pref",
        tags="memory,hermes,fact-store,preference",
    )
    retriever = FactRetriever(store)

    results = retriever.search(QUERY)

    assert [result["fact_id"] for result in results] == [fact_id]


def test_store_search_handles_hyphenated_plain_language_query(tmp_path):
    store = MemoryStore(tmp_path / "memory_store.db")
    fact_id = store.add_fact(
        FACT,
        category="user_pref",
        tags="memory,hermes,fact-store,preference",
    )

    results = store.search_facts(QUERY)

    assert [result["fact_id"] for result in results] == [fact_id]


def test_retriever_search_preserves_unicode_terms_that_casefold_expand(tmp_path):
    store = MemoryStore(tmp_path / "memory_store.db")
    fact_id = store.add_fact(
        "Matt likes cafés near Straße des 17. Juni.",
        category="user_pref",
        tags="unicode,search",
    )
    retriever = FactRetriever(store)

    results = retriever.search("Straße cafés")

    assert [result["fact_id"] for result in results] == [fact_id]
