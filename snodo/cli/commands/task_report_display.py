"""Human-readable rendering for ``snodo task report``."""


def validator_stats_payload(validator_stats):
    """Convert aggregated validator statistics to their report JSON shape."""
    return {
        "validators": {
            validator: {
                phase: {
                    "verdict_count": stats.verdict_count,
                    "severity_counts": dict(stats.severity_counts),
                    "reused_count": stats.reused_count,
                    "reused_severity_counts": dict(stats.reused_severity_counts),
                    "block_rate": stats.block_rate,
                }
                for phase, stats in phases.items()
            }
            for validator, phases in validator_stats.validators.items()
        },
        "skipped_events": validator_stats.skipped_events,
    }


def print_task_report(
    *, days, total_completed, total_merged, reviewed_count, accepted_count,
    amended_count, discarded_count, unreviewed_count, rate_pct,
    validator_data, run_data,
):
    """Print review, validator, outcome, and usage statistics."""
    print(f"Human Review Acceptance Rate (Last {days} days)")
    print("-" * 45)
    print(f"Completed tasks (task_complete): {total_completed}")
    print(f"Merged units (task_merged):      {total_merged}")
    rev_pct = (reviewed_count / total_merged * 100.0) if total_merged > 0 else None
    rev_pct_str = f"{rev_pct:.1f}%" if rev_pct is not None else "n/a"
    rate_pct_str = f"{rate_pct:.1f}%" if rate_pct is not None else "n/a"
    print(f"Reviewed tasks:            {reviewed_count} ({rev_pct_str})")
    print(f"  - Accepted unchanged:    {accepted_count}")
    print(f"  - Amended by operator:   {amended_count}")
    print(f"  - Discarded / reverted:  {discarded_count}")
    if unreviewed_count > 0:
        print(f"  - Unreviewed:            {unreviewed_count}")
    print()
    print(f"Unchanged Acceptance Rate: {rate_pct_str} ({accepted_count}/{reviewed_count} reviewed tasks accepted unchanged)")
    print()
    print("Validator Outcomes")
    print("------------------")
    if not validator_data["validators"]:
        print("No validator verdict data in this window.")
    else:
        for validator, phases in sorted(validator_data["validators"].items()):
            for phase, stats in sorted(phases.items()):
                counts = ", ".join(
                    f"{verdict}={count}" for verdict, count in sorted(stats["severity_counts"].items())
                ) or "no verdicts"
                block_rate = f"{stats['block_rate']:.1%}" if stats["block_rate"] is not None else "n/a"
                print(f"{validator} ({phase}): {counts}; block rate {block_rate}")
    print()
    print("Run Outcomes and Usage")
    print("----------------------")
    print(f"Runs: {run_data['task_count']}")
    first_pass = run_data["first_pass_rate"]
    print(f"First-pass rate: {first_pass:.1%}" if first_pass is not None else "First-pass rate: n/a")
    print(f"Outcomes: {run_data['outcomes'] or 'none'}")
    print(f"Halt types: {run_data['halt_types'] or 'none'}")
    totals = run_data["totals"]
    print(f"Cost: ${totals['cost_usd']:.4f} ({totals['unknown_cost_runs']} runs with no measured cost)")
    print(f"Tokens: {totals['tokens']}")
    print(f"Duration: {totals['duration_seconds']:.1f}s")
