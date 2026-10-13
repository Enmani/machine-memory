# Storage and retrieval

`memo` owns the authoritative fixed-width `LOG.txt` and binary summaries in
`TREE/`. Log records remain 320 bytes and tree records remain 288 bytes. Query
and relationship operations do not rewrite them. The existing `wake`, `zoom`,
compression, source adapters and project file checks keep their roles.

`<memory-directory>.knowledge.sqlite3` is a separate, standard-library SQLite
sidecar. It stores positions, source observations and decision relationships.
Relationships use complete original text rather than mutable log positions or
hashes. Every access reconciles positions from the raw log, so a chronological
rebuild does not connect a relationship to another memory. Missing endpoints do
not participate in current recall. Back up the sidecar alongside the log and tree:
the source observations and model/manual relationships are not fully derivable.

The duplicate-aware writer shares the log's existing process lock. It writes and
flushes the log before committing the sidecar. If a process stops between those
steps, the next access recovers the durable log entries. Exact duplicate content
is recorded as an observation rather than another log entry. The key keeps the
project, dates written in the text, numbers, paths, punctuation and case; only
whitespace and the trailing conversation reference are normalized.

Semantic search reuses the existing OMP model channel. The model selects
uniformly spaced binary-tree branches, descends into smaller branches, and ranks
original memories. Keyword hits supplement that path. No embedding model or
vector index is installed. Requests have a call budget; exhausted traversal is
reported as partial. Branch summaries can omit a detail, so this is bounded
semantic retrieval rather than an exhaustive full-transcript scan.

Relationship review examines at most 24 pending memories in the existing sync,
in batches of eight. Older candidate pairs come from word matching and nearby
history within the same project. The model must cite exact excerpts and supply
a clear classification; invalid IDs or evidence abort that batch. A confidence
threshold is a filter, not a guarantee of correctness. Review may miss old
paraphrases outside its shortlist. Use semantic search and an explicit relation
for those. Preview is the default for manual review; sync applies validated
results. Removing a model/manual relation leaves every original intact.

No model call holds the memory lock. Results are checked against the current
raw text before relationships are committed or search IDs displayed. A review
failure retains pending work, allows project updates to proceed, and makes the
sync exit unsuccessfully. No new daemon, timer or startup item is created.

Both ordinary project search and semantic project search resolve duplicate and
replacement relations through the global store, then run the existing project
file checks. Raw `wake`, regex `recall` and per-project `history` remain available
for forensic history. A new adopted change should be written explicitly (for
example, “switched back to A” rather than repeating an old decision verbatim).

Archived text is untrusted data. Model requests use the existing tool-less OMP
runner and secret redaction. The configured provider may be remote; local
storage does not make model processing offline. There is no new credentials
store.
