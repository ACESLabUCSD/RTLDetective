# Prompt Composition and Memory Retrieval

This document describes exactly what is sent to the LLM on every debugging
iteration, where each piece of memory lives on disk, and how the dynamic
memory retrieval works.

The two relevant source files are
[`agent/debug_agent.py`](agent/debug_agent.py) (prompt construction,
retrieval orchestration) and
[`agent/memory_loader.py`](agent/memory_loader.py) (memory loading + token
search).

---

## 1. What the LLM sees on every call

The model receives three things on every `client.chat(...)` call:

1. **`system_prompt`** — set exactly once at the start of `run_agent`, never
   modified afterwards.
2. **`conversation_history`** — accumulated across iterations. The main debug
   conversation appends one user message and one assistant reply per
   iteration. The isolated LLM-selection call in iter 2+ does **not** leak
   into this history.
3. **`user_prompt`** — freshly constructed for the current iteration.

---

## 2. The system prompt (constant for the whole run)

Built by `build_slim_system_prompt(index_text, methodology_text, mode)` in
[`agent/debug_agent.py`](agent/debug_agent.py).

Sections, in order:

| Block                            | Source on disk                                              | Notes                                                                         |
| -------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Role / Verilog basics            | hardcoded in `build_slim_system_prompt`                     | "You are VerilogDebugAgent..." plus core HDL rules                            |
| `=== MEMORY INDEX ===`           | `memory/index.md`                                           | Built offline by `build_memory/build_memory_summary.py`. One entry per doc with sections + retrieve-when hints |
| `=== DEBUGGING METHODOLOGY ===`  | `memory/debugging_manuals/debugging_methodology.md`         | Phase 1-4 methodology                                                          |
| Output format / JSON schema      | hardcoded                                                   | Differs between `--mode regen` (full module) and `--mode patch` (line edits)  |

Both files in the second column are listed in `STATIC_MEMORY_KEYS`. They are
*always* in the system prompt, so the dynamic retrieval below intentionally
excludes them to avoid duplication.

---

## 3. The user prompt

### 3.1 Iteration 1 — `build_initial_user_prompt`

```
Please analyze the following Verilog file for bugs and provide fixes.

=== FILE: <name> ===
<line-numbered source>

=== DESIGN DESCRIPTION ===            (only when --description supplied)
<design description text>

=== STATIC ANALYSIS RESULTS ===
<output of tools/static_analyzer.py>

=== COMPILE & TESTBENCH RESULTS ===
<iverilog compile output + per-test PASS/FAIL>

=== RELEVANT REFERENCE MATERIAL ===   (dynamically retrieved memory)
<markdown sections selected for this iteration>
--- fix_patterns.json ---             (always appended)
<full content of memory/common_patterns/fix_patterns.json>
```

### 3.2 Iteration 2+ — `build_refinement_prompt`

```
This is debugging iteration N.
Your previous fix still has issues. Review the feedback and return the
complete corrected module.

=== YOUR PREVIOUS FIXED CODE ===
<line-numbered fixed code from prev iter>

=== STATIC ANALYSIS ISSUES ===
<fresh static analyzer output on the previous fix>

=== COMPILE & TESTBENCH RESULTS ===
<fresh iverilog + testbench output on the previous fix>

=== RELEVANT REFERENCE MATERIAL ===   (different retrieval strategy, see §5.2)
<carry-over from prev iter ∪ fresh heuristic ∪ isolated LLM selection>
--- fix_patterns.json ---
<full content>
```

---

## 4. Memory layout on disk

The dynamic-retrieval pool lives under `memory/`:

