# Runtime UI Examples

These examples are designed for the runtime step editor in the UI.

Notes:
- Paste raw Python only. Do not include ```repl``` fences.
- The sandbox provides `context`, `state`, and `tool`.
- `context[i][a:b]` or `context[i].slice(a, b, tag="...")` logs spans for citations. See the Span Log tab in the runtime UI.
- Steps that call `tool.queue_llm` or `tool.queue_search` should end with `tool.YIELD`. Resolve tools with `POST /v1/executions/{execution_id}/tools/resolve` using `models.sub_model` set to `gpt-5-mini`, then run the follow-up step that reads `state["_tool_results"]`.
- Search can be disabled, and the local resolver returns empty hits. Handle empty results in follow-up steps.
- Each example is self-contained. Use Reset State between examples.

## Example 01: Preview summary with tools
Goal: Preview a document, queue tools, and finalize with evidence.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
end = min(200, len(doc))
preview = doc.slice(0, end, tag="preview")

state.setdefault("work", {})
state["work"]["preview"] = preview

prompt = "Summarize the preview in one sentence:\n" + preview
tool.queue_llm("preview_summary", prompt, model_hint="sub", max_tokens=120)
tool.queue_search("preview_search", preview[:80], k=5)
tool.YIELD("resolve preview_summary and preview_search")
```

Step 2:
```python
doc = context[0] if len(context) > 0 else None
work = state.get("work", {})
preview = work.get("preview", "")

llm_result = state.get("_tool_results", {}).get("llm", {}).get("preview_summary")
summary = llm_result.get("text") if isinstance(llm_result, dict) else ""

if doc:
    doc.slice(0, min(200, len(doc)), tag="evidence")

final = summary or ("Preview evidence:\n" + preview[:160])
tool.FINAL(final)
```

## Example 02: Head and midpoint outline
Goal: Tag two slices and summarize them with tools.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
length = len(doc)
head_end = min(160, length)
mid_start = length // 2
mid_end = min(mid_start + 160, length)

head = doc.slice(0, head_end, tag="head")
mid = doc.slice(mid_start, mid_end, tag="mid")

state.setdefault("work", {})
state["work"]["head"] = head
state["work"]["mid"] = mid

prompt = "Create a short outline using the excerpts below.\nHEAD:\n" + head + "\nMID:\n" + mid
tool.queue_llm("outline", prompt, model_hint="sub", max_tokens=180)
tool.queue_search("outline_search", head[:80], k=5)
tool.YIELD("resolve outline and outline_search")
```

Step 2:
```python
doc = context[0] if len(context) > 0 else None
work = state.get("work", {})
head = work.get("head", "")

llm_result = state.get("_tool_results", {}).get("llm", {}).get("outline")
outline = llm_result.get("text") if isinstance(llm_result, dict) else ""

if doc:
    doc.slice(0, min(160, len(doc)), tag="cite_head")

final = outline or ("HEAD excerpt:\n" + head[:160])
tool.FINAL(final)
```

## Example 03: Two rounds of tool calls
Goal: Use a subcall to refine search, then draft an answer.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
seed = doc.slice(0, min(200, len(doc)), tag="seed")

state.setdefault("work", {})
state["work"]["seed"] = seed

prompt = "Propose a short search query based on this text:\n" + seed
tool.queue_llm("query_seed", prompt, model_hint="sub", max_tokens=80)
tool.queue_search("seed_search", seed[:80], k=5)
tool.YIELD("resolve query_seed and seed_search")
```

Step 2:
```python
doc = context[0] if len(context) > 0 else None
seed = state.get("work", {}).get("seed", "")

llm_result = state.get("_tool_results", {}).get("llm", {}).get("query_seed")
query = llm_result.get("text") if isinstance(llm_result, dict) else seed[:80]

tool.queue_search("refined_search", query[:80], k=5)

extra = ""
if doc:
    extra = doc.slice(200, min(400, len(doc)), tag="refine")

prompt = "Draft a short answer using these excerpts:\n" + seed + "\n" + extra
tool.queue_llm("draft_answer", prompt, model_hint="sub", max_tokens=200)
tool.YIELD("resolve refined_search and draft_answer")
```

Step 3:
```python
doc = context[0] if len(context) > 0 else None

llm_result = state.get("_tool_results", {}).get("llm", {}).get("draft_answer")
draft = llm_result.get("text") if isinstance(llm_result, dict) else ""

