# ACMM Run Record v0.1

This directory contains the machine-readable schema and a populated example for ACMM Run
Record v0.1.

A Run Record describes one realized execution of one planned run. It contains execution
status, realized provenance, normalized evidence references, and atomic opportunity
judgments.

Run Records do not contain ACMM aggregate scores, compliance proportions, confidence
intervals, or derived scenario/run-level outcomes.

The schema enforces local structure and controlled vocabularies. The Python semantic
validator additionally checks the record against the Experiment Contract that produced it.
