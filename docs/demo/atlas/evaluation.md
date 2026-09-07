# Atlas: long-document evaluation

**Fictional demonstration. These numbers are invented; they are not measured model performance.**

Recorded: 2026-08-14 08:34:17 UTC. Corpus: 120 invented documents, including 40 long-document queries. Fixed questions; keyword search is the baseline. The first comparison left this subset untested.

| Check | Fictional result |
|---|---|
| Queries losing their source span after chunking | 18 of 40 |
| Decision | Retain the keyword baseline |
| Unresolved condition | Overlap needed to preserve the full evidence span |
| Next step | Repair boundaries and rerun the same evaluation |

The demo Conversation links this output to the corresponding tool result. The file is a fixture; the software did not run this experiment.