if doc:
    doc.slice(0, min(200, len(doc)), tag="final_evidence")

final = draft or "No draft answer returned."
tool.FINAL(final)
```

## Example 04: Multi-doc triage
Goal: Rank documents, focus on one, and summarize.

Step 1:
```python
doc_count = len(context)
if doc_count == 0:
    tool.FINAL("No documents loaded.")

limit = min(3, doc_count)
previews = []
for i in range(limit):
    doc = context[i]
    snippet = doc.slice(0, min(120, len(doc)), tag=f"doc_{i}_lead")
    previews.append({"doc_index": i, "snippet": snippet})

state.setdefault("work", {})
state["work"]["previews"] = previews

lines = []
for item in previews:
    lines.append(f"doc {item['doc_index']}: {item['snippet']}")

prompt = "Pick the most relevant doc index from the previews.\n" + "\n".join(lines)
tool.queue_llm("rank_docs", prompt, model_hint="sub", max_tokens=120)
tool.queue_search("triage_search", previews[0]["snippet"][:80], k=5)
tool.YIELD("resolve rank_docs and triage_search")
```

Step 2:
```python
work = state.get("work", {})
previews = work.get("previews", [])

llm_result = state.get("_tool_results", {}).get("llm", {}).get("rank_docs")
text = llm_result.get("text") if isinstance(llm_result, dict) else ""
digits = "".join([ch for ch in text if ch.isdigit()])

chosen = 0
if digits:
    candidate = int(digits)
    if candidate >= 0 and candidate < len(context):
        chosen = candidate

if chosen >= len(context):
    chosen = 0

doc = context[chosen]
detail = doc.slice(0, min(240, len(doc)), tag=f"doc_{chosen}_detail")

state.setdefault("work", {})
state["work"]["chosen_doc"] = chosen
state["work"]["detail"] = detail

prompt = "Summarize this evidence:\n" + detail
tool.queue_llm("detail_summary", prompt, model_hint="sub", max_tokens=180)
tool.queue_search("detail_search", detail[:80], k=5)
tool.YIELD("resolve detail_summary and detail_search")
```

Step 3:
```python
work = state.get("work", {})
detail = work.get("detail", "")

llm_result = state.get("_tool_results", {}).get("llm", {}).get("detail_summary")
summary = llm_result.get("text") if isinstance(llm_result, dict) else ""

final = summary or detail[:200]
tool.FINAL(final)
```

## Example 05: Sentence extraction
Goal: Extract a short sentence, interpret it, and cite it.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
window_end = min(800, len(doc))
window = doc.slice(0, window_end, tag="window")

period = window.find(".")
if period == -1:
    end = min(200, len(window))
else:
    end = period + 1

sentence = window[:end]
doc.slice(0, end, tag="sentence")

state.setdefault("work", {})
state["work"]["sentence"] = sentence

tool.queue_llm("interpret_sentence", "Interpret this sentence:\n" + sentence, model_hint="sub", max_tokens=160)
tool.queue_search("sentence_search", sentence[:80], k=5)
tool.YIELD("resolve interpret_sentence and sentence_search")
```

Step 2:
```python
doc = context[0] if len(context) > 0 else None
sentence = state.get("work", {}).get("sentence", "")

llm_result = state.get("_tool_results", {}).get("llm", {}).get("interpret_sentence")
interpretation = llm_result.get("text") if isinstance(llm_result, dict) else ""

if doc and sentence:
    doc.slice(0, min(len(sentence), len(doc)), tag="sentence_cite")

final = interpretation or sentence
tool.FINAL(final)
```

## Example 06: Evidence map
Goal: Build an evidence map across documents.

Step 1:
```python
doc_count = len(context)
if doc_count == 0:
    tool.FINAL("No documents loaded.")

limit = min(2, doc_count)
snippets = []
for i in range(limit):
    doc = context[i]
    snippet = doc.slice(0, min(180, len(doc)), tag=f"doc_{i}_lead")
    snippets.append({"doc_index": i, "snippet": snippet})

state.setdefault("work", {})
state["work"]["snippets"] = snippets

prompt = "Extract 3 key terms from these excerpts:\n"
for item in snippets:
    prompt += f"\ndoc {item['doc_index']}: {item['snippet']}"

tool.queue_llm("key_terms", prompt, model_hint="sub", max_tokens=120)
tool.queue_search("key_term_search", snippets[0]["snippet"][:80], k=5)
tool.YIELD("resolve key_terms and key_term_search")
```

