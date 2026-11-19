# What is new

## 0.29

### The web dashboard

The web dashboard brings together **Now**, **Plans**, **Delivery**, and **Gates** views for activity, plan progress, delivery metrics, and validator outcomes. The **Protocol** view lets you edit supported validator settings, review a diff, and explicitly save the protocol. See the [web dashboard guide](web-dashboard.md) for the views and their limits.

### Models and validators

Configure ordered validator fallback models with `llm.validator.fallback`; operational failures can switch to alternatives without changing verdict behavior. `snodo validate --validator <id> --model <model>` runs a fresh judgement for one validator, while `snodo models --benchmark-run --validator <id> --model <model>` replays and compares a recorded validator. Unchanged validator subjects can reuse verdicts on retries. See the [configuration reference](configuration.md#validator-model-fallbacks), [command reference](command-reference.md#work-and-planning), and [outcomes and recovery guide](outcomes.md#reused-validator-verdicts-and-judges-that-return-no-verdict).

### Usage and logs

Task and plan runs end with a per-role usage table, and `snodo logs <job_id>` appends the same table after text output. `snodo logs --json` provides versioned machine-readable log records, including usage totals grouped by role. See the [run usage summary](following-a-run.md#end-of-run-usage-summary) and [machine interface](machine-interface.md#snodo-logs-id-json).

### Intake

Run `snodo intake --generate-adr` to propose decision records from repository patterns, then accept or reject proposals individually. Accepted records are written with **Proposed** status for review. See [Propose decision records from code](evolving-the-protocol.md#propose-decision-records-from-code).

### Notifications and job outcomes

Job notifications identify success, recovered warnings, human decisions, blocked work, and engine faults; messages include relevant validator context and the authorization command when a human decision is needed. A finished job with exit code 0 is a success even if an earlier attempt halted and a retry resolved it. See [running unattended](running-unattended.md) and [job outcomes](outcomes.md#job-outcomes-beyond-the-five-halts).

### The scikit-learn demo

The `scikit-learn-26194` demo runs governed and bare arms from the same recorded base commit and writes a report and JSON record for each. Run it with `python demo/run_demo.py --model <provider/model>`; see the [demo guide](demo.md) for model setup, arm details, outputs, and verification caveats.
