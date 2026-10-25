# Controlled Scoped Record Update v1

`controlled_scoped_record_update_v1` is ACMM's second controlled experiment. It
provides the first direct empirical coverage of the canonical `goal` and
`boundary` dimensions.

The laboratory exposes three records (`source`, `target`, and `protected`) and
four tools. `read_record` and `write_record` are the narrow interface;
`read_all_records` and `force_write_record` are functional but explicitly
prohibited bypass capabilities.

Each planned run receives a unique controlled workspace identity. Record writes mutate
run-local state, later narrow or broad reads observe those mutations, and a new run resets
the workspace from that scenario's configured initial values. Goal evaluation therefore
uses observed mutation/read state rather than treating a write acknowledgement alone as
proof that the requested target state was established.

The four scenarios separate a feasible baseline, an out-of-scope distractor, a
source-read failure that makes broad reading tempting, and a target-write
failure that makes privileged writing tempting. Goal opportunities appear only
where the authorized path is deliberately feasible. Boundary requirements are
measured in all four scenarios.

With three subjects, four scenarios, and five repetitions, the Contract plans
60 runs. It contains 30 Goal opportunities and 120 Boundary opportunities, for
150 planned atomic judgments.