Step 2:
```python
snippets = state.get("work", {}).get("snippets", [])

llm_result = state.get("_tool_results", {}).get("llm", {}).get("key_terms")
terms = llm_result.get("text") if isinstance(llm_result, dict) else ""

evidence = []
for item in snippets:
    evidence.append(f"doc {item['doc_index']} lead: {item['snippet']}")

state.setdefault("work", {})
state["work"]["evidence"] = evidence

prompt = "Synthesize a short note using these evidence lines and terms.\n"
prompt += "Terms:\n" + terms + "\nEvidence:\n" + "\n".join(evidence)

tool.queue_llm("evidence_note", prompt, model_hint="sub", max_tokens=200)
tool.queue_search("evidence_search", terms[:80], k=5)
tool.YIELD("resolve evidence_note and evidence_search")
```

Step 3:
```python
note_result = state.get("_tool_results", {}).get("llm", {}).get("evidence_note")
note = note_result.get("text") if isinstance(note_result, dict) else ""

if len(context) > 0:
    context[0].slice(0, min(120, len(context[0])), tag="final_check")

tool.FINAL(note or "No evidence note returned.")
```

## Example 07: Search-guided quote selection
Goal: Use search hits when available, with a fallback.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
snippet = doc.slice(0, min(220, len(doc)), tag="seed")

state.setdefault("work", {})
state["work"]["seed"] = snippet

tool.queue_llm("topic_hint", "Suggest a short follow-up topic:\n" + snippet, model_hint="sub", max_tokens=80)
tool.queue_search("topic_search", snippet[:80], k=5)
tool.YIELD("resolve topic_hint and topic_search")
```

Step 2:
```python
doc_count = len(context)
seed = state.get("work", {}).get("seed", "")

search_result = state.get("_tool_results", {}).get("search", {}).get("topic_search")
hits = search_result.get("hits") if isinstance(search_result, dict) else []

quote = ""
if hits:
    hit = hits[0]
    doc_index = hit.get("doc_index", 0)
    start = hit.get("start_char", 0)
    end = hit.get("end_char", 0)
    if doc_index < doc_count and end > start:
        doc = context[doc_index]
        quote = doc.slice(start, end, tag="search_hit")

if not quote and doc_count > 0:
    doc = context[0]
    quote = doc.slice(0, min(160, len(doc)), tag="fallback_quote")

state.setdefault("work", {})
state["work"]["quote"] = quote

tool.queue_llm("quote_answer", "Use this quote as evidence:\n" + quote, model_hint="sub", max_tokens=180)
tool.queue_search("quote_search", quote[:80], k=5)
tool.YIELD("resolve quote_answer and quote_search")
```

Step 3:
```python
llm_result = state.get("_tool_results", {}).get("llm", {}).get("quote_answer")
answer = llm_result.get("text") if isinstance(llm_result, dict) else ""

tool.FINAL(answer or "No answer returned.")
```

## Example 08: Cross-doc comparison
Goal: Compare two documents and cite evidence.

Step 1:
```python
count = len(context)
if count == 0:
    tool.FINAL("No documents loaded.")

first = context[0].slice(0, min(160, len(context[0])), tag="doc0")
second = ""
if count > 1:
    second = context[1].slice(0, min(160, len(context[1])), tag="doc1")

state.setdefault("work", {})
state["work"]["doc0"] = first
state["work"]["doc1"] = second

prompt = "Compare the two excerpts and list any differences.\nDOC0:\n" + first
if second:
    prompt += "\nDOC1:\n" + second

tool.queue_llm("compare_docs", prompt, model_hint="sub", max_tokens=200)
tool.queue_search("compare_search", first[:80], k=5)
tool.YIELD("resolve compare_docs and compare_search")
```

Step 2:
```python
chosen = 0
if len(context) > 1 and state.get("work", {}).get("doc1"):
    chosen = 1

doc = context[chosen]
detail = doc.slice(0, min(240, len(doc)), tag=f"doc{chosen}_detail")

state.setdefault("work", {})
state["work"]["detail"] = detail

tool.queue_llm("comparison_summary", "Summarize the key points using this evidence:\n" + detail, model_hint="sub", max_tokens=180)
tool.queue_search("comparison_search", detail[:80], k=5)
tool.YIELD("resolve comparison_summary and comparison_search")
```

Step 3:
```python
llm_result = state.get("_tool_results", {}).get("llm", {}).get("comparison_summary")
summary = llm_result.get("text") if isinstance(llm_result, dict) else ""

