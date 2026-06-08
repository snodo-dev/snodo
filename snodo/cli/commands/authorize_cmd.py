"""CLI authorize command — review and sign a pending decision.

FILE: snodo/cli/commands/authorize_cmd.py

Human-only: this command is NOT exposed as an MCP tool.  An LLM
orchestrator cannot mint its own signed record — the human must run
this CLI command to authorize a proposed decision.

The authorize takes ONLY task_id.  The decision content is read from
the stored proposal in session state — never from CLI args.  This is
the human-accountability anchor: the human reviews what the CLI
rendered from stored state and confirms, and the signature seals that
exact content.
"""

import sys

from snodo.infrastructure.paths import require_project_root
from snodo.infrastructure.state import read_state
from snodo.infrastructure.session import SessionManager


def authorize_command(args) -> int:
    """Review and sign a pending decision, or list all pending decisions.

    Args:
        args: Namespace with task_id (optional) and --yes (optional skip prompt).
    """
    task_id = getattr(args, "task_id", "")

    project_root = require_project_root()
    state = read_state(project_root)
    mode = state.current_mode

    if not mode:
        print("Error: No active mode. Run 'snodo mode change <mode>' first.",
              file=sys.stderr)
        return 1

    session_mgr = SessionManager()
    session = session_mgr.get_active_session(mode, project_root)
    if session is None:
        print(f"Error: No active session for mode={mode}", file=sys.stderr)
        return 1

    pending = session.checkpoint.decisions.get("pending_decisions", {})

    # ---- No task_id: list pending decisions ----
    if not task_id:
        reject_all = getattr(args, "reject_all", False)
        if reject_all:
            return _reject_all_decisions(session, pending, session_mgr)
        return _list_pending(session, pending)

    if not isinstance(pending, dict) or task_id not in pending:
        print(f"No pending decision for task {task_id}.",
              file=sys.stderr)
        return 1

    proposal = pending[task_id]
    proposal_type = proposal.get("type", "unknown")

    # ----- RENDER the stored proposal to the human -----
    print(f"Pending decision for task: {task_id}")
    print(f"  Type:   {proposal_type}")
    if proposal_type == "adjudicate":
        print(f"  Validator: {proposal.get('validator_id', '—')}")
        print(f"  Decision:  {proposal.get('decision', '—')}")
    elif proposal_type == "set_model":
        print(f"  Model:  {proposal.get('proposed_model', '—')}")
        print(f"  Scope:  {proposal.get('scope', '—')}")
    print(f"  Justification: {proposal.get('justification', '—')}")
    print(f"  Proposed by:   {proposal.get('proposed_by', '—')}")
    print()

    # ----- Human confirmation -----
    skip_prompt = getattr(args, "yes", False)
    if not skip_prompt:
        try:
            answer = input("Authorize this decision? [y/N/r] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 1
        if answer == "r":
            return _reject_decision(task_id, proposal, session, session_mgr)
        if answer != "y":
            print("Cancelled.", file=sys.stderr)
            return 1

    # ----- Mint RS256 record -----
    from snodo.infrastructure.decisions import signing_issuer
    from snodo.core.interfaces import ValidatorResult

    issuer = signing_issuer()

    if proposal_type == "adjudicate":
        validator_result = ValidatorResult(
            validator_id=proposal["validator_id"],
            severity="warn",
            justification=proposal["justification"],
        )
        record = issuer.issue_record(
            task_ref=task_id,
            validator_id=proposal["validator_id"],
            validator_result=validator_result,
            decision=proposal["decision"],
            justification=proposal["justification"],
            resolved_by="human",
        )
        # Persist to decision_records (existing path)
        records = session.checkpoint.decisions.get("decision_records", [])
        if not isinstance(records, list):
            records = []
        records.append(record.jwt)
        session_mgr.update_decision(
            session.session_id, "decision_records", records,
        )

    elif proposal_type == "set_model":
        from snodo.infrastructure.decisions import DecisionRecord
        from datetime import datetime as dt, timezone

        now = dt.now(timezone.utc)
        payload = {
            "iat": now,
            "task_ref": task_id,
            "type": "set_model",
            "proposed_model": proposal["proposed_model"],
            "scope": proposal["scope"],
            "justification": proposal["justification"],
            "resolved_by": "human",
        }

        jwt_str = issuer.sign_payload(payload)

        record = DecisionRecord(
            jwt=jwt_str,
            task_ref=task_id,
            decision="set_model",
            justification=proposal["justification"],
            resolved_by="human",
            issued_at=now.isoformat(),
        )

        # Write to authorized_decisions so the consumer can find it
        auth = session.checkpoint.decisions.get("authorized_decisions", [])
        if not isinstance(auth, list):
            auth = []
        auth.append(record.jwt)
        session_mgr.update_decision(
            session.session_id, "authorized_decisions", auth,
        )

    else:
        print(f"Error: Unknown proposal type '{proposal_type}'", file=sys.stderr)
        return 1

    # ----- Consume the proposal -----
    del pending[task_id]
    session_mgr.update_decision(
        session.session_id, "pending_decisions", pending,
    )

    print("Decision authorized and signed (RS256).")
    print(f"  Record ID: {issuer._record_id(record.jwt)}")
    return 0


def _list_pending(session, pending: dict) -> int:
    """Print a table of all pending decisions in the session."""
    if not isinstance(pending, dict) or not pending:
        print("No pending decisions in current session.")
        return 0

    print(f"Pending decisions in session {session.session_id} ({session.mode}):")
    print()

    rows = []
    for tid, proposal in sorted(pending.items()):
        ptype = proposal.get("type", "unknown")
        if ptype == "adjudicate":
            target = proposal.get("validator_id", "—")
        elif ptype == "set_model":
            target = proposal.get("scope", "—")
        else:
            target = "—"
        justification = proposal.get("justification", "—")
        if len(justification) > 40:
            justification = justification[:37] + "..."
        rows.append((tid, ptype, target, justification))

    col_widths = [
        max(len(r[0]) for r in rows),
        max(len(r[1]) for r in rows),
        max(len(r[2]) for r in rows),
    ]
    col_widths = [max(w, len(h)) for w, h in zip(
        col_widths, ["TASK ID", "TYPE", "TARGET", "JUSTIFICATION"]
    )]
    col_widths[0] = max(col_widths[0], 7)
    col_widths[1] = max(col_widths[1], 4)
    col_widths[2] = max(col_widths[2], 6)

    header = (
        f" {'TASK ID':<{col_widths[0]}}  "
        f"{'TYPE':<{col_widths[1]}}  "
        f"{'TARGET':<{col_widths[2]}}  "
        f"JUSTIFICATION"
    )
    print(header)
    print("-" * len(header))

    for tid, ptype, target, justification in rows:
        print(
            f" {tid:<{col_widths[0]}}  "
            f"{ptype:<{col_widths[1]}}  "
            f"{target:<{col_widths[2]}}  "
            f"{justification}"
        )

    print()
    print("Run: snodo authorize <task_id> to review and sign.")
    return 0


def _reject_all_decisions(session, pending: dict, session_mgr) -> int:
    """Bulk reject all pending decisions.

    Shows the list, prompts for confirmation, then mints a signed
    reject record for each and clears them all.
    """
    if not isinstance(pending, dict) or not pending:
        print("No pending decisions to reject.")
        return 0

    _list_pending(session, pending)

    count = len(pending)
    try:
        answer = input(f"\nReject all {count} pending decisions? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 1
    if answer != "y":
        print("Cancelled.", file=sys.stderr)
        return 1

    rejected = 0
    for task_id in sorted(pending.keys()):
        proposal = pending[task_id]
        _reject_decision(task_id, proposal, session, session_mgr)
        rejected += 1

    print(f"\n{rejected} decision(s) rejected and recorded.")
    return 0


def _reject_decision(task_id: str, proposal: dict, session, session_mgr) -> int:
    """Mint a signed reject record and clear the pending decision.

    Returns 0 on success.
    """
    from snodo.infrastructure.decisions import signing_issuer, DecisionRecord
    from snodo.core.interfaces import ValidatorResult
    from datetime import datetime as dt, timezone

    issuer = signing_issuer()
    proposal_type = proposal.get("type", "unknown")
    now = dt.now(timezone.utc)

    if proposal_type == "adjudicate":
        validator_result = ValidatorResult(
            validator_id=proposal.get("validator_id", ""),
            severity="warn",
            justification=proposal.get("justification", ""),
        )
        record = issuer.issue_record(
            task_ref=task_id,
            validator_id=proposal.get("validator_id", ""),
            validator_result=validator_result,
            decision="reject",
            justification=proposal.get("justification", ""),
            resolved_by="human",
        )
    else:
        payload = {
            "iat": now,
            "task_ref": task_id,
            "type": proposal_type,
            "decision": "reject",
            "justification": proposal.get("justification", ""),
            "resolved_by": "human",
        }
        if proposal_type == "set_model":
            payload["proposed_model"] = proposal.get("proposed_model", "")
            payload["scope"] = proposal.get("scope", "")
        jwt_str = issuer.sign_payload(payload)
        record = DecisionRecord(
            jwt=jwt_str,
            task_ref=task_id,
            decision="reject",
            justification=proposal.get("justification", ""),
            resolved_by="human",
            issued_at=now.isoformat(),
        )

    # Persist to decision_records
    records = session.checkpoint.decisions.get("decision_records", [])
    if not isinstance(records, list):
        records = []
    records.append(record.jwt)
    session_mgr.update_decision(
        session.session_id, "decision_records", records,
    )

    # Consume the proposal
    pending = session.checkpoint.decisions.get("pending_decisions", {})
    if isinstance(pending, dict) and task_id in pending:
        del pending[task_id]
        session_mgr.update_decision(
            session.session_id, "pending_decisions", pending,
        )

    print("Decision rejected and recorded.")
    print(f"  Record ID: {issuer._record_id(record.jwt)}")
    return 0