| Path                                                   | Format   | Purpose                                                                 |
| ------------------------------------------------------ | -------- | ----------------------------------------------------------------------- |
| `memory/index.md`                                      | markdown | Pinned in system prompt (`STATIC_MEMORY_KEYS`)                          |
| `memory/debugging_manuals/debugging_methodology.md`    | markdown | Pinned in system prompt (`STATIC_MEMORY_KEYS`)                          |
| `memory/debugging_manuals/common_bug_patterns.md`      | markdown | Catalog of S1-S9, F1-F10, I1-I4 bug patterns. Heuristic-eligible.        |
| `memory/hardware_specs/verilog_language_reference.md`  | markdown | Verilog construct reference (Always Blocks, Data Types, ...). Heuristic-eligible. |
| `memory/hardware_specs/module_templates.md`            | markdown | Correct templates: counter, FSM, shift register, etc. Heuristic-eligible. |
| `memory/hardware_specs/synthesis_constraints.md`       | markdown | Synthesis rules / latch avoidance. Heuristic-eligible.                   |
| `memory/common_patterns/fix_patterns.json`             | JSON     | 16 before/after fix patterns. Always appended on every iteration.        |

`HEURISTIC_MEMORY_DOC_KEYS` in `debug_agent.py` is the whitelist of markdown
docs eligible for dynamic retrieval (everything in `memory/` except the two
pinned ones).

`MemoryLoader.load_all()` walks `memory/`, reads every file, and indexes
markdown files into `MemorySection` objects keyed on H1/H2/H3 headings.

---

## 5. How memory is retrieved each iteration

The retrieval is **token-overlap based**, not embedding/vector based. There
are zero ML/runtime dependencies.

### 5.1 Query construction — `extract_retrieval_queries`

A single deduplicated `list[str]` of query strings is built by merging four
signal sources:

1. **Family-expanded previous bug categories** (iter 2+ only).
   `_family_expansion(previous_bugs)` takes each `previous_bugs[*].category`
   such as `F2` and expands it to the full family `F1..F10`. Same for
   `S*` (S1-S9) and `I*` (I1-I4).

2. **Substring triggers in the static + testbench logs.**
   `_SIGNAL_KEYWORDS` is a hand-curated table of `(trigger_substring,
   query_terms)`. If `trigger_substring` appears (case-insensitive) anywhere
   in `static_report + tb_report`, the corresponding query terms are added.
   Examples:

   | trigger              | seeded queries                                     |
   | -------------------- | -------------------------------------------------- |
   | `sensitivity`        | `F2`, `sensitivity list`, `Sensitivity Lists`      |
   | `truncat`            | `F5`, `bit width truncation`                       |
   | `unknown module`     | `I1`, `Module Instantiation`                       |
   | `not connected`      | `I2`, `Incomplete Port Connection`                 |
   | `out of range`       | `S8`, `Out-of-Bounds`                              |
   | `posedge` / `negedge`| `F2`, `Sensitivity Lists`, `Async Reset`           |

3. **Functional-bug certain seed.**
   `_tb_signals_functional_bug(tb_report)` returns `True` when compilation
   succeeded (no `[COMPILATION FAILED]`) but tests failed (`[FAIL]` line, or
   a `0/N tests` with `N > 0`, or `Pass rate: 0.0%`). When true, the entire
   `F1..F10` family is added to the query list. This handles MEIC cases
   where the log contains essentially no exploitable substring (e.g.
   `accu_func_improper_sensignal` only emits a `timescale inherited`
   warning plus `0/40 tests`).

4. **Design-description keyword mapping.**
   `_DESIGN_DESC_HINTS` is a small static dictionary mapping common module
   names to seed terms. Examples:

   | keyword in description | seeded queries                                              |
   | ---------------------- | ----------------------------------------------------------- |
   | `counter`              | `Counter`, `Parameterized Counter Template`, `Sequential Logic` |
   | `fsm` / `state machine`| `FSM Template`, `Sequential Logic`, `F10`                    |
   | `accumulator`          | `F1`, `F5`, `Sequential Logic`                              |
   | `shift register`       | `Shift Register Template`                                   |
   | `fifo`                 | `F4`, `F8`, `Sequential Logic`                              |
   | `alu`                  | `Combinational Logic`, `Case Statements`, `Operators`       |

### 5.2 Section ranking — `MemoryLoader.search_sections_by_tokens`

The query list is then matched against every indexed markdown section:

