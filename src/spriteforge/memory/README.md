# Memory safety boundary

Secret detection uses heuristics, not guarantees. Known environment credentials,
bearer tokens, private-key headers, and credential-style assignments are rejected;
arbitrary or unlabeled secrets may pass these checks.

Redaction happens at the source before the Librarian sees run data. Producers must
redact run events, tool results, and artifacts before including them in a Librarian
request. The memory writer's detection is a second check, not a replacement for
source redaction. Never submit API keys or secrets as knowledge.
