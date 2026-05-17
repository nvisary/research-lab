# mr_zscore_meta — meta-labeled BTC z-score MR

Demo strategy showing how to attach a meta-labeler (López de Prado-style
secondary classifier) to a primary signal. The primary is single-symbol
BTC z-score mean reversion; the meta-labeler is a LogReg trained on
triple-barrier outcomes using regime/vol/momentum features.

## Hypothesis trail

| iter | verdict | hypothesis | result |
|---|---|---|---|
| 1 | BASELINE | initial single-BTC MR + LogReg meta-labeler | — |