1. Tokenize all queries: `re.split(r"\W+", q.lower())`, drop tokens shorter
   than 3 characters, take the union as `query_tokens`.
2. Separately extract bug codes (e.g. `F2`, `S6`) from the queries — these
   are short (2 chars) and would be dropped by step 1, so they are kept in
   their own `bug_codes` set.
3. For each section, build `blob_tokens` by tokenising
   `heading + content[:200]` the same way.
4. **Score = `|query_tokens ∩ blob_tokens|`** (count of distinct query
   tokens that appear in the section). For each `bug_code` whose lowercase
   form is a substring of `heading + content[:200]`, score is incremented
   by 1.
5. Keep sections with score ≥ 1, sort by `(score desc, len(heading) asc)`.
   Ties break in favour of shorter (more specific) headings.

### 5.3 Iteration-specific assembly

Both builders excluded sections in `STATIC_MEMORY_KEYS` and only keep
sections from docs in `HEURISTIC_MEMORY_DOC_KEYS`. The result is capped at
`max_sections = 24`.

#### `build_iter1_memory` (iter 1)

Pure heuristic, no LLM call:

1. Build queries (sources 2, 3, 4 from §5.1 — no `previous_bugs` yet).
2. Token-rank sections.
3. If fewer than 6 sections survived, apply the **floor** (see §5.4).
4. Format the reference block. Always append `fix_patterns.json` full text.

#### `build_iter_n_memory` (iter 2+)

Heuristic + isolated LLM-selection + carry-over, monotonically growing:

1. Re-materialise every `(doc_key, heading)` from
   `previous_meta.section_keys` so iter N never loses what iter N-1 had.
2. Run heuristic again (same as iter 1, but now with family-expanded
   `previous_bugs`).
3. Run an **isolated** LLM-selection call:
   - Snapshot `len(client.conversation_history)`.
   - Call `client.chat(selection_prompt)` (the prompt asks the model to pick
     `{doc_key: [headings] | "*"}` using the index already in its system
     prompt).
   - In a `finally` block, truncate `conversation_history` back to the
     snapshot length, discarding both the selection prompt and the response.
   - Match each requested heading against actual section headings; if a doc
     is requested but none of its headings match, fall back to all sections
     of that doc.
4. Merge: `carry ∪ heuristic ∪ llm_selected` (in that order, deduplicated).
5. Apply the floor if the merged set is still < 6 sections.
6. Format the reference block, always appending `fix_patterns.json`.

### 5.4 The safety floor — `_floor_sections`

Triggered when the merged section count is below `floor_threshold = 6`. It
adds:

- All sections of `memory/debugging_manuals/common_bug_patterns.md`
- These specific sections of `memory/hardware_specs/verilog_language_reference.md`:
  `Data Types`, `Bit Widths and Literals`, `Always Blocks`,
  `Sensitivity Lists`, `Case Statements`, `Continuous Assignments`,
  `Operators`

This guarantees that the LLM never debugs a Verilog module with zero
reference material, even if every other signal source returned nothing.

---

## 6. Observability

Every iteration logs and persists the following to
`report["loop_history"][i]`:

- `memory_doc_keys` — list of doc keys included this iteration
- `memory_section_headings` — list of section headings included
- `memory_bytes` — size in bytes of the formatted reference block
- `memory_source` — `{heuristic: N, llm: N, floor_triggered: bool, query_count: N}`
- `memory_ratios` — `{docs_loaded, docs_total, docs_pct, sections_loaded,
  sections_total, sections_pct, bytes_loaded, bytes_total, bytes_pct}`

Per-iteration stdout also prints a short summary, e.g.:

```
--- Iteration 1 Memory ---
  docs:     4/5     ( 80.0%)
  sections: 22/148  ( 14.9%)
  bytes:    9648/24694 ( 39.1%)
  source:   heuristic=22, llm=0, floor=False, queries=11
```

The denominators come from `MemoryLoader.pool_stats(exclude_keys=STATIC_MEMORY_KEYS)`,
i.e. the size of the *dynamic* pool (excluding the docs already pinned in
the system prompt).
