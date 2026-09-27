"""Minimal async adapter runner; results and raw outputs stay in memory."""
from copy import deepcopy
from time import perf_counter


async def run_episode(env, task_id, agent):
    """Run one episode with an agent exposing async act(observation, history).

    Each response is {"action": <tool action>, "raw_output": <model output>}.
    Malformed responses are recorded and passed as invalid actions to the gym.
    """
    observation, metadata = await env.reset(task_id)
    initial_observation = deepcopy(observation)
    trajectory = []
    latency_ms = 0.0

    while True:
        started = perf_counter()
        try:
            response = await agent.act(deepcopy(observation), deepcopy(trajectory))
        except Exception as exc:
            latency_ms += (perf_counter() - started) * 1000
            return {**metadata, "status": "agent_error", "success": False,
                    "reason": "agent_error", "error_type": type(exc).__name__,
                    "checks": None, "latency_ms": latency_ms,
                    "initial_observation": initial_observation,
                    "trajectory": trajectory}
        latency_ms += (perf_counter() - started) * 1000

        if isinstance(response, dict) and set(response) == {"action", "raw_output"}:
            action = response["action"]
            raw_output = response["raw_output"]
        else:
            action = None
            raw_output = response

        observation, reward, terminated, truncated, info = await env.step(action)
        trajectory.append({"action": deepcopy(action), "raw_output": raw_output,
                           "observation": deepcopy(observation), "reward": reward})
        if terminated or truncated:
            return {**metadata, "status": "completed", "success": info.get("success", False),
                    "reason": info.get("reason"), "checks": info.get("checks"),
                    "safety_violation": info.get("safety_violation", False),
                    "latency_ms": latency_ms,
                    "initial_observation": initial_observation,
                    "trajectory": trajectory}
