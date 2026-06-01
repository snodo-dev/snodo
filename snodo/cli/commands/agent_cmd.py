"""Agent command - Manage agent memory and threads.

FILE: snodo/cli/commands/agent_cmd.py (Task 5.2)
"""

import sys
import time


def agent_command(args) -> int:
    """Manage agent memory and threads."""
    from snodo.infrastructure.memory import AgentMemoryManager, MemoryError

    try:
        manager = AgentMemoryManager()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    action = args.agent_action
    try:
        if action == "list":
            return _agent_list(manager)
        elif action == "memory":
            return _agent_memory(manager, args.agent_id)
        elif action == "reset":
            return _agent_reset(manager, args.agent_id)
        elif action == "rotate":
            return _agent_rotate(manager, args.agent_id)
        else:
            print("Unknown agent action. Use: list, memory, reset, rotate", file=sys.stderr)
            return 1
    except MemoryError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _agent_list(manager) -> int:
    """List all registered agents."""
    agents = manager.list_agents()
    if not agents:
        print("No agents found.")
        print("Agents are created automatically when you run tasks.")
        return 0

    print(f"{'ID':<30} {'Thread':<12} {'Tasks':<8} {'Last Used'}")
    print("-" * 72)
    for agent in agents:
        thread_short = agent["thread_id"][:8] + "..."
        task_count = agent.get("task_count", 0)
        last_used = _format_time(agent.get("last_used_at"))
        print(f"{agent['id']:<30} {thread_short:<12} {task_count:<8} {last_used}")
    return 0


def _agent_memory(manager, agent_id: str) -> int:
    """Show memory summary for an agent."""
    agent = manager.get_agent(agent_id)
    if agent is None:
        print(f"Error: Agent not found: {agent_id}", file=sys.stderr)
        return 1

    summary = manager.get_memory_summary(agent_id)

    print(f"Agent: {agent_id}")
    print(f"Thread ID: {agent['thread_id']}")
    print(f"Project: {agent.get('project', 'N/A')}")
    print(f"Mode: {agent.get('mode', 'N/A')}")
    print(f"Tasks completed: {agent.get('task_count', 0)}")
    print(f"Created: {_format_time(agent.get('created_at'))}")
    print(f"Last used: {_format_time(agent.get('last_used_at'))}")
    print()
    print(f"Checkpoints: {summary.get('checkpoint_count', 0)}")
    print(f"Database: {'exists' if summary.get('db_exists') else 'not created'}")
    return 0


def _agent_reset(manager, agent_id: str) -> int:
    """Reset agent memory (clear checkpoints and assign new thread)."""
    result = manager.reset_memory(agent_id)
    print(f"Agent {agent_id} memory cleared.")
    print(f"New thread ID: {result['thread_id']}")
    return 0


def _agent_rotate(manager, agent_id: str) -> int:
    """Rotate agent thread ID (keeps old checkpoints)."""
    result = manager.rotate_thread(agent_id)
    print(f"Agent {agent_id} thread rotated.")
    print(f"New thread ID: {result['thread_id']}")
    return 0


def _format_time(ts) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "N/A"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "N/A"
