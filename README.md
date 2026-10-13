# machine-memory

One durable memory for AI agents on the same machine. Keep a small binary-tree
history, find memories by meaning, retain every conversation source, and track
which decisions are current.

Python 3.10+ and its standard library are enough for storage, exact duplicate
grouping, relationships and keyword queries. Semantic search and conversation
distillation reuse an existing [oh-my-pi](https://github.com/can1357/oh-my-pi)
installation and its configured model account. They do not install embeddings,
a vector database or a background service.

The memory store follows [OptMem](https://github.com/VictorTaelin/OptMem)'s
binary-tree design and file format. This implementation was written independently.
Semantic retrieval, deduplication, decision tracking and project indexes extend
that store without replacing it.

## Install

```powershell
git clone https://github.com/Enmani/machine-memory.git
cd machine-memory
python install.py --instructions
```

This installs `memo` and `memolib` under `~/.optmem` and adds a managed instruction
block to existing Codex, Claude Code and oh-my-pi instruction files. It preserves
the surrounding instructions. Plain `python install.py` installs the tools and
writes a block you can add yourself. Neither command creates a startup item.

On Windows, run the installed tool as `python "$HOME/.optmem/memo"`.
The commands printed by the tool use the correct absolute path.

## Record and find

```powershell
python "$HOME/.optmem/memo" note "[my-project] Adopted SQLite for a single-machine deployment."
python "$HOME/.optmem/memo" wake
python "$HOME/.optmem/memo" recall "SQLite|database"
python "$HOME/.optmem/memo" search "What database did we choose to keep deployment simple?" --project my-project
python "$HOME/.optmem/memo" inspect 0
```

`wake` reads at most 96 history lines: recent memories stay detailed, older ones
collapse into summaries. `zoom <a-b>` opens a summary. `note` prints any pending
compression for the agent to complete with `nap <a-b> "<summary>"`.

Notes begin with `[project]` and fit in 280 UTF-8 bytes. Exact repeated content
across conversations is grouped automatically; source references and original
observation dates remain inspectable. Write changed outcomes, distinct events
and adopted changes explicitly so they are not mistaken for an unchanged fact.

`search` uses the configured model to select tree branches and rank original
memories, including paraphrases with no shared keywords. `--max-calls` bounds its
work, and partial traversal is reported. Summaries and the bounded search can
miss details. `recall` is a model-free regex scan of the original log.

## Decisions and semantic duplicates

```powershell
python "$HOME/.optmem/memo" note "[my-project] Switched to PostgreSQL for shared writes."
python "$HOME/.optmem/memo" relate 1 0 replaces --reason "The new adopted decision supersedes the single-machine setup."
python "$HOME/.optmem/memo" search "Which database are we using?" --project my-project
python "$HOME/.optmem/memo" search "Why did we originally choose SQLite?" --project my-project --history
python "$HOME/.optmem/memo" review
python "$HOME/.optmem/memo" review --apply
python "$HOME/.optmem/memo" unlink 1 0 replaces
```

Relations run from the newer memory to an older one in the same project:

| Kind | Meaning |
| --- | --- |
| `duplicate` | The same fact or event, phrased differently; sources are grouped. |
| `replaces` | An adopted change supersedes an older decision. |
| `enriches` | New information adds to an older memory; both remain valid. |
| `challenges` | The memories disagree and the conflict remains unresolved. |

Review proposes relationships by meaning and requires exact source excerpts.
Manual review previews by default; `--apply` saves validated proposals. Use
`--ids 12 18` to examine specific newer memories. Review uses a shortlist of
similar and nearby project memories, so distant paraphrases can be missed.
Search first and use `relate` when you already know the relevant pair.

Search normally follows replacement links to the current decision. `--history`
includes superseded decisions. No relation deletes or rewrites the original
log, tree or conversation. Use `inspect` to see sources and reasons, and `unlink`
to remove a mistaken model/manual relationship.

## Conversation history

Adapters read Codex, Claude Code and oh-my-pi logs. They keep user messages and
answers, excluding reasoning, tool payloads, instructions and compaction
snapshots. Forked conversations do not repeat their inherited turns.

```powershell
python harvest.py stats
python harvest.py distill --source codex --limit 10
python harvest.py assemble
python harvest.py nap
python harvest.py sync --log
python harvest.py show cx:019e7e85 --grep "database"
```

Distilled notes retain a conversation reference (`cx`, `cc` or `om`) that `show`
resolves to the original transcript. Sync processes quiet conversations, groups
exact duplicates, compresses history, reviews at most 24 pending memories, and
refreshes registered project indexes. Failed relationship review retains pending
work for retry and reports failure. `--no-relations` skips model review.

An optional Windows task runs sync every three hours while logged in:
`python install.py --schedule`. It is opt-in. `--unschedule` removes it. Existing
installations keep their task configuration when the tools are updated.

Web-only conversations need an export adapter. Add one in `harvest/sources.py`
and register it in `ADAPTERS`.

## Project files and evidence

```powershell
python ptree.py register my-project C:/Projects/my-project
python ptree.py sync my-project --ai
python ptree.py wake my-project
python ptree.py find my-project "database setup"
python ptree.py find my-project "how did we simplify deployment?" --semantic
python ptree.py node my-project docs/database.md
python ptree.py verify my-project
```

A project tree indexes current files; its history contains global notes tagged
for that project. File outlines come from titles and exported names. Folder
summaries are updated when their inputs change. Searches show files alongside
memories and flag referenced files that are missing, changed in Git or only present in
an unmerged worktree. Both search modes use current decision relations.

Optional `paper:<id>` and `node:<id>` pointers connect memories to existing
PaperTree and PhD research trees without copying their contents. Configure their
locations in `harvest/links.py` or its environment settings and the instruction
template in `install.py` for your machine.

## Data and model use

The authoritative memory lives in `~/.optmem/memory/LOG.txt` and `TREE/`.
Relationships and source observations live next to it in
`~/.optmem/memory.knowledge.sqlite3`. Back up both: source observations and
relationship judgments are not fully reconstructible from the log. Raw memory
commands continue to work without the sidecar. Relationships survive a log
reorder because they use original text, not mutable line positions.

Storage and keyword queries run locally. Model processing uses the provider
already configured in oh-my-pi, which may be remote. Before sending archived
text, the shared runner redacts recognizable credentials and disables tools,
extensions and project rules. Redaction does not remove every possible private
fact. A model failure is reported; keyword recall remains available.

See [storage and retrieval design](docs/architecture.md) for concurrency,
recovery and retrieval limits.

## Test

```powershell
python -m unittest discover -s tests
```

Tests cover binary-tree compatibility, log adapters, source preservation,
concurrent duplicate writes, semantic branch traversal, relationship evidence,
cycle rejection, log reordering and project file checks. CI runs without a model
account. Real-model acceptance requires an existing OMP login.

MIT licensed. The Nowledge Mem comparison informed feature selection; this
project does not include or require Nowledge Mem software.