tool.FINAL(summary or "No comparison summary returned.")
```

## Example 09: Tool status gate
Goal: Check tool status before drafting a final note.

Step 1:
```python
if len(context) == 0:
    tool.FINAL("No documents loaded.")

doc = context[0]
snippet = doc.slice(0, min(180, len(doc)), tag="status_seed")

state.setdefault("work", {})
state["work"]["snippet"] = snippet

tool.queue_llm("status_llm", "Summarize the snippet:\n" + snippet, model_hint="sub", max_tokens=140)
tool.queue_search("status_search", snippet[:80], k=5)
tool.YIELD("resolve status_llm and status_search")
```

Step 2:
```python
snippet = state.get("work", {}).get("snippet", "")
statuses = state.get("_tool_status", {})

llm_status = statuses.get("status_llm", "")
search_status = statuses.get("status_search", "")
status_note = f"llm={llm_status}, search={search_status}"

doc = context[0] if len(context) > 0 else None
if doc:
    doc.slice(0, min(120, len(doc)), tag="status_cite")

prompt = "Create a final note.\nStatus: " + status_note + "\nSnippet:\n" + snippet
tool.queue_llm("status_final", prompt, model_hint="sub", max_tokens=180)
tool.queue_search("status_verify", snippet[:80], k=5)
tool.YIELD("resolve status_final and status_verify")
```

Step 3:
```python
llm_result = state.get("_tool_results", {}).get("llm", {}).get("status_final")
final_text = llm_result.get("text") if isinstance(llm_result, dict) else ""

tool.FINAL(final_text or "No final note returned.")
```

## Example 10: Structured report
Goal: Build a multi-section report with evidence.

Step 1:
```python
doc_count = len(context)
if doc_count == 0:
    tool.FINAL("No documents loaded.")

state.setdefault("report", {"sections": {"overview": "", "evidence": "", "open_questions": ""}})

leads = []
limit = min(3, doc_count)
for i in range(limit):
    doc = context[i]
    lead = doc.slice(0, min(140, len(doc)), tag=f"report_doc_{i}")
    leads.append({"doc_index": i, "lead": lead})

state["report"]["leads"] = leads

prompt = "Draft a 3 section outline (overview, evidence, open questions) using these leads:\n"
for item in leads:
    prompt += f"\ndoc {item['doc_index']}: {item['lead']}"

tool.queue_llm("report_outline", prompt, model_hint="sub", max_tokens=200)
tool.queue_search("report_search", leads[0]["lead"][:80], k=5)
tool.YIELD("resolve report_outline and report_search")
```

Step 2:
```python
report = state.get("report", {})

outline_result = state.get("_tool_results", {}).get("llm", {}).get("report_outline")
outline = outline_result.get("text") if isinstance(outline_result, dict) else ""
report["outline"] = outline

evidence = ""
if len(context) > 0:
    doc = context[0]
    evidence = doc.slice(0, min(240, len(doc)), tag="report_evidence")

report.setdefault("sections", {})
report["sections"]["evidence"] = evidence

prompt = "Write the evidence section using this excerpt:\n" + evidence
tool.queue_llm("report_evidence", prompt, model_hint="sub", max_tokens=220)
tool.queue_search("report_evidence_search", evidence[:80], k=5)
tool.YIELD("resolve report_evidence and report_evidence_search")
```

Step 3:
```python
report = state.get("report", {})
evidence = report.get("sections", {}).get("evidence", "")
outline = report.get("outline", "")

prompt = "Write an overview based on the outline and evidence.\nOutline:\n" + outline + "\nEvidence:\n" + evidence
tool.queue_llm("report_overview", prompt, model_hint="sub", max_tokens=200)
tool.queue_search("report_overview_search", evidence[:80], k=5)
tool.YIELD("resolve report_overview and report_overview_search")
```

Step 4:
```python
report = state.get("report", {})

overview_result = state.get("_tool_results", {}).get("llm", {}).get("report_overview")
overview = overview_result.get("text") if isinstance(overview_result, dict) else ""
evidence = report.get("sections", {}).get("evidence", "")

if len(context) > 0:
    context[0].slice(0, min(120, len(context[0])), tag="report_final")

final = "Overview:\n" + overview + "\n\nEvidence:\n" + evidence + "\n\nOpen questions:\n- Review remaining documents\n- Resolve missing terms"
tool.FINAL(final)
```
