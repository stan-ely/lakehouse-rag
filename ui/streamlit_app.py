"""Demo UI: ask as a persona, and see what the persona is allowed to see.

    mise run ui        # streamlit run ui/streamlit_app.py

The point of the demo is the persona switch. The same question asked as Exec and as Sales
returns an answer with citations in one case and a refusal in the other, and the refusal text
is identical to the one you get for a question nobody can answer — a caller cannot tell "this
does not exist" from "this exists and is not yours".

This talks to the API over HTTP like any other client, with a persona JWT. It never imports
the retrieval stack: a UI that could query Postgres directly would prove nothing about the
access control in the service.
"""

import os

import httpx
import streamlit as st

from app.auth import mint_token
from app.settings import get_settings
from app.tokens import PERSONAS

API_URL = os.environ.get("RAG_API_URL", "http://localhost:8000")
TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Each is a golden-set question, so the demo shows behaviour the evaluation has checked.
EXAMPLES = [
    "What is the nightly hotel cap for domestic travel?",
    "How many shipments were late last month?",
    "What is the base salary range for pay band L7?",
    "How many invoices are overdue, and how overdue must one be for the account to go on "
    "credit hold?",
]


def ask(question: str, persona: str) -> tuple[int, dict[str, object]]:
    token = mint_token(get_settings(), f"demo-{persona}", PERSONAS[persona])
    response = httpx.post(
        f"{API_URL}/query",
        json={"question": question},
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    body: dict[str, object] = response.json() if response.content else {}
    return response.status_code, body


def render(body: dict[str, object]) -> None:
    if body.get("refused"):
        st.warning(str(body.get("answer", "")))
        st.caption(f"reason: {body.get('refusal_reason')}")
    else:
        # Streamlit renders $...$ as LaTeX, which swallows a range like "$230,000 to $320,000".
        st.markdown(str(body.get("answer", "")).replace("$", r"\$"))

    citations = body.get("citations") or []
    if isinstance(citations, list) and citations:
        st.subheader("Citations")
        for citation in citations:
            st.markdown(
                f"**[{citation['index']}] {citation['title']}** "
                f"`{citation['doc_version']}` — {citation['source_uri']}"
            )

    if sql := body.get("sql"):
        st.subheader("Generated SQL")
        st.code(str(sql), language="sql")
    if sql_error := body.get("sql_error"):
        st.error(f"SQL failed: {sql_error}")

    usage = body.get("usage") or {}
    cost = body.get("cost_usd")
    timings = body.get("timings_ms") or {}
    left, middle, right = st.columns(3)
    left.metric("Route", str(body.get("route") or "docs"))
    if isinstance(usage, dict):
        middle.metric("Tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    right.metric("Cost", f"${cost:.5f}" if isinstance(cost, int | float) else "unknown")
    if isinstance(timings, dict) and timings:
        st.caption(" · ".join(f"{k} {v:.0f} ms" for k, v in timings.items()))
    st.caption(f"request {body.get('request_id')}")


def main() -> None:
    st.set_page_config(page_title="Larkspur Logistics assistant", page_icon="📦")
    st.title("Larkspur Logistics assistant")

    persona = st.sidebar.selectbox("Ask as", sorted(PERSONAS), index=sorted(PERSONAS).index("exec"))
    st.sidebar.caption("Groups: " + (", ".join(PERSONAS[persona]) or "none — every query refuses"))
    st.sidebar.divider()
    st.sidebar.caption(f"API: {API_URL}")

    question = st.text_input("Question", placeholder=EXAMPLES[0])
    st.caption("Try: " + " · ".join(f"*{e}*" for e in EXAMPLES[1:]))

    if not st.button("Ask", type="primary") or not question.strip():
        return
    with st.spinner("Thinking..."):
        try:
            code, body = ask(question, persona)
        except httpx.HTTPError as exc:
            st.error(f"Could not reach the API at {API_URL}: {exc}")
            return

    if code == 429:
        st.error("Rate limited. Wait a moment and ask again.")
    elif code >= 500:
        st.error(f"The API returned {code}: {body.get('detail', 'unavailable')}")
    elif code != 200:
        st.error(f"The API returned {code}: {body.get('detail')}")
    else:
        render(body)


if __name__ == "__main__":  # Streamlit executes the script as __main__.
    main()
