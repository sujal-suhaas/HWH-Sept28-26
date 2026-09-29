Our incident agent recalled the right runbook, cited it, and could not name the root cause. No error. Tests green.

One missing function argument.

A helper annotates runbook hits with the cause they treat. It needs the catalog. The parameter was optional, and the path running on every incident omitted it.

Root Cause Hit@1 sat at 0.00 while Fix Hit@1 climbed. That asymmetry was the diagnosis: a model that can't reason fails both, but one holding the fix and no cause was never told the cause. The default was the bug.

Hindsight holds the memory. Memory ON, 4 cutoffs, one pattern: Root Cause Hit@1 0.00 → 1.00 once a confirmed outcome exists to recall. n=1 per cutoff — a ladder, not a rate.

https://github.com/sujal-suhaas/HWH-Sept28-26

#AIAgents #AI #Hindsight #AgentMemory #AIMemory
